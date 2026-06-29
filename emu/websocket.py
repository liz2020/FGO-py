"""WebSocket endpoints for live status updates and screenshot streaming."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from emu.ldplayer import LDPlayerBackend
from emu.models import InstanceStatus
from emu.registry import ScriptRegistry

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manage active WebSocket connections."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._screenshot_subscribers: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        # Remove from screenshot subscriptions
        for subs in self._screenshot_subscribers.values():
            if websocket in subs:
                subs.remove(websocket)

    def subscribe_screenshot(self, websocket: WebSocket, index: int):
        if index not in self._screenshot_subscribers:
            self._screenshot_subscribers[index] = []
        if websocket not in self._screenshot_subscribers[index]:
            self._screenshot_subscribers[index].append(websocket)

    def unsubscribe_screenshot(self, websocket: WebSocket, index: int):
        if index in self._screenshot_subscribers:
            subs = self._screenshot_subscribers[index]
            if websocket in subs:
                subs.remove(websocket)

    def screenshot_subscribers(self, index: int) -> list[WebSocket]:
        return self._screenshot_subscribers.get(index, [])

    async def broadcast(self, message: dict):
        """Send message to all connected clients."""
        data = json.dumps(message)
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


def setup_websocket_routes(
    app: FastAPI,
    get_backend: Callable[[], LDPlayerBackend],
    registry: ScriptRegistry,
) -> None:
    """Register WebSocket routes on the FastAPI app."""

    @app.websocket("/ws/status")
    async def websocket_status(websocket: WebSocket):
        """WebSocket endpoint for live instance status updates.

        Clients can send messages to subscribe/unsubscribe from screenshot streams:
          {"action": "subscribe_screenshot", "index": 2}
          {"action": "unsubscribe_screenshot", "index": 2}
        """
        await manager.connect(websocket)

        # Start background task for status polling
        status_task = asyncio.create_task(_poll_status(websocket, get_backend))
        screenshot_task = asyncio.create_task(_stream_screenshots(websocket, get_backend))

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    action = msg.get("action")
                    index = msg.get("index")

                    if action == "subscribe_screenshot" and index is not None:
                        manager.subscribe_screenshot(websocket, index)
                        await websocket.send_text(json.dumps({
                            "type": "subscribed",
                            "index": index,
                        }))
                    elif action == "unsubscribe_screenshot" and index is not None:
                        manager.unsubscribe_screenshot(websocket, index)
                        await websocket.send_text(json.dumps({
                            "type": "unsubscribed",
                            "index": index,
                        }))
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            status_task.cancel()
            screenshot_task.cancel()
            manager.disconnect(websocket)


async def _poll_status(
    websocket: WebSocket,
    get_backend: Callable[[], LDPlayerBackend],
    interval: float = 3.0,
):
    """Periodically poll instance status and push changes."""
    last_status: dict[int, str] = {}

    while True:
        try:
            backend = get_backend()
            instances = backend.list_instances()

            for inst in instances:
                prev = last_status.get(inst.index)
                current = inst.status.value
                if prev != current:
                    last_status[inst.index] = current
                    await manager.broadcast({
                        "type": "instance_status",
                        "index": inst.index,
                        "name": inst.name,
                        "status": current,
                        "pid": inst.pid,
                    })
        except Exception as e:
            logger.debug("Status poll error: %s", e)

        await asyncio.sleep(interval)


async def _stream_screenshots(
    websocket: WebSocket,
    get_backend: Callable[[], LDPlayerBackend],
    fps: float = 10.0,
):
    """Stream screenshots to subscribed clients at the configured FPS."""
    interval = 1.0 / fps

    while True:
        try:
            backend = get_backend()
            # Check all subscribed indices
            for index, subscribers in list(manager._screenshot_subscribers.items()):
                if not subscribers:
                    continue

                img = backend.screenshot(index)
                if img is None:
                    continue

                # Encode to JPEG
                import cv2
                success, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 50])
                if not success:
                    continue

                img_b64 = base64.b64encode(buffer.tobytes()).decode("ascii")
                message = json.dumps({
                    "type": "screenshot",
                    "index": index,
                    "data": img_b64,
                })

                # Send to subscribers
                disconnected = []
                for ws in subscribers:
                    try:
                        await ws.send_text(message)
                    except Exception:
                        disconnected.append(ws)
                for ws in disconnected:
                    manager.disconnect(ws)
        except Exception as e:
            logger.debug("Screenshot stream error: %s", e)

        await asyncio.sleep(interval)
