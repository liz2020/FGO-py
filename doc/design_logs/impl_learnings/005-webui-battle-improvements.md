# Implementation Learnings: Battle UI Improvements (005)

Captures surprises, workarounds, and design pivots discovered while implementing `doc/design_logs/005-webui-battle-improvements.md`.

---

## 1. `normalAttackOnly` must suppress both skills AND NP

**Assumption:** "仅使用普通攻击" (normal attack only) means just skip skills.

**Reality:** The Chinese term explicitly means "only use normal attacks" — both servant skills (`dispatchSkill`) and Noble Phantasms (NP/宝具) in `selectCard()` must be suppressed.

**Fix:** Two guard points:
- `dispatchSkill()`: early return when flag is set (skips all skill casting)
- `selectCard()`: force `hougu=[False,False,False]` so NP cards are never selected

**Takeaway:** NP selection in `Turn.selectCard()` is driven by the `hougu` list from `Detect.cache.isHouguReady()`. Zeroing it out is the cleanest suppression — the card evaluation logic still works correctly with only 5 normal cards.

---

## 2. Auto-battle loop doesn't self-terminate when not in battle

**Assumption:** `Battle()()` will quickly recognize it's not in battle and return.

**Reality:** The `Battle.__call__` loop (`while True`) checks multiple conditions each iteration (`isTurnBegin`, `isBattleFinished`, `isBattleDefeated`). If none match (e.g., user is on the main menu), it just keeps clicking `\xBB\x08` (tap dismiss) and looping forever.

**Fix:** The auto-battle button must be cancellable. Added `cancel_auto_battle()` which calls `schedule.stop()` — this sets the stop flag that `Detect()` checks via `schedule.sleep()` in its latency delays, causing `ScriptStop` to propagate up.

**Takeaway:** `Battle()` is designed to be called only after quest navigation places you on the battle screen. It has no "not in battle" detection — it assumes you're in one. Any standalone invocation needs a cancel escape hatch.

---

## 3. Preserving UI state across WebSocket-driven re-renders

**Assumption:** Re-rendering the queue list after state updates is fine.

**Reality:** WebSocket events trigger full `render()` calls which replace innerHTML. Any DOM state (expanded/collapsed details) is lost on every event — including after drag-and-drop reorder.

**Fix:** Maintain an `expandedIds` Set in JS that tracks which task IDs are expanded. On render, check this set to apply the correct CSS classes. `toggleExpand` updates both the DOM (immediate feedback) and the set (persistence).

**Takeaway:** For any UI state that isn't in the server model (expand/collapse, scroll position, input focus), keep a client-side mirror and reapply it in the render function. Don't rely on DOM persistence when innerHTML is replaced.

---

## 4. Auto-battle thread must use `schedule.reset()` before `Battle()`

**Assumption:** Just calling `Battle()()` in a new thread works.

**Reality:** If a previous task was cancelled, `schedule.__stopMsg` may still be set. The first `Detect()` call inside `Battle` would immediately raise `ScriptStop`.

**Fix:** Call `schedule.reset()` at the start of the auto-battle thread, before `Battle()()`, to clear any stale stop/pause flags.

**Takeaway:** Any code path that starts a fresh automation run (task queue, auto-battle, CLI) must call `schedule.reset()` first. This is the "arm" step before the "run" step.

---

## 5. HTML5 Drag and Drop: `dragover` must call `preventDefault()`

**Assumption:** Setting `draggable="true"` and handling `drop` is enough.

**Reality:** By default, dropping is not allowed on most elements. The `dragover` event must call `e.preventDefault()` to signal that the element accepts drops. Without this, the `drop` event never fires.

**Takeaway:** HTML5 DnD always needs at minimum: `dragstart` (set data), `dragover` (preventDefault), `drop` (handle). The `dragleave`/`dragend` handlers are for visual feedback cleanup only.
