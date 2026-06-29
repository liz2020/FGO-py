# 006 — Manual Mode (Live Touch Passthrough)

## Summary

Add a **"Manual"** button to the left of the existing "⚔ Auto Battle" button in `queue.html`. When active, the user can tap and drag directly on the live preview image, and those gestures are translated into real emulator input. The preview frame rate is bumped to 30 FPS for a smooth interactive experience. Entering manual mode cancels any running auto-battle and active task.

---

## UI Layout

```
┌─────────────────────────────────────┐
│          [Game Screenshot]          │
└─────────────────────────────────────┘
  [🖐 Manual]  [⚔ Auto Battle]  [● Live]
```

The "🖐 Manual" button is a toggle, styled similarly to "● Live".

---

## Button States

| Condition | Button Appearance |
|-----------|------------------|
| Manual mode OFF | Border outline, dim text: `🖐 Manual` |
| Manual mode ON | Filled/highlighted: `🖐 Manual` (active class) |

When manual mode is ON:
- The "⚔ Auto Battle" button is **disabled**
- The task queue "Start" button is **disabled**
- The Live toggle is force-enabled (preview must be streaming)

When manual mode is OFF (clicked again):
- State returns to normal — Auto Battle and Start re-enabled
- Live toggle returns to user's previous preference (can stay on or off)
- Frame rate returns to default (2s interval)

---

## Behavior on Activation

1. **Cancel active task** — call `POST /api/control/cancel` to abort the running task via `schedule.stop()`
2. **Cancel auto-battle** — if `is_auto_battle_active()`, call `POST /api/control/auto-battle/cancel`
3. **Enable live preview at 30 FPS** — switch `liveInterval` from 2000ms to ~33ms
4. **Broadcast WS event** — `{"event": "manual_mode", "active": true}` so other connected clients see the state
5. **Register pointer/touch event handlers** on the screenshot `<img>` element

## Behavior on Deactivation

1. **Broadcast WS event** — `{"event": "manual_mode", "active": false}`
2. **Restore live interval** — revert to 2000ms (or stop live if it was off before)
3. **Remove pointer event handlers** from the screenshot image
4. No need to call `schedule.reset()` — that's the responsibility of the next task start

---

## Touch/Drag Translation

### Coordinate Mapping

The game runs at a fixed **1280×720** coordinate space. The preview `<img>` is rendered with `object-fit: contain` inside a 16:9 container. Translation:

```
game_x = (pointer_x_in_img / img_rendered_width)  * 1280
game_y = (pointer_y_in_img / img_rendered_height) * 720
```

Use `getBoundingClientRect()` on the `<img>` element to get its rendered position and size (accounting for letterboxing if any).

### Gesture Types

| User Gesture | Translated Action | Backend Call |
|---|---|---|
| Single tap (pointerdown + pointerup, < 200ms, < 10px movement) | `device.touch(pos)` | `POST /api/input/tap` `{x, y}` |
| Drag (pointerdown → pointermove → pointerup, ≥ 10px) | `device.swipe(begin, end, duration)` | `POST /api/input/swipe` `{x1, y1, x2, y2, duration}` |

Duration for swipe = elapsed time between pointerdown and pointerup (clamped 100–2000ms).

### Frontend Pointer Handling

```javascript
let pointerState = null;

screenshotImg.addEventListener('pointerdown', (e) => {
    pointerState = { x: e.offsetX, y: e.offsetY, time: Date.now() };
    e.preventDefault();
});

screenshotImg.addEventListener('pointerup', (e) => {
    if (!pointerState) return;
    const dx = e.offsetX - pointerState.x;
    const dy = e.offsetY - pointerState.y;
    const dt = Date.now() - pointerState.time;
    const dist = Math.sqrt(dx*dx + dy*dy);

    const [gx1, gy1] = imgToGame(pointerState.x, pointerState.y);
    if (dist < 10) {
        fetch(`${API}/api/input/tap`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({x: gx1, y: gy1})
        });
    } else {
        const [gx2, gy2] = imgToGame(e.offsetX, e.offsetY);
        const duration = Math.max(100, Math.min(2000, dt));
        fetch(`${API}/api/input/swipe`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({x1: gx1, y1: gy1, x2: gx2, y2: gy2, duration})
        });
    }
    pointerState = null;
});
```

### Backend Input Endpoints

```python
class TapRequest(BaseModel):
    x: int
    y: int

class SwipeRequest(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    duration: int = 300

@app.post("/api/input/tap")
async def input_tap(req: TapRequest):
    if not fgoDevice.device.available:
        raise HTTPException(503, "Device not available")
    # Run in thread to avoid blocking — touch uses time.sleep
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
```

Note: We call `device.I.touch` / `device.I.swipe` (the raw `LDPlayerDevice` methods) directly, bypassing the `Device` wrapper that uses `schedule.sleep()`. This avoids triggering stop/pause checks from manual input.

---

## Preview FPS Bump

### Current Behavior

Live mode polls `POST /api/screenshot` every 2000ms via `setInterval`.

### Manual Mode Behavior

Switch to ~100ms interval (10 FPS). This balances smoothness with emulator performance:
- Screenshots are taken via shared-memory LDOpenGL DLL — very fast (~2-5ms)
- JPEG encoding at quality 80 for 1280×720 is ~5-10ms
- Network is localhost

### Optimization: Binary WebSocket Streaming (Optional V2)

For V1, rapid polling is fine. V2 could add a dedicated binary WebSocket endpoint that pushes JPEG frames server-side, eliminating per-frame HTTP overhead.

---

## State Management

### New State Fields (Frontend)

```javascript
let manualMode = false;        // Whether manual mode is active
let preManualLiveState = null; // Was live toggle on before manual mode?
```

### WebSocket Events

| Event | Payload | Trigger |
|---|---|---|
| `manual_mode` | `{active: bool}` | Manual button toggled |

The frontend listens for `manual_mode` to sync state across multiple open browser tabs.

---

## Backend State

Manual mode is primarily a **frontend concern** — it just changes the frame rate and attaches pointer handlers. The backend only needs:

1. The new `/api/input/tap` and `/api/input/swipe` endpoints (stateless)
2. Optionally, a `manual_mode` flag on the server to block task starts while active (guards against race conditions from other clients)

### Optional Server-Side Guard

```python
_manual_mode = False

@app.post("/api/control/manual")
async def toggle_manual(active: bool):
    global _manual_mode
    if active:
        # Cancel running work
        if is_auto_battle_active():
            cancel_auto_battle()
        if task_queue.is_busy():
            task_queue.cancel()
    _manual_mode = active
    ws_manager.enqueue({"event": "manual_mode", "active": active})
    return {"ok": True}
```

The `/api/control/start` and `/api/control/auto-battle` endpoints would reject with 409 if `_manual_mode` is True.

---

## Suppress Screenshot Access Log

Uvicorn's default access logger prints a line for every `POST /api/screenshot 200`. At 30 FPS this floods the console. Fix: only log screenshot requests on **failure** (non-2xx).

### Approach

Add a custom ASGI middleware that suppresses uvicorn access log for the screenshot endpoint on success:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class SuppressScreenshotLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path == "/api/screenshot" and response.status_code < 400:
            # Attach a flag so the custom log filter skips this request
            response.headers["X-No-Access-Log"] = "1"
        return response
```

Alternatively (simpler): set `access_log=False` in `uvicorn.run()` and use FastAPI's own logging where needed:

```python
uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)
```

Then add explicit error-only logging in the screenshot endpoint:

```python
@app.post("/api/screenshot")
async def screenshot():
    if not fgoDevice.device.available:
        logger.warning("Screenshot request failed: device not available")
        raise HTTPException(503, "Device not available")
    img = fgoDevice.device.screenshot()
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return {"image": base64.b64encode(buf.tobytes()).decode()}
```

**Recommendation**: Use `access_log=False` — it's the simplest, and the only high-frequency endpoint is `/api/screenshot`. Other endpoints are infrequent enough that explicit logging isn't needed, or can be added selectively.

---

## Implementation Order

1. Backend: Disable uvicorn access log (`access_log=False`), add error-only logging for screenshot
2. Backend: Add `/api/input/tap` and `/api/input/swipe` endpoints
3. Backend: Add `/api/control/manual` toggle endpoint with cancel logic
4. Frontend: Add Manual button + CSS
5. Frontend: Pointer event handlers with coordinate translation
6. Frontend: FPS switch logic (33ms interval in manual mode)
7. Frontend: State sync via WS `manual_mode` event
8. Frontend: Disable Auto Battle / Start when manual mode is on

---

## Edge Cases

| Case | Handling |
|---|---|
| User taps preview while NOT in manual mode | Existing behavior: `takeScreenshot()` on click |
| Task finishes while entering manual mode | Cancel is idempotent — no error if nothing is active |
| Multiple browser clients | WS event syncs manual state; all clients enable/disable together |
| Device disconnects during manual mode | `/api/input/*` returns 503; frontend shows error toast |
| Screenshot fails in 30 FPS loop | Silently skip frame, retry next interval |
| Pointer leaves img mid-drag | Listen for `pointerup` on `document` as fallback |

---

## Open Questions

- Should there be a visual cursor/crosshair overlay on the preview to show where the user is touching?
  - Recommendation: Not for V1; add later if usability testing shows it's needed
- Should manual mode auto-disable after N minutes of inactivity?
  - Recommendation: No timeout for V1; user explicitly toggles off
