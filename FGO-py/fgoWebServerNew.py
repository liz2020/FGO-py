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
import fgoKernel
from fgoLogging import getLogger
from fgoTaskQueue import Task, task_queue, task_worker
from fgoQuestCatalog import get_catalog

logger = getLogger('WebNew')


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active.remove(websocket)

    async def broadcast(self, message: dict):
        data = json.dumps(message, ensure_ascii=False)
        for ws in self.active[:]:
            try:
                await ws.send_text(data)
            except Exception:
                self.active.remove(ws)


ws_manager = ConnectionManager()

# Bridge: task_queue broadcasts sync events → async WebSocket push
_loop: asyncio.AbstractEventLoop | None = None


def _on_task_event(event: dict):
    """Called from worker thread; schedules async broadcast."""
    loop = _loop
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(event), loop)


task_queue.subscribe(_on_task_event)


@app.on_event("startup")
async def _capture_loop():
    global _loop
    _loop = asyncio.get_running_loop()


# --- Pydantic models ---

class QuestItem(BaseModel):
    quest: list[int]
    count: int


class AddTaskRequest(BaseModel):
    type: str  # "operation" | "battle"
    params: dict = {}


class MoveRequest(BaseModel):
    position: int


# --- REST endpoints ---

@app.get("/")
async def root():
    html_path = static_path / "queue.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/queue")
async def get_queue():
    return {"queue": task_queue.list_all(), "running": task_queue.is_running}


@app.post("/api/queue")
async def add_task(req: AddTaskRequest):
    if req.type not in ("operation", "battle"):
        raise HTTPException(400, f"Unknown task type: {req.type}")
    task = Task(type=req.type, params=req.params)
    task_queue.add(task)
    return {"id": task.id, "task": task.to_dict()}


@app.delete("/api/queue/{task_id}")
async def remove_task(task_id: str):
    if not task_queue.remove(task_id):
        raise HTTPException(404, "Task not found in queue")
    return {"ok": True}


@app.post("/api/control/start")
async def control_start():
    task_queue.start()
    return {"ok": True, "running": True}


@app.post("/api/control/pause")
async def control_pause():
    task_queue.pause()
    return {"ok": True}


@app.post("/api/control/stop")
async def control_stop():
    task_queue.stop_current()
    return {"ok": True}


@app.post("/api/control/stop-all")
async def control_stop_all():
    task_queue.stop_all()
    return {"ok": True}


@app.get("/api/quests")
async def get_quests(lang: str = "zh"):
    return get_catalog(lang)


@app.post("/api/screenshot")
async def screenshot():
    if not fgoDevice.device.available:
        raise HTTPException(503, "Device not available")
    img = fgoKernel.Detect().im
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return {"image": base64.b64encode(buf.tobytes()).decode()}


# --- WebSocket ---

@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send current state on connect
        await websocket.send_json({
            "event": "connected",
            "queue": task_queue.list_all(),
            "running": task_queue.is_running,
        })
        # Keep alive — client can send pings
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# --- Entry point ---

def main(config=None, port: int = 15000):
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
