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
from fgoTaskQueue import Task, task_queue, task_worker, run_auto_battle, is_auto_battle_active
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


@app.post("/api/queue")
async def add_task(req: AddTaskRequest):
    if req.type not in ("operation", "battle"):
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


@app.get("/api/quests")
async def get_quests(lang: str = "zh"):
    return get_catalog(lang)


@app.post("/api/screenshot")
async def screenshot():
    if not fgoDevice.device.available:
        raise HTTPException(503, "Device not available")
    # Use raw screenshot — bypass Detect() which calls schedule.sleep/checkStop
    img = fgoDevice.device.screenshot()
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return {"image": base64.b64encode(buf.tobytes()).decode()}


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
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
