"""Entry point for the Emulator Manager service.

Usage:
    uv run python -m emu
    uv run python -m emu --port 15100 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser


def _manager_is_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/status", timeout=1) as response:
            return json.load(response).get("service") == "emulator-manager"
    except (urllib.error.URLError, TimeoutError):
        return False
    except (ValueError, AttributeError):
        logging.getLogger(__name__).error("Unexpected response from %s/api/status", url)
        return False


def _open_browser_when_ready(url: str, stopped: threading.Event, timeout_s: float = 120):
    logger = logging.getLogger(__name__)
    deadline = time.monotonic() + timeout_s
    while not stopped.is_set() and time.monotonic() < deadline:
        if _manager_is_ready(url):
            if not stopped.is_set() and not webbrowser.open(url):
                logger.warning("Could not open a browser. Open %s manually", url)
            return
        stopped.wait(.25)
    if not stopped.is_set():
        logger.error("Manager did not become ready within %ss. Open %s manually after startup", timeout_s, url)


def main():
    parser = argparse.ArgumentParser(description="Emulator Manager Service")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=15100, help="Bind port (default: 15100)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--open-browser", action="store_true", help="Open the dashboard after the service is ready")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Suppress noisy httpx request logs (e.g. repeated screenshot proxy calls)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    stopped = threading.Event()
    if args.open_browser:
        host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        if ":" in host:
            host = f"[{host}]"
        url = f"http://{host}:{args.port}"
        if _manager_is_ready(url):
            logger = logging.getLogger(__name__)
            logger.info("Manager already running at %s; opening dashboard only", url)
            if not webbrowser.open(url):
                logger.error("Could not open a browser. Open %s manually", url)
                raise SystemExit(1)
            return
        threading.Thread(
            target=_open_browser_when_ready, args=(url, stopped),
            daemon=True, name="OpenDashboard",
        ).start()
    import uvicorn
    try:
        uvicorn.run(
            "emu.service:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
            access_log=False,
        )
    finally:
        stopped.set()


if __name__ == "__main__":
    main()
