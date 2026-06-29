# 006 — Manual Mode Implementation Learnings

## 1. Blocking event loop starves WebSocket connections

**Assumption:** Calling `fgoDevice.device.screenshot()` synchronously in a FastAPI async endpoint is fine since it's fast (~5ms).

**Reality:** At 10 FPS, even 5ms of synchronous blocking per request adds up. When one client polls rapidly, a second client's WebSocket handshake can't complete because the event loop never yields long enough for the upgrade.

**Fix:** Move the entire screenshot capture + JPEG encode into `asyncio.to_thread()` so the event loop stays free for concurrent connections.

**Takeaway:** Any I/O or CPU work in an async endpoint that runs at high frequency must use `to_thread()`, even if individual calls seem fast. The issue is cumulative starvation, not single-call latency.

---

## 2. setInterval causes request pileup over slow networks

**Assumption:** `setInterval(takeScreenshot, 100)` gives us 10 FPS.

**Reality:** If a screenshot request takes >100ms (e.g., Tailscale latency, slow encode), the next interval fires before the previous one completes. Requests pile up, saturating the server and making WebSocket connections fail.

**Fix:** Replace `setInterval` with a self-scheduling loop: `setTimeout(takeScreenshot, 100)` at the end of each completed fetch. This guarantees non-overlapping requests.

**Takeaway:** Never use `setInterval` for network polling. Always wait for the previous request to complete before scheduling the next one.

---

## 3. innerHTML replacement causes black flash on mobile

**Assumption:** Setting `box.innerHTML = '<img src="...">'` each frame is fine for updating the screenshot.

**Reality:** On mobile Chrome, destroying and recreating the `<img>` element every frame causes a visible black flash between frames (the brief moment with no image element).

**Fix:** Create the `<img>` once and reuse it — only update `img.src` on each frame.

**Takeaway:** For high-frequency DOM updates, always mutate existing elements' properties rather than replacing innerHTML.

---

## 4. touch-action: none vs pointer events

**Assumption:** Setting `touch-action: none` might prevent pointer event detection.

**Reality:** `touch-action: none` only disables the browser's default touch gestures (scroll, zoom, navigation). Pointer events (`pointerdown`, `pointerup`, `pointermove`) still fire normally.

**Fix:** No fix needed — this is the correct approach. Use `touch-action: none` on interactive game preview areas to prevent page scrolling while keeping full pointer event support.

**Takeaway:** `touch-action` controls browser default behaviors, not JavaScript event delivery.

---

## 5. Mobile Chrome long-press triggers "save image" even with touch-action: none

**Assumption:** `touch-action: none` prevents all default touch behaviors including the long-press context menu.

**Reality:** Chrome's "save image" / context menu on long-press is separate from touch-action. It's triggered by the browser's image element detection, not by touch gesture handling.

**Fix:** Three layers: (1) `-webkit-touch-callout: none` on the `<img>`, (2) `pointer-events: none` on the `<img>` so touches hit the container instead, (3) `contextmenu` event listener with `preventDefault()`.

**Takeaway:** Mobile context menu suppression requires CSS (`-webkit-touch-callout`, `user-select`) + JS (`contextmenu` preventDefault). `touch-action` alone is insufficient.

---

## 6. Two sources of access logs: uvicorn + httpx

**Assumption:** Setting `access_log=False` on the FGO-py uvicorn server would silence all screenshot request logs.

**Reality:** The emu manager has its own uvicorn access log (for the proxy route) AND httpx logs each proxied request at INFO level. Both need silencing independently.

**Fix:** Set `access_log=False` on both uvicorn instances, and `logging.getLogger("httpx").setLevel(logging.WARNING)` in the emu entry point.

**Takeaway:** In a proxy architecture, request logging exists at every hop. Silence both the proxy server's access log and the HTTP client library's request log.

---

## 7. 30 FPS screenshot polling overwhelms the emulator

**Assumption:** LDOpenGL shared memory screenshots are essentially free, so 30 FPS should work.

**Reality:** While individual screenshots are fast, at 30 FPS the constant memory reads + JPEG encoding + network transfer created enough load to noticeably slow down the emulator.

**Fix:** Reduce to 10 FPS (100ms interval). This provides a smooth-enough interactive experience without impacting emulator performance.

**Takeaway:** Even "cheap" operations have a cost at high frequency. Start conservative (10 FPS) and only increase if users report lag in interactivity.
