# Implementation Learnings: Web UI Task Queue (003)

Captures surprises, workarounds, and design pivots discovered while implementing `doc/design_logs/003-webui-task-queue.md`.

---

## 1. `schedule.sleep()` calls `checkStop()` — screenshot endpoint breaks after cancel

**Assumption:** `Detect()` is safe to call anytime for a screenshot.

**Reality:** `Detect()` → `DetectBase.__init__()` → `schedule.sleep(anteLatency)` → `checkStop()` → raises `ScriptStop` if `__stopMsg` is still set from a previous cancel.

**Fix:** Screenshot endpoint uses `device.screenshot()` directly, bypassing the schedule-aware `Detect()` class entirely.

**Takeaway:** Any code path that touches `schedule.sleep()` or `schedule.checkSuspend()` is affected by the global stop/pause flags. Non-task code (like API endpoints) must avoid these paths.

---

## 2. Thread→async broadcast: `call_soon_threadsafe`, never `run_coroutine_threadsafe` from async context

**Problem:** Worker thread needs to push WebSocket events to the async event loop. Initial attempt used `asyncio.run_coroutine_threadsafe()` — caused deadlock when called from within the same event loop thread (e.g., from an `await`-ed API handler).

**Fix:** Worker thread (non-async) uses `loop.call_soon_threadsafe(asyncio.ensure_future, coro)`. API handlers (already in the loop) `await` the broadcast directly.

**Takeaway:** Know which thread you're in. `run_coroutine_threadsafe` blocks the caller until the coroutine completes — if the caller IS the event loop thread, it's an instant deadlock.

---

## 3. UI toggle button must check semantic state, not just data presence

**Problem:** Cancel button and Start button are the same toggle. After cancel, `state.active` still holds the cancelled task (truthy). The toggle logic `if (state.active) → cancel` sent a cancel request when the user meant to start.

**Fix:** Check `state.active.status === 'active'` for the cancel path, not just truthiness.

**Takeaway:** When UI state objects can linger in multiple terminal states, always check the semantic status field, not just whether the object exists.

---

## 4. `schedule.pause()` is a toggle — double-click unpauses

**Reality:** `schedule.pause()` does `self.__pauseFlag = not self.__pauseFlag`. Clicking pause twice resumes execution silently.

**Fix:** Use `schedule.reset()` (called by Start) which explicitly sets `__pauseFlag = False` regardless of current state. This is idempotent and safe.

**Takeaway:** Toggle-style APIs are dangerous for distributed UIs where clicks may be duplicated or retried. Prefer explicit set/clear operations.

---

## 5. `LDPlayerDevice` must have `package` attribute for detection to work

**Problem:** `fgoDetect.setup(device)` checks `hasattr(device, 'package')`. Without it, `XDetect.region` stays empty, and `Detect()` falls through to `XDetectBase(*args)` which fails with `__init__() takes 1 positional argument but 3 were given`.

**Fix:** Added `_detect_fgo_package()` method to `LDPlayerDevice` that runs `ldconsole adb --command "pm list packages"` to find the FGO package.

**Takeaway:** When adding a new device implementation, check all callers of `setup()` to see what attributes they expect beyond the obvious `screenshot`/`press`/`swipe`.

---

## 6. Emu manager subprocess lifecycle — code changes require restart

**Problem:** The emu registry launches FGO-py as a subprocess with `stdout=DEVNULL`. Code changes on disk have no effect on running processes.

**Fix:** Must stop + start from the emu dashboard to pick up changes. No hot-reload.

**Takeaway:** Document the restart requirement. For development, kill the process manually and re-launch. Consider adding a `/api/restart` endpoint for dev convenience in the future.

---

## 7. Cancelled task stays in active slot — simpler than a separate cancelled list

**Initial design:** Cancelled tasks move to a separate "cancelled" list section in the UI.

**Revised design:** Cancelled task just stays in the active slot with a red "Cancelled" badge. Clicking Start clears it and picks the next pending task. No separate list needed.

**Takeaway:** Fewer UI sections = less cognitive load. The active slot already shows "what happened last" — reuse it for terminal states instead of creating new containers.
