"""Reverse proxy for automation script web UIs.

Routes /scripts/{name}/{index}/{path} to the script's internal localhost port.
This allows all access through a single externally-exposed port (15100).
"""

from __future__ import annotations

import asyncio
import html
import logging

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response

from emu.registry import ScriptRegistry

logger = logging.getLogger(__name__)


def _error_page(title: str, message: str, status_code: int) -> HTMLResponse:
    """Render a proxy error page with an explicit retry action."""
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title}</title>
    <style>
        body {{
            background: #1a1a2e;
            color: #eaeaea;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}
        main {{
            background: #0f3460;
            border-radius: 12px;
            max-width: 520px;
            padding: 28px;
            text-align: center;
        }}
        h2 {{ margin: 0 0 12px; }}
        p {{ color: #c8c8d0; line-height: 1.5; }}
        .actions {{
            display: flex;
            gap: 12px;
            justify-content: center;
            margin-top: 24px;
            flex-wrap: wrap;
        }}
        button, a {{
            border: 0;
            border-radius: 8px;
            cursor: pointer;
            display: inline-block;
            font-size: 0.95rem;
            font-weight: 600;
            padding: 12px 18px;
            text-decoration: none;
        }}
        button {{ background: #4ecca3; color: #1a1a2e; }}
        a {{ background: #16213e; color: #eaeaea; }}
    </style>
</head>
<body>
    <main>
        <h2>{safe_title}</h2>
        <p>{safe_message}</p>
        <div class="actions">
            <button type="button" onclick="window.location.reload()">Refresh</button>
            <a href="/">Back to dashboard</a>
        </div>
    </main>
</body>
</html>""",
        status_code=status_code,
    )


def setup_proxy_routes(app: FastAPI, registry: ScriptRegistry) -> None:
    """Register reverse proxy routes on the FastAPI app."""

    @app.websocket("/scripts/{script_name}/{index}/ws/{path:path}")
    async def proxy_script_ws(websocket: WebSocket, script_name: str, index: int, path: str):
        """Proxy WebSocket connections to automation script's internal server."""
        running = registry.get_running(script_name, index)
        if not running:
            await websocket.close(code=1013, reason="Script not running")
            return

        proc = running[0]
        target_url = f"ws://127.0.0.1:{proc.port}/ws/{path}"

        await websocket.accept()

        import websockets
        try:
            async with websockets.connect(target_url) as upstream:
                async def forward_to_client():
                    async for msg in upstream:
                        await websocket.send_text(msg)

                async def forward_to_upstream():
                    while True:
                        data = await websocket.receive_text()
                        await upstream.send(data)

                await asyncio.gather(forward_to_client(), forward_to_upstream())
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning("Proxy WS error for %s/%d: %s", script_name, index, e)

    @app.api_route(
        "/scripts/{script_name}/{index}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def proxy_script(script_name: str, index: int, path: str, request: Request):
        """Proxy requests to automation script's internal web server."""
        running = registry.get_running(script_name, index)
        if not running:
            return _error_page(
                "Script not running",
                f"{script_name} is not running for instance {index}.",
                status_code=503,
            )

        proc = running[0]
        target_url = f"http://127.0.0.1:{proc.port}/{path}"

        # Forward query string
        if request.url.query:
            target_url += f"?{request.url.query}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Forward the request
                body = await request.body()
                headers = dict(request.headers)
                # Remove host header to avoid conflicts
                headers.pop("host", None)
                # Add base path header so scripts can generate correct URLs
                headers["x-script-base"] = f"/scripts/{script_name}/{index}"

                resp = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=headers,
                    content=body,
                )

                # Filter out hop-by-hop headers
                excluded_headers = {"transfer-encoding", "connection", "keep-alive"}
                response_headers = {
                    k: v for k, v in resp.headers.items()
                    if k.lower() not in excluded_headers
                }

                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers=response_headers,
                    media_type=resp.headers.get("content-type"),
                )
        except httpx.ConnectError:
            return _error_page(
                "Connection failed",
                f"Cannot reach {script_name} on port {proc.port}. "
                "The script may still be starting up.",
                status_code=502,
            )
        except httpx.TimeoutException:
            return _error_page(
                "Timeout",
                f"Request to {script_name} timed out.",
                status_code=504,
            )

    # Redirect /scripts/{name}/{index} (no trailing slash) to /scripts/{name}/{index}/
    @app.get("/scripts/{script_name}/{index}")
    async def proxy_script_redirect(script_name: str, index: int):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/scripts/{script_name}/{index}/")
