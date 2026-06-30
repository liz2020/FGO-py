"""FastAPI web server with task queue and WebSocket status streaming."""
import asyncio
import base64
import cv2
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import fgoDevice
from fgoLogging import getLogger
from fgoTaskQueue import Task, task_queue, task_worker, run_auto_battle, is_auto_battle_active, cancel_auto_battle, configure_progress
from fgoQuestCatalog import get_catalog

logger = getLogger('WebNew')


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    # Start the task worker thread
    task_worker.start()
    logger.info("Task worker started")
    yield


app = FastAPI(title="FGO-py", lifespan=lifespan)

# Serve static files (CSS/JS/images if any)
static_path = Path(__file__).parent / "fgoWebUI"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# --- WebSocket connection manager ---

class ConnectionManager:
    def __init__(self):
        self.active: list[tuple[WebSocket, asyncio.Queue]] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        queue = asyncio.Queue()
        self.active.append((websocket, queue))
        return queue

    def disconnect(self, websocket: WebSocket):
        self.active = [(ws, q) for ws, q in self.active if ws is not websocket]

    def enqueue(self, message: dict):
        """Thread-safe: put message into all connected clients' queues."""
        for _, queue in self.active[:]:
            queue.put_nowait(message)


ws_manager = ConnectionManager()

# Bridge: task_queue broadcasts sync events → ConnectionManager queues
_loop: asyncio.AbstractEventLoop | None = None


def _on_task_event(event: dict):
    """Called from worker thread; thread-safely enqueues event for all WebSocket clients."""
    loop = _loop
    if loop and loop.is_running():
        loop.call_soon_threadsafe(ws_manager.enqueue, event)


task_queue.subscribe(_on_task_event)


# --- Pydantic models ---

class QuestItem(BaseModel):
    quest: list[int]
    count: int


class AddTaskRequest(BaseModel):
    type: str  # "operation" | "battle"
    params: dict = {}


class MoveRequest(BaseModel):
    position: int


class ReorderRequest(BaseModel):
    ids: list[str]


# --- REST endpoints ---

@app.get("/")
async def root():
    html_path = static_path / "queue.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/queue")
async def get_queue():
    return task_queue.get_state()


VALID_TASK_TYPES = ("operation", "battle", "wait", "stop_emulator", "start_emulator", "eat_apple")


@app.post("/api/queue")
async def add_task(req: AddTaskRequest):
    if req.type not in VALID_TASK_TYPES:
        raise HTTPException(400, f"Unknown task type: {req.type}")
    task = Task(type=req.type, params=req.params)
    task_queue.add(task)
    state = task_queue.get_state()
    ws_manager.enqueue({"event": "state_updated", "state": state})
    return {"id": task.id, "task": task.to_dict()}


@app.delete("/api/queue/{task_id}")
async def remove_task(task_id: str):
    if not task_queue.remove(task_id):
        raise HTTPException(404, "Task not found in queue")
    state = task_queue.get_state()
    ws_manager.enqueue({"event": "state_updated", "state": state})
    return {"ok": True}


@app.post("/api/control/start")
async def control_start():
    if _manual_mode:
        raise HTTPException(409, "Manual mode is active")
    task_queue.start()
    state = task_queue.get_state()
    ws_manager.enqueue({"event": "state_updated", "state": state})
    return {"ok": True}


@app.post("/api/control/cancel")
async def control_cancel():
    task_queue.cancel()
    return {"ok": True}


@app.post("/api/queue/reorder")
async def reorder_queue(req: ReorderRequest):
    if not task_queue.reorder(req.ids):
        raise HTTPException(400, "IDs don't match pending tasks")
    state = task_queue.get_state()
    ws_manager.enqueue({"event": "state_updated", "state": state})
    return {"ok": True}


@app.post("/api/control/auto-battle")
async def auto_battle():
    if _manual_mode:
        raise HTTPException(409, "Manual mode is active")
    if task_queue.is_busy():
        raise HTTPException(409, "A task is currently running")
    if is_auto_battle_active():
        raise HTTPException(409, "Auto battle already running")

    def _broadcast(event):
        loop = _loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(ws_manager.enqueue, event)

    if not run_auto_battle(_broadcast):
        raise HTTPException(409, "Cannot start auto battle")
    return {"ok": True}


@app.post("/api/control/auto-battle/cancel")
async def auto_battle_cancel():
    if not is_auto_battle_active():
        raise HTTPException(404, "No auto battle running")
    cancel_auto_battle()
    return {"ok": True}


@app.get("/api/control/auto-battle/status")
async def auto_battle_status():
    return {"active": is_auto_battle_active()}


# --- Manual mode ---

_manual_mode = False


class ManualModeRequest(BaseModel):
    active: bool


class TapRequest(BaseModel):
    x: int
    y: int


class SwipeRequest(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    duration: int = 300


class HoldRequest(BaseModel):
    x: int
    y: int
    duration: int = 1000


@app.post("/api/control/manual")
async def toggle_manual(req: ManualModeRequest):
    global _manual_mode
    if req.active:
        # Cancel running work before entering manual mode
        if is_auto_battle_active():
            cancel_auto_battle()
        if task_queue.is_busy():
            task_queue.cancel()
    _manual_mode = req.active
    ws_manager.enqueue({"event": "manual_mode", "active": req.active})
    return {"ok": True}


@app.post("/api/input/tap")
async def input_tap(req: TapRequest):
    if not fgoDevice.device.available:
        raise HTTPException(503, "Device not available")
    # Run in thread — touch uses time.sleep internally
    await asyncio.to_thread(fgoDevice.device.I.touch, (req.x, req.y))
    return {"ok": True}


@app.post("/api/input/swipe")
async def input_swipe(req: SwipeRequest):
    if not fgoDevice.device.available:
        raise HTTPException(503, "Device not available")
    await asyncio.to_thread(
        fgoDevice.device.I.swipe, (req.x1, req.y1), (req.x2, req.y2), req.duration
    )
    return {"ok": True}


@app.post("/api/input/hold")
async def input_hold(req: HoldRequest):
    if not fgoDevice.device.available:
        raise HTTPException(503, "Device not available")

    def _hold():
        import ctypes, time
        user32 = ctypes.windll.user32
        dev = fgoDevice.device.I
        x = int(req.x * dev._scale_x)
        y = int(req.y * dev._scale_y)
        lparam = x | (y << 16)
        user32.PostMessageW(dev._render_hwnd, dev.WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.01)
        user32.PostMessageW(dev._render_hwnd, dev.WM_LBUTTONDOWN, dev.MK_LBUTTON, lparam)
        time.sleep(req.duration / 1000.0)
        user32.PostMessageW(dev._render_hwnd, dev.WM_LBUTTONUP, 0, lparam)

    await asyncio.to_thread(_hold)
    return {"ok": True}


@app.get("/api/quests")
async def get_quests(lang: str = "zh"):
    return get_catalog(lang)


@app.post("/api/reconnect")
async def reconnect_device():
    """Try to reconnect to the device (e.g., after emulator starts)."""
    pending = getattr(fgoDevice, '_pending_device_name', None)
    if not pending:
        raise HTTPException(400, "No device name configured")
    try:
        fgoDevice.device = fgoDevice.Device(pending)
        return {"ok": True, "device": fgoDevice.device.name}
    except Exception as e:
        raise HTTPException(503, f"Device not available: {e}")


@app.post("/api/screenshot")
async def screenshot():
    if not fgoDevice.device.available:
        # Try to reconnect automatically if we know the device name
        pending = getattr(fgoDevice, '_pending_device_name', None)
        if pending:
            try:
                fgoDevice.device = fgoDevice.Device(pending)
            except Exception:
                pass
        if not fgoDevice.device.available:
            raise HTTPException(503, "Device not available")

    def _capture():
        img = fgoDevice.device.screenshot()
        _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(buf.tobytes()).decode()

    data = await asyncio.to_thread(_capture)
    return {"image": data}


# --- WebSocket ---

@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    queue = await ws_manager.connect(websocket)
    try:
        # Send current state on connect
        await websocket.send_json({
            "event": "state_updated",
            "state": task_queue.get_state(),
        })

        # Two concurrent tasks: read from queue (push to client) + keep-alive (read from client)
        async def _sender():
            while True:
                msg = await queue.get()
                await websocket.send_text(json.dumps(msg, ensure_ascii=False))

        async def _receiver():
            while True:
                await websocket.receive_text()

        await asyncio.gather(_sender(), _receiver())
    except (WebSocketDisconnect, Exception):
        ws_manager.disconnect(websocket)


# --- Entry point ---

def main(config=None, port: int = 15000):
    import uvicorn
    # Configure progress reporting to emu manager
    instance_index = 0
    try:
        if hasattr(fgoDevice, 'device') and fgoDevice.device:
            name = getattr(fgoDevice.device, 'name', '')
            if 'ldplayer:' in name:
                instance_index = int(name.split(':')[1])
    except Exception:
        pass
    configure_progress('http://127.0.0.1:15100', instance_index)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
