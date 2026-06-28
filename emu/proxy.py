"""Reverse proxy for automation script web UIs.

Routes /scripts/{name}/{index}/{path} to the script's internal localhost port.
This allows all access through a single externally-exposed port (15100).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from emu.registry import ScriptRegistry

logger = logging.getLogger(__name__)


def setup_proxy_routes(app: FastAPI, registry: ScriptRegistry) -> None:
    """Register reverse proxy routes on the FastAPI app."""

    @app.api_route(
        "/scripts/{script_name}/{index}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def proxy_script(script_name: str, index: int, path: str, request: Request):
        """Proxy requests to automation script's internal web server."""
        running = registry.get_running(script_name, index)
        if not running:
            return HTMLResponse(
                "<html><body><h2>Script not running</h2>"
                f"<p>{script_name} is not running for instance {index}.</p>"
                "<p><a href='/'>← Back to dashboard</a></p></body></html>",
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
            return HTMLResponse(
                "<html><body><h2>Connection failed</h2>"
                f"<p>Cannot reach {script_name} on port {proc.port}. "
                "The script may still be starting up.</p>"
                "<p><a href='/'>← Back to dashboard</a></p></body></html>",
                status_code=502,
            )
        except httpx.TimeoutException:
            return HTMLResponse(
                "<html><body><h2>Timeout</h2>"
                f"<p>Request to {script_name} timed out.</p></body></html>",
                status_code=504,
            )

    # Redirect /scripts/{name}/{index} (no trailing slash) to /scripts/{name}/{index}/
    @app.get("/scripts/{script_name}/{index}")
    async def proxy_script_redirect(script_name: str, index: int):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/scripts/{script_name}/{index}/")
