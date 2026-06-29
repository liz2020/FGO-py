# 005 — Web UI Battle Improvements

## Summary

Three related improvements to the queue.html Web UI:

1. **"仅使用普通攻击" (Normal Attack Only) checkbox** in the Add Quest section
2. **Expandable + drag-reorderable queue items**
3. **"Auto Battle" one-off button** below live preview

---

## 1. Normal Attack Only Checkbox

### Location

Add Quest section, below the Repeat input and above the "+ Add to Queue" button.

### Behavior

- Checkbox label: `仅使用普通攻击`
- When checked, the task params include `"normal_attack_only": true`
- Backend sets `Turn.normalAttackOnly = True` before running the battle, resets it after
- Affects the smart `Turn.dispatchSkill()` — skips all servant skill casting
- Affects `Turn.selectCard()` — forces all NP (Noble Phantasm/宝具) to be suppressed, only normal command cards are selected

### UI Wireframe

```
┌─ Add Quest ──────────────────────┐
│ Part     [Select ▾]              │
│ Chapter  [Select ▾]              │
│ Quest    [Select ▾]              │
│ Repeat   [1  ]                   │
│ ☐ 仅使用普通攻击                  │
│ [        + Add to Queue        ] │
└──────────────────────────────────┘
```

### Frontend Changes (`queue.html`)

- Add checkbox input with id `chkNoSkill` before the Add button
- In `addToQueue()`, read checked state and include `normal_attack_only: true` in `params`

### Backend Changes

- `fgoTaskQueue.py` `_execute()`: read `task.params.get("normal_attack_only", False)`, set `Turn.normalAttackOnly` before battle, reset after
- Ensure reset in a `finally` block to avoid stale state on error

---

## 2. Expandable + Drag-Reorderable Queue Items

### Current State

Queue items are flat `<li>` elements showing a one-line description + remove button.

### Design

**Expandable:**
- Each queue item has a chevron or tap target
- Expanding shows task details: quest name, count, no_skill flag, apple settings
- Collapsed by default to keep the list compact

**Drag-Reorderable:**
- Each item gets a drag handle (≡ icon) on the left
- Use HTML5 Drag & Drop API (no external library, keeping it self-contained)
- On drop, call a new `POST /api/queue/reorder` endpoint with the new ID order
- Backend `TaskQueue` gets a `reorder(ids: list[str])` method that reorders pending items

### UI Wireframe

```
┌─ Queue ──────────────────────────┐
│ ≡  冬木 - 未確認座標X ×3   ▾  🗑 │
│ ┌──────────────────────────────┐ │  ← expanded
│ │ Quest: 1-0-0-0              │ │
│ │ Count: 3                    │ │
│ │ Normal attack only: Yes     │ │
│ └──────────────────────────────┘ │
│ ≡  カメロット ×1          ▸  🗑 │  ← collapsed
└──────────────────────────────────┘
```

### Backend Changes

- New endpoint: `POST /api/queue/reorder` — body: `{"ids": ["id1", "id2", ...]}`
- `TaskQueue.reorder(ids)`: validate all IDs exist in pending, reorder to match

---

## 3. Auto Battle Button

### Purpose

A one-off action for when the user is already in a battle screen (e.g., manually entered a quest or resumed from a crash). Picks up the current battle and runs it to completion using the smart `Turn` logic.

### Location

Screenshot controls bar, to the left of the "● Live" toggle button.

### UI Wireframe

```
┌─────────────────────────────────────┐
│          [Game Screenshot]          │
└─────────────────────────────────────┘
         [⚔ Auto Battle]  [● Live]
```

### Behavior

| Condition | Button State |
|-----------|--------------|
| No active task, queue idle | **Enabled** (clickable) |
| Active task is cancelled/error | **Enabled** |
| Active task running | **Disabled** (greyed out) |
| Auto Battle in progress | Shows "⚔ Running..." + disabled Start button |

**Flow:**
1. User clicks "⚔ Auto Battle"
2. Frontend calls `POST /api/control/auto-battle`
3. Backend creates an ephemeral `Battle()` instance and runs `battle()`
4. While running: Start button is disabled, Auto Battle button shows active state
5. On battle finish: state resets, Auto Battle button returns to idle
6. No effect on the task queue — this is entirely out-of-band

### State Machine

```
          click
  IDLE ──────────► RUNNING
   ▲                  │
   │    battle ends   │
   └──────────────────┘
```

### Frontend Changes

- New button in `.screenshot-controls`
- New state field: `autoBattle: false` — toggled by WS events
- `renderButton()` checks `state.autoBattle` to disable Start
- New function `startAutoBattle()` calling the endpoint

### Backend Changes

**New endpoint:** `POST /api/control/auto-battle`
- Rejects if a task is currently `active`
- Runs `Battle()()` on the task worker (or a dedicated thread)
- Sends WS events: `auto_battle_started`, `auto_battle_finished`
- Sets/clears a flag so `controlToggle` (Start) is blocked during auto-battle

**In `fgoTaskQueue.py` or `fgoWebServerNew.py`:**

```python
@app.post("/api/control/auto-battle")
async def auto_battle():
    if task_queue.is_busy():
        raise HTTPException(409, "A task is currently running")
    # Run battle in background thread
    task_queue.run_one_off(lambda: fgoKernel.Battle()())
    return {"ok": True}
```

### Integration with `noSkill`

The auto-battle button could optionally respect a global toggle or use the current `Turn.noSkill` state. For V1, it uses whatever `Turn.noSkill` is currently set to (can be toggled via a separate config endpoint later).

---

## Implementation Order

1. `Turn.normalAttackOnly` flag in kernel (✅ already done — skips skills + NP)
2. Backend: wire `normal_attack_only` param in task queue
3. Frontend: checkbox in Add Quest
4. Backend: reorder endpoint
5. Frontend: expandable items + drag-and-drop
6. Backend: auto-battle endpoint + WS events
7. Frontend: Auto Battle button + state handling

---

## Open Questions

- Should reorder animation be CSS-only or use a micro-library like SortableJS?
  - Recommendation: plain HTML5 DnD for now, zero dependencies
