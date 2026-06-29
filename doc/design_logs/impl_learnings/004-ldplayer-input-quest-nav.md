# Implementation Learnings: LDPlayer Input & Quest Navigation (004)

Captures surprises, workarounds, and design pivots discovered while fixing LDPlayer input and quest navigation issues.

---

## 1. `ldconsole adb` returns exit code 0 even on failure

**Assumption:** If `ldconsole adb --command "shell input tap X Y"` exits with code 0, the touch was delivered.

**Reality:** When ADB is not connected to the emulator instance, `ldconsole` still exits 0. The error `adb.exe: device 'emulator-5554' not found` appears in stdout, not stderr. All input silently fails.

**Fix:** Replaced ADB-based input entirely with Win32 `PostMessage` to the emulator's `RenderWindow` HWND.

**Takeaway:** Never trust exit codes from wrapper CLIs that shell out to sub-tools. Always verify the actual effect (e.g., take a screenshot before/after, or parse stdout for error strings).

---

## 2. Win32 PostMessage for emulator input — MaaFramework's approach

**Problem:** ADB was completely disconnected from LDPlayer, with all ports (5555-5558) refusing connections.

**Solution:** LDPlayer's render window accepts standard Win32 mouse messages (`WM_LBUTTONDOWN`, `WM_LBUTTONUP`, `WM_MOUSEMOVE`). The HWND is found by:
1. Enumerate top-level windows with class `LDPlayerMainFrame`
2. Match by PID from `ldconsole list2`
3. Find child window with class `RenderWindow`

Coordinate scaling: game coords (1280×720) → render window client area, using `GetClientRect` for the actual size.

**Takeaway:** When ADB is unreliable, Win32 message-based input is a robust alternative for Windows emulators. It's the same approach MaaFramework uses.

---

## 3. Quest tuple length mismatch — web catalog vs kernel expectations

**Assumption:** The quest tuples from `reishift.place` keys (3-element) are sufficient for navigation.

**Reality:** `reishift(quest)` iterates `range(1, len(quest))` to call navigation steps. A 3-element tuple `(1,0,0)` only calls 2 prefixes: `place[(1,)]` and `place[(1,0)]`. A 4-element tuple `(1,0,0,0)` calls 3 prefixes including `place[(1,0,0)]` — the Map navigation step.

**Fix:** Changed `fgoQuestCatalog.py` to source tuples from `fgoMetadata.quest` (4-element, matching the old Qt GUI) instead of deriving from `reishift.place` keys.

**Takeaway:** When building a new frontend for an existing backend, match the data format of the original frontend exactly. The tuple length IS the control flow — it determines how many navigation steps execute.

---

## 4. Part-level navigation nodes may not exist based on account progress

**Assumption:** The quest list always shows a "Part 1" group header (`1.png`) that must be tapped before individual chapters appear.

**Reality:** The Part 1 group header only appears once ALL chapters (1-0 through 1-7) are completed. On incomplete accounts, chapters are listed individually without a group banner.

**Fix:** Mark part-level `List` nodes as `optional=True`. Before scrolling, check if child chapters are already visible (ungrouped state) and skip immediately. Fallback: `MAX_SCROLLS=20` prevents infinite loops.

**Takeaway:** Game UIs often change layout based on player progression. Navigation code must handle multiple possible screen states, not just the "end-game" state the developer tested with.

---

## 5. FastAPI `lifespan` makes `@app.on_event("startup")` dead code

**Assumption:** Both `lifespan` and `on_event("startup")` handlers run at startup.

**Reality:** When `app = FastAPI(lifespan=lifespan)` is used, all `@app.on_event("startup")` and `@app.on_event("shutdown")` handlers are **silently ignored**. They never execute.

**Symptom:** `_loop` (the asyncio event loop reference) was never captured, so `_on_task_event` checked `if loop and loop.is_running()` → always False → all worker thread events were silently dropped → WebSocket clients never received task completion updates.

**Fix:** Moved `_loop = asyncio.get_running_loop()` into the `lifespan` async context manager.

**Takeaway:** FastAPI's `lifespan` and `on_event` are mutually exclusive — pick one. If you use `lifespan`, put ALL startup/shutdown logic there. This is documented in FastAPI's migration guide but easy to miss when refactoring incrementally.

---

## 6. Thread→async bridge: use asyncio.Queue per connection, not fire-and-forget coroutines

**Problem:** `loop.call_soon_threadsafe(asyncio.ensure_future, ws_manager.broadcast(event))` creates fire-and-forget tasks. If sending fails (connection dropped, loop busy), the error is lost and events disappear silently.

**Fix:** Each WebSocket connection gets a dedicated `asyncio.Queue`. The worker thread enqueues via `call_soon_threadsafe(ws_manager.enqueue, event)`. A sender task per connection `await`s the queue and sends. Delivery is guaranteed in-order and failures are per-connection.

**Takeaway:** For thread→async WebSocket push, prefer a queue-per-connection pattern over broadcast coroutines. It's more debuggable, naturally handles backpressure, and makes connection lifecycle explicit.
