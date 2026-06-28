"""FastAPI web service for the Emulator Manager.

Provides REST endpoints for:
- Instance lifecycle management (list, launch, stop, restart)
- App management (list, launch, kill)
- Screenshot capture
- ADB info
- Script management (list, start, stop)
- System status
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from emu.ldplayer import LDPlayerBackend
from emu.models import AutomationScript, InstanceStatus
from emu.proxy import setup_proxy_routes
from emu.registry import ScriptRegistry
from emu.websocket import setup_websocket_routes

logger = logging.getLogger(__name__)

# Global state
backend: LDPlayerBackend | None = None
registry: ScriptRegistry = ScriptRegistry()


def get_backend() -> LDPlayerBackend:
    if backend is None:
        raise HTTPException(status_code=503, detail="No emulator backend available")
    return backend


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize backend on startup."""
    global backend
    backend = LDPlayerBackend()
    info = backend.detect()
    if info:
        logger.info("Detected emulator: %s at %s", info.name, info.install_dir)
    else:
        logger.warning("No LDPlayer installation detected")

    # Register default scripts (configurable later)
    _register_default_scripts()

    yield

    # Cleanup: stop all script processes
    for proc in registry.all_processes():
        if proc.status == "running":
            registry.stop(proc.script_name, proc.instance_index)


def _register_default_scripts():
    """Register built-in automation scripts."""
    registry.register(AutomationScript(
        name="fgo",
        display_name="FGO-py",
        package_filter="com.aniplex.fategrandorder*",
        base_port=15001,
        start_command="uv run python FGO-py/fgo.py web --device ldplayer:{index} --port {port}",
    ))


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Emulator Manager",
        version="1.0.0",
        lifespan=lifespan,
    )

    # --- Instance endpoints ---

    @app.get("/api/instances")
    async def list_instances():
        b = get_backend()
        instances = b.list_instances()
        return [
            {
                "index": inst.index,
                "name": inst.name,
                "status": inst.status.value,
                "pid": inst.pid,
                "adb_serial": inst.adb_serial,
                "emulator_brand": inst.emulator_brand,
            }
            for inst in instances
        ]

    @app.get("/api/instances/{index}")
    async def get_instance(index: int):
        b = get_backend()
        inst = b.get_instance(index)
        if inst is None:
            raise HTTPException(status_code=404, detail=f"Instance {index} not found")
        return {
            "index": inst.index,
            "name": inst.name,
            "status": inst.status.value,
            "pid": inst.pid,
            "adb_serial": inst.adb_serial,
            "emulator_brand": inst.emulator_brand,
        }

    @app.post("/api/instances/{index}/launch")
    async def launch_instance(index: int):
        b = get_backend()
        success = b.launch(index)
        return {"success": success, "message": f"Instance {index} launch requested"}

    @app.post("/api/instances/{index}/stop")
    async def stop_instance(index: int):
        b = get_backend()
        success = b.stop(index)
        return {"success": success, "message": f"Instance {index} stop requested"}

    @app.post("/api/instances/{index}/restart")
    async def restart_instance(index: int):
        b = get_backend()
        success = b.restart(index)
        return {"success": success, "message": f"Instance {index} restart requested"}

    # --- App endpoints ---

    @app.get("/api/instances/{index}/apps")
    async def list_apps(index: int):
        b = get_backend()
        apps = b.list_apps(index)
        return {"apps": apps}

    @app.post("/api/instances/{index}/apps/launch")
    async def launch_app(index: int, request: Request):
        body = await request.json()
        package_name = body.get("package_name")
        if not package_name:
            raise HTTPException(status_code=400, detail="package_name required")
        b = get_backend()
        success = b.launch_app(index, package_name)
        return {"success": success}

    @app.post("/api/instances/{index}/apps/kill")
    async def kill_app(index: int, request: Request):
        body = await request.json()
        package_name = body.get("package_name")
        if not package_name:
            raise HTTPException(status_code=400, detail="package_name required")
        b = get_backend()
        success = b.kill_app(index, package_name)
        return {"success": success}

    # --- Screenshot ---

    @app.get("/api/instances/{index}/screenshot")
    async def screenshot(index: int):
        b = get_backend()
        img = b.screenshot(index)
        if img is None:
            raise HTTPException(status_code=503, detail="Screenshot unavailable")
        # Encode to JPEG for efficiency
        import cv2
        success, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
        if not success:
            raise HTTPException(status_code=500, detail="Failed to encode screenshot")
        return Response(content=buffer.tobytes(), media_type="image/jpeg")

    # --- ADB ---

    @app.get("/api/instances/{index}/adb")
    async def get_adb(index: int):
        b = get_backend()
        serial = b.adb_serial(index)
        return {"serial": serial}

    @app.post("/api/instances/{index}/adb/connect")
    async def connect_adb(index: int):
        b = get_backend()
        serial = b.adb_serial(index)
        # Attempt ADB connect
        import subprocess
        result = subprocess.run(
            ["adb", "connect", serial],
            capture_output=True, text=True, timeout=10,
        )
        return {"serial": serial, "output": result.stdout.strip()}

    # --- Scripts ---

    @app.get("/api/scripts")
    async def list_scripts():
        scripts = registry.scripts
        result = []
        for s in scripts:
            running = registry.get_running(s.name)
            result.append({
                "name": s.name,
                "display_name": s.display_name,
                "package_filter": s.package_filter,
                "running_instances": [
                    {"index": p.instance_index, "port": p.port, "pid": p.pid}
                    for p in running
                ],
            })
        return result

    @app.post("/api/scripts/{name}/start")
    async def start_script(name: str, request: Request):
        body = await request.json()
        instance_index = body.get("instance_index")
        if instance_index is None:
            raise HTTPException(status_code=400, detail="instance_index required")

        script = registry.get_script(name)
        if script is None:
            raise HTTPException(status_code=404, detail=f"Script '{name}' not found")

        b = get_backend()
        serial = b.adb_serial(instance_index)

        # Use the repo root as cwd for script processes
        import pathlib
        repo_root = pathlib.Path(__file__).parent.parent
        proc = registry.start(name, instance_index, serial, cwd=repo_root)
        return {
            "status": proc.status,
            "pid": proc.pid,
            "port": proc.port,
            "instance_index": proc.instance_index,
        }

    @app.post("/api/scripts/{name}/stop")
    async def stop_script(name: str, request: Request):
        body = await request.json()
        instance_index = body.get("instance_index")
        if instance_index is None:
            raise HTTPException(status_code=400, detail="instance_index required")

        success = registry.stop(name, instance_index)
        return {"success": success}

    # --- System ---

    @app.get("/api/status")
    async def system_status():
        b = get_backend()
        info = b.detect()
        return {
            "service": "emulator-manager",
            "version": "1.0.0",
            "emulator": {
                "detected": info is not None,
                "name": info.name if info else None,
                "brand": info.brand if info else None,
                "install_dir": str(info.install_dir) if info else None,
            },
        }

    @app.get("/api/emulators")
    async def list_emulators():
        b = get_backend()
        info = b.detect()
        if info is None:
            return []
        return [{
            "name": info.name,
            "brand": info.brand,
            "version": info.version,
            "install_dir": str(info.install_dir),
        }]

    # --- Setup additional routes ---
    setup_proxy_routes(app, registry)
    setup_websocket_routes(app, get_backend, registry)

    # --- Static files (web UI) ---
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
