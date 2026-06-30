# Implementation Learnings — 008 Task Types and Emu Decoupling

## 1. LDOpenGL handles crash when emulator stops

**Assumption:** FGO-py would gracefully handle the emulator stopping — the screenshot would just fail and the script would wait.

**Reality:** When the emulator process exits, LDOpenGL shared memory handles become invalid. Any subsequent screenshot call crashes the entire FGO-py process with an unrecoverable error (stale DLL handles).

**Fix:** In the `stop_emulator` task, proactively reset the device to a disconnected placeholder (`fgoDevice.device = fgoDevice.Device()`) *before* issuing the stop command. The `start_emulator` task then reconnects with `fgoDevice.Device(pending_name)` after verifying the emulator is responsive.

**Takeaway:** When controlling an external process's lifecycle, always invalidate your handles *before* the shutdown — don't wait for failure. Shared memory and DLL-based IPC is not resilient to process exit.

## 2. Device connection failure blocks web server startup

**Assumption:** `fgo.py web2 --device ldplayer:0` would start the web server regardless of emulator state, since the web server is useful even when the emulator is offline (e.g., to queue tasks).

**Reality:** `LDPlayerDevice.__init__` raises `RuntimeError` when the emulator isn't running (can't find the HWND). This happened before the FastAPI server started, so the entire process exited.

**Fix:** Wrapped device initialization in a try/catch in `fgo.py`. On failure, store the device name in `fgoDevice._pending_device_name` and set `fgoDevice.device = fgoDevice.Device()` (disconnected). The web server starts normally, and reconnection happens lazily when the emulator comes online.

**Takeaway:** Entry points that start long-running servers should never fail-fast on optional dependencies. Degrade gracefully and provide a reconnection path.

## 3. `.progress-text` CSS class has `position: absolute`

**Assumption:** Using the existing `.progress-text` class for standalone script-status text on the emu dashboard would render it inline.

**Reality:** In `emu/static/index.html`, `.progress-text` has `position: absolute` — designed to overlay inside `.progress-bar-wrap` (which is `position: relative`). Using it outside that context causes the text to float over unrelated elements.

**Fix:** Removed the class from standalone status text and used plain inline styles instead.

**Takeaway:** Always check the CSS definition of an existing class before reusing it in a new context. Absolute positioning classes are layout-container-dependent and silently break outside their intended parent.

## 4. `eat_apple` is not a standalone task

**Assumption:** "Eat apple" could be a queued task type that the user triggers independently to restore AP.

**Reality:** Apple consumption in FGO happens as part of the operation flow — when AP runs out after a battle, the game shows a prompt asking whether to use an apple. The kernel's `eatApple` method navigates this specific prompt. It's not something you do from the home screen.

**Fix:** Removed `eat_apple` from the task type dropdown and API. Apple configuration remains as a parameter on `operation` tasks (`apple_total`, `apple_kind`) where it belongs.

**Takeaway:** Before exposing a game action as a standalone task, verify whether it requires specific game UI state. If it only works in a particular context (mid-operation), it should be a parameter on the enclosing task, not an independent task type.

## 5. Emu manager buttons become non-clickable after script stops emulator

**Assumption:** Stopping and restarting the emulator from FGO-py would be transparent to the emu manager dashboard.

**Reality:** When the emulator stops, the emu manager's `instance_status` WebSocket message reports `STOPPED`. The dashboard's `renderCard()` was originally conditional — it hid the preview area and automation section entirely when the emulator was offline, making the FGO-py navigation button disappear.

**Fix:** Changed `renderCard()` to always render the preview area (with a dimmed "Emulator offline" placeholder) and automation section regardless of emulator state. Also added live-toggle logic: auto-unsubscribe from screenshots on stop, auto-subscribe on start.

**Takeaway:** When a script can control its own infrastructure (start/stop emulator), the management dashboard must handle all states gracefully. Never hide navigation to a running script just because its dependency is temporarily offline.

## 6. `time.sleep()` vs `schedule.sleep()` in non-task contexts

**Assumption:** Using `schedule.sleep()` everywhere would let all waits be cancellable.

**Reality:** `schedule.sleep()` checks stop/pause flags and raises `ScriptStop`. In `_wait_and_reconnect()` (polling for emulator startup), a `ScriptStop` would abort the reconnection loop prematurely — the emulator might be halfway through booting. The start_emulator task needs to complete successfully even if a cancel was requested during the wait.

**Fix:** Used raw `time.sleep()` in `_wait_and_reconnect()` for the polling loop, since this is infrastructure work that should always complete. The task boundary handles cancellation.

**Takeaway:** Infrastructure operations (reconnecting devices, waiting for external processes) should use raw sleep, not schedule-aware sleep. Only game automation loops should be interruptible via `schedule.sleep()`.

## 7. `finally` blocks overwrite progress on cancellation

**Assumption:** Putting `_report_progress(total, total, "done", "Complete")` in a `finally` block was safe — it would report completion whether the operation succeeded or was cancelled.

**Reality:** When a task is cancelled via `ScriptStop`, the `finally` block runs *before* the `except ScriptStop` handler. This overwrites `task.progress` with 100% completion, so the cancelled task's progress bar shows full instead of partial.

**Fix:** Moved the "done" progress report out of `finally` and into the success path (after `op()` returns normally). The `finally` block only handles cleanup (`normalAttackOnly = False`), not progress reporting.

**Takeaway:** `finally` blocks should only contain cleanup that is correct in all exit paths. Progress/status reporting is path-dependent — put "done" in the success path and "cancelled" in the exception handler.

## 8. WebSocket initial state event wipes client-side state

**Assumption:** Restoring `activeProgress` from `fetchState()` on page load would persist the progress bar across navigation.

**Reality:** The WebSocket `onopen` triggers the server to send a `state_updated` event with full state. The event handler set `activeProgress = null`, overwriting the progress that `fetchState()` had just restored — causing a flash-then-disappear.

**Fix:** Changed all event handlers (`state_updated`, `task_started`, `task_finished`) to read progress from `state.active.progress` instead of nulling `activeProgress`.

**Takeaway:** When a page has both REST initial load and WebSocket push, ensure the push handler doesn't blindly reset state that the REST call already populated. Extract state from the pushed payload rather than clearing it.
