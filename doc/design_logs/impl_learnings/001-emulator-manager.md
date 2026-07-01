# Implementation Learnings: Emulator Manager (001)

Captures surprises, workarounds, and design pivots discovered while implementing `doc/design_logs/001-emulator-manager.md`.

---

## 1. LDPlayer 14 Screenshot Capture — DLL vtable, not shared memory

**PRD assumption:** Shared memory (`ldopengl0`, `ldopengl1`, …) for zero-copy screenshot.

**Reality:** LDPlayer 14 does **not** expose shared memory segments for screenshots. The actual mechanism is a C++ vtable interface in `ldopengl64.dll`, discovered via [MaaXYZ/EmulatorExtras](https://github.com/MaaXYZ/EmulatorExtras) (`LD/dnopengl/dnopengl.h`).

**How it works:**
```
CreateScreenShotInstance(playeridx, playerpid) → IScreenShotClass*
vtable[0] = destructor
vtable[1] = cap()   → void*   (raw BGR pixel data, bottom-up)
vtable[2] = release() → void
```

- Must pass the **PID** (from `list2`) and know **width×height** to interpret the buffer.
- Pixel data is BGR, bottom-up — needs `np.flip(arr, axis=0)` and BGR→RGB conversion.
- Factory function uses `__cdecl` calling convention (not stdcall).

**Takeaway:** Always prototype screenshot capture against the real emulator early. Documentation from the emulator vendor is nonexistent; open-source automation frameworks are the best reference.

---

## 2. LDPlayer 14 `list2` extended format

**PRD assumption:** `list2` returns `index,name,top_hwnd,bind_hwnd,is_running,pid,vbox_pid` (7 fields).

**Reality:** LDPlayer 14 outputs **10 fields**: `index,name,top_hwnd,bind_hwnd,is_running,pid,vbox_pid,width,height,dpi`.

- The `is_running` field is not strictly `0`/`1` — non-zero means running.
- Instance names can contain Chinese characters, causing `UnicodeEncodeError` on `cp1252` consoles.

**Takeaway:** Parse defensively (handle variable field counts). Width/height from `list2` are needed for the screenshot buffer size.

---

## 3. LDPlayer 14 ADB ports are not exposed by default

**PRD assumption:** Each instance has a predictable ADB port (`5555 + 2*index`).

**Reality:** LDPlayer 14 does not open ADB ports by default. Port scans across 5553–5600 and common ranges found nothing.

**Workaround:** Use `ldconsole adb --index N --command "shell input tap X Y"` for touch/swipe input instead of connecting via ADB directly. This avoids the ADB dependency entirely.

**Takeaway:** Don't assume ADB connectivity. The `ldconsole adb` passthrough is reliable and doesn't require port mapping.

---

## 4. Python module entry point: `python -m emu.service` vs `python -m emu`

**Issue:** `python -m emu.service` runs `service.py` as a script (it doesn't find `__main__.py`). The correct invocation is `python -m emu`, which triggers `emu/__main__.py`.

**Takeaway:** Document the run command clearly. If the package has a `__main__.py`, always use `python -m <package>`.

---

## 5. Reverse proxy URL rewriting

**Problem:** FGO-py web UI uses absolute API paths (`/api/teamup/load`). When accessed via the emu manager proxy at `/scripts/fgo/0/`, absolute paths bypass the proxy and hit the emu manager's own API.

**Solution:**
- Changed all fetch URLs to **relative** (no leading `/`): `api/teamup/load`
- From `/scripts/fgo/0/index`, relative `api/teamup/load` resolves to `/scripts/fgo/0/api/teamup/load` → proxy forwards correctly.
- From `/index` (direct access), relative `api/teamup/load` resolves to `/api/teamup/load` → hits FGO-py directly.
- Flask redirect `redirect('/index')` → `redirect('index')` to preserve the proxy base path.

**Also:** The "← Manager" back-link is only shown when the `X-Script-Base` request header is present (set by the proxy), avoiding a confusing link when accessing FGO-py directly.

**Takeaway:** When building apps that may be served behind a reverse proxy, use relative URLs from the start. Proxy-awareness headers (`X-Script-Base`, `X-Forwarded-Prefix`) let the app adapt.

---

## 6. Subprocess pipe buffer deadlock

**Problem:** `subprocess.Popen(..., stdout=PIPE, stderr=PIPE)` for spawned scripts can deadlock if the child writes enough output to fill the OS pipe buffer (~64KB) and nobody reads it.

**Solution:** Use `subprocess.DEVNULL` for fire-and-forget script processes. If logging is needed later, redirect to log files instead.

**Takeaway:** Only use `PIPE` if you actively consume the output (e.g., `communicate()`). For long-lived child processes, use `DEVNULL` or file handles.

---

## 7. `.git/HEAD` is unreliable in worktrees

**Problem:** `fgo.py` reads `../.git/HEAD` to determine the current branch. In a git worktree, `.git` is a file (not a directory) pointing to the main checkout's `.git` directory, and the relative path `../.git/HEAD` may not exist.

**Solution:** Wrapped in `try/except` — branch detection is non-critical and gracefully degrades.

**Takeaway:** Avoid relying on `.git` directory structure directly. Use `git rev-parse` or `gitpython` for robust branch detection.

---

## 8. PySide6 removal was straightforward

Removing the PySide6 GUI (6 files) and switching the default entry point from `gui` to `web` had no ripple effects. The web UI (Flask + vanilla JS) is a complete replacement for the desktop GUI, and the mobile-responsive dark theme works well on both phone and desktop.

**Takeaway:** Decoupled UI layers make migration painless. The web-first approach is better for the remote-access use case (Tailscale from phone).

---

## 9. Keep the service network-agnostic

**PRD assumption:** Show Tailscale hostname/IP in the dashboard for easy access.

**Decision:** Removed. The emu manager shouldn't care whether the user connects via Tailscale, local browser, or any other network. Embedding Tailscale awareness creates an unnecessary coupling — it's the user's responsibility to know how to reach their own PC.

**Takeaway:** Infrastructure services should be network-layer agnostic. Let the network stack (DNS, Tailscale MagicDNS, mDNS) handle discoverability — don't bake assumptions about the transport into the application layer.

---

## 10. Relative URLs are essential for reverse-proxy compatibility

**Problem:** FGO-py's web UI used absolute paths (`/api/teamup/load`). When served behind the emu manager's reverse proxy at `/scripts/fgo/0/`, these absolute paths bypass the proxy and hit the wrong server.

**Solution:**
- All fetch calls use relative URLs without leading `/` (e.g., `api/teamup/load`)
- From `/scripts/fgo/0/index` (proxy), resolves to `/scripts/fgo/0/api/teamup/load` → forwarded correctly
- From `/index` (direct), resolves to `/api/teamup/load` → hits FGO-py directly
- Flask redirect uses `redirect('index')` not `redirect('/index')` to preserve base path

**Takeaway:** Always use relative URLs in apps that may be reverse-proxied. This eliminates the need for URL rewriting in the proxy layer.

---

## 11. Subprocess PIPE buffers cause silent hangs

**Problem:** Spawning FGO-py with `stdout=PIPE, stderr=PIPE` caused the process to hang silently when nobody consumed the output and the OS pipe buffer (~64KB) filled up.

**Solution:** Use `subprocess.DEVNULL` for fire-and-forget child processes. Only use `PIPE` if you actively call `communicate()` or read from the pipe in a thread.

**Takeaway:** For long-lived child processes, always use `DEVNULL` or redirect to log files. `PIPE` is only safe with active consumers.

---

## 12. Progress reporting via HTTP callback is simple and decoupled

**Design:** FGO-py POSTs `{current, total, status, detail}` to `http://127.0.0.1:15100/api/scripts/fgo/progress` every few seconds during farming.

**Why not WebSocket?** Flask doesn't natively support WebSocket. Adding flask-socketio or switching to async would be over-engineering for a simple progress counter. HTTP POST is fire-and-forget, works with `urllib.request` (no deps), and the emu manager just stores the latest value in memory.

**Takeaway:** Don't reach for WebSocket when a simple POST-to-poll pattern suffices. The dashboard polls progress on its own refresh cycle anyway.

---

## 13. Uvicorn's `websockets` legacy protocol is not safe for concurrent writes

**Assumption:** Multiple background coroutines could all `await websocket.send_text(...)` on the same connection concurrently — the transport would serialize them.

**Reality:** Every `/ws/status` connection had four independent producers writing to the same socket: `_poll_status` (broadcasts `instance_status` on state change), `_stream_screenshots` (JPEG frames at 10 fps), the receive-loop that acks `subscribed`/`unsubscribed`, and HTTP handlers calling `manager.broadcast(...)`. When two of them landed in the underlying `websockets.legacy.protocol.drain_helper` at the same time, they collided on the drain waiter and blew up:

```
File "...websockets/legacy/protocol.py", line 308, in _drain_helper
    assert waiter is None or waiter.cancelled()
AssertionError
```

The traceback surfaced through `keepalive_ping` because that's the coroutine that happened to lose the race — but the actual second writer was always one of the app-level producers. The connection was then dropped and the client had to reconnect.

**Fix:** Serialize sends per connection with an `asyncio.Lock`. `ConnectionManager` now keeps a `dict[id(websocket), asyncio.Lock]`, populated on `connect` and cleared on `disconnect`, and exposes an `async def send_text(ws, data)` that acquires the lock around the actual `ws.send_text(...)`. Every producer (`broadcast`, receive-loop acks, screenshot stream) goes through it. The lock is per-connection so different clients don't block each other, and the screenshot fan-out still runs concurrently across sockets.

**Takeaway:** Under uvicorn's default `ws=websockets` (legacy protocol), treat a `WebSocket` as a **single-writer resource** and gate every send through a per-connection lock as soon as you have more than one coroutine that might send. Alternatives — switching uvicorn to `ws=wsproto`, or funneling all sends through a per-connection `asyncio.Queue` with one dedicated sender task — work too, but a lock is the smallest change.
