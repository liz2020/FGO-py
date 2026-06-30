# 008 — New Task Types & Emulator–Script Decoupling

**Date**: 2026-06-29  
**Status**: Draft  
**Author**: @liz2020

## Problem

1. **Limited task types** — The Web UI "Add Quest" section only supports quest-farming operations. Users cannot queue system-level actions like stopping/starting the emulator, waiting, or AP recovery as part of an automated workflow.

2. **Emu manager coupling** — Currently stopping an emulator must first stop the FGO-py script (007 "Stop Cascade"). This coupling is backwards: the *script* should be able to orchestrate the emulator (e.g., restart it for stability), not the other way around.

3. **Script status invisible when emulator is down** — The emu manager dashboard only shows FGO-py script status for running emulators. If a script intentionally stopped the emulator (e.g., for a cooldown) and is waiting to restart it, the user has no visibility into that state.

## Proposed Design

### New "Add Task" Section

Add a new collapsible section below "Add Quest" in the FGO-py Web UI (`queue.html`). This section lets users enqueue system-level tasks that execute in the same serial task queue as quest operations.

#### UI Wireframe

```
┌─ Add Quest ──────────────────────┐
│ Part     [Select ▾]              │
│ Chapter  [Select ▾]              │
│ Quest    [Select ▾]              │
│ Repeat   [1  ]                   │
│ ☐ 仅使用普通攻击                  │
│ [        + Add to Queue        ] │
└──────────────────────────────────┘

┌─ Add Task ───────────────────────┐
│ Task Type  [Select ▾]            │
│                                  │
│ ┌ (varies by type) ───────────┐  │
│ │  ...parameters...           │  │
│ └─────────────────────────────┘  │
│                                  │
│ [        + Add to Queue        ] │
└──────────────────────────────────┘
```

### New Task Types

| Type | Description | Parameters |
|------|-------------|------------|
| `stop_emulator` | Gracefully stop the emulator instance | — |
| `start_emulator` | Launch the emulator instance | — |
| `stop_script` | Stop the FGO-py script (clears queue) | — |
| `wait` | Pause execution for a duration | `minutes: int` |
| `eat_apple` | Use AP recovery apple | `apple_kind: gold \| silver \| bronze \| copper \| quartz` |

#### Task Parameter UI (conditional rendering)

```
Task Type: [Stop Emulator ▾]     → no parameters
Task Type: [Start Emulator ▾]    → no parameters  
Task Type: [Stop Script ▾]       → no parameters
Task Type: [Wait ▾]              → Minutes: [30  ]
Task Type: [Eat Apple ▾]         → Apple: [Gold ▾] [Silver ▾] [Bronze ▾] [Copper ▾] [Quartz ▾]
```

### Task Definitions

```python
# In task queue type routing

case "stop_emulator":
    # Call emu manager API or ldconsole directly
    ldconsole.quit(instance_index)
    return {"stopped": True}

case "start_emulator":
    # Launch emulator, wait for boot
    ldconsole.launch(instance_index)
    wait_for_emulator_ready(instance_index)
    return {"started": True}

case "stop_script":
    # Clear remaining queue, raise ScriptStop
    task_queue.clear_pending()
    raise ScriptStop("stop_script task executed")

case "wait":
    minutes = task.params["minutes"]
    schedule.sleep(minutes * 60)
    return {"waited_minutes": minutes}

case "eat_apple":
    apple_kind = task.params["apple_kind"]
    # Navigate to AP recovery dialog, select apple, confirm
    fgoKernel.eatApple(apple_kind)
    return {"apple_kind": apple_kind}
```

### Example Workflow

A user could queue:
1. Quest farming ×10 (uses all AP)
2. Wait 120 minutes (natural AP regen)
3. Quest farming ×10 (another session)
4. Stop emulator (done for the day)

Or for emulator stability:
1. Quest farming ×20
2. Stop emulator
3. Wait 5 minutes (cooldown)
4. Start emulator
5. Quest farming ×20

---

## Emulator–Script Decoupling

### Current Flow (007)

```
User clicks "Stop Emulator" on emu dashboard
  → emu service stops FGO-py script first
  → emu service stops emulator
```

The script is treated as a child of the emulator.

### New Flow

```
User clicks "Stop Emulator" on emu dashboard
  → emu service stops emulator directly (no script stop needed)
  → FGO-py script detects emulator gone (screenshot fails)
  → Script enters "emulator_offline" state (does not crash)
  → If script has a "start_emulator" task queued, it waits and resumes

Script executes "stop_emulator" task
  → Script calls ldconsole quit
  → Script knows emulator is stopped (intentional)
  → Script continues to next task (e.g., wait → start_emulator)
```

### Key Changes

1. **Emu manager "Stop" button no longer stops scripts** — Remove the stop-cascade from `emu/service.py`. The script is responsible for its own lifecycle.

2. **Script handles emulator-offline gracefully** — When screenshot/input fails due to emulator being stopped, the script doesn't crash. It enters a waiting state if the stop was intentional (i.e., a `stop_emulator` task was executed).

3. **Script status visible regardless of emulator state** — The emu manager dashboard shows script status (running/waiting/idle/error) even when the emulator is not running.

---

## Script Status on Emu Dashboard (Emulator Offline)

### Current Behavior (from `emu/static/index.html`)

When emulator is **running**, the card shows:
- Header: instance name + `[running]` badge
- Meta: `⏹ Stop` inline button · Index · ADB serial
- Preview area: 16:9 live screenshot with `● Live` toggle (auto-subscribed)
- Automation section: progress bar + `⏹ Stop` script + `→ FGO-py` link

When emulator is **stopped**, the card shows only:
- Header: instance name + `[stopped]` badge
- Meta: Index · ADB serial
- `▶ Launch` button
- **No preview, no script section at all**

### New Behavior

Preview area and script status are **always visible** regardless of emulator state:

```
┌─ Instance: FGO-1 ────────────────────────────┐
│  FGO-1                          [stopped]     │
│  Index 0 · 127.0.0.1:5555                    │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │                                         │  │
│  │         Emulator offline                │  │  ← dimmed placeholder
│  │                                         │  │
│  └─────────────────────────────────────────┘  │
│                                    [● Live]   │  ← disabled when offline
│                                               │
│  [           ▶ Launch            ]            │
│                                               │
│  ── Automation ──────────────────────────     │
│  Script: ⏳ Waiting (restart in 12 min)       │  ← visible!
│  [⏹ Stop]  [→ FGO-py]                        │
└───────────────────────────────────────────────┘

┌─ Instance: FGO-2 ────────────────────────────┐
│  FGO-2                          [running]     │
│  ⏹ Stop · Index 1 · 192.168.1.2:5556        │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │                                         │  │
│  │        (live screenshot)                │  │  ← normal live preview
│  │                                         │  │
│  └─────────────────────────────────────────┘  │
│                               ▶ LIVE [● Live] │
│                                               │
│  ── Automation ──────────────────────────     │
│  ┃████████████░░░░░░░░┃ 3/5 冬木 T2          │  ← progress bar
│  [⏹ Stop]  [→ FGO-py]                        │
└───────────────────────────────────────────────┘
```

### Changes to `renderCard()`

Currently the HTML rendering is gated on `isRunning`:

```js
// Current: preview only when running
if (isRunning) { preview = `...`; }

// Current: scripts section only when running
if (isRunning) { scriptsHtml = `...`; }
```

New logic:
1. **Preview area always renders** — when emulator is offline, show a dimmed placeholder ("Emulator offline"), disable the Live button
2. **Automation section always renders** — script status comes from the FGO-py web server `/api/status`, which is reachable as long as the script process is alive (independent of emulator)
3. **Launch button** still only appears when emulator is stopped
4. **Stop inline button** on meta line still only appears when emulator is running

### Script States (exposed via API)

| State | Description | Shown as |
|-------|-------------|----------|
| `idle` | No task running, queue empty | `Script: Idle` |
| `running` | Actively executing a task | `Script: Running — <task desc>` |
| `waiting` | Executing a `wait` task | `Script: ⏳ Waiting (X min left)` |
| `emu_offline` | Emulator intentionally stopped, pending restart | `Script: 💤 Emu offline` |
| `error` | Task failed | `Script: ❌ Error — <msg>` |
| `stopped` | Script process not running | `Script: —` |

### API Changes

**FGO-py side:**
- `GET /api/status` now includes `script_state` field with the above values
- WebSocket broadcasts state changes

**Emu manager side:**
- `GET /api/instances` response includes `script_status` for each instance (fetched from FGO-py's `/api/status` or from registry metadata)
- The emu dashboard renders script status unconditionally (not gated on `instance.running`)
- The "→ FGO-py" link remains available even when emulator is stopped (script server is still running)

---

## Backend Architecture

### FGO-py Script as Independent Process

The FGO-py process outlives the emulator:

```
┌────────────────────────────────────────┐
│  FGO-py Process (always running)       │
│                                        │
│  ┌─── Task Queue ──────────────────┐   │
│  │ quest ×10 → wait 5m → start_emu │   │
│  └─────────────────────────────────┘   │
│                                        │
│  ┌─── Web Server (FastAPI) ────────┐   │
│  │ Always serving /api + /ws       │   │
│  └─────────────────────────────────┘   │
│                                        │
│  ┌─── Device Layer ────────────────┐   │
│  │ Graceful: returns None on       │   │
│  │ screenshot if emu is offline    │   │
│  └─────────────────────────────────┘   │
└────────────────────────────────────────┘
         │                    ▲
         │ ldconsole          │ ldconsole
         ▼                    │
┌────────────────────┐        │
│  LDPlayer Emulator │ ◄──────┘
│  (may be stopped)  │
└────────────────────┘
```

### Emulator Start/Stop from Script

The FGO-py process needs access to `ldconsole` to control the emulator. Currently only the emu manager uses this.

**Options:**

A. **Direct ldconsole call** — FGO-py imports/shells-out to `ldconsole.exe` directly.
   - ✅ Simple, no dependency on emu manager
   - ❌ Duplicates ldconsole path logic

B. **Call emu manager API** — FGO-py calls `POST /api/instances/{id}/start|stop` on the emu manager.
   - ✅ Single source of truth for emulator control
   - ✅ Emu manager can update its own state immediately
   - ❌ Requires emu manager to be running

**Recommendation:** Option B (call emu manager API). The emu manager is always running when we need emulator control, and it keeps instance state consistent.

---

## Progress Bar: FGO-py → Emu Manager

### Current Infrastructure

The emu manager **already has** a progress reporting system:

```
FGO-py script ──POST──► emu manager /api/scripts/{name}/progress
                              │
emu dashboard ──GET───► emu manager /api/scripts/{name}/progress/{index}
                              │
                         renders progress bar: ┃████░░░░┃ 3/5 冬木 T2
```

**Emu manager endpoints (already implemented):**
- `POST /api/scripts/{name}/progress` — script pushes `{instance_index, current, total, status, detail}`
- `GET /api/scripts/{name}/progress/{index}` — dashboard polls every 10s

**Dashboard rendering (already implemented):**
- Shows progress bar when `total > 0`
- Shows text status when `status !== 'idle'`
- Format: `{current}/{total} {detail}`

**Problem:** FGO-py never calls the POST endpoint. The progress bar sits empty.

### Available Data in Kernel

| Data | Location | When Updated |
|------|----------|--------------|
| Battle count | `Main.battleCount` | After each battle completes |
| Battle turn | `Battle.turn` | At each turn start detection |
| Total battles | `Operation` params sum | Known before execution |
| Quest tuple | `Operation` list items | Known before execution |
| Battle time | `Main.battleTime` | After each battle |
| Defeated count | `Main.defeated` | On battle loss |

### Implementation: Progress Callback

Inject a progress reporter into the `TaskWorker._execute()` that fires after each battle:

```python
# In fgoTaskQueue.py

class TaskWorker:
    def __init__(self, queue, emu_manager_url: str, instance_index: int):
        self.emu_url = emu_manager_url
        self.instance_index = instance_index

    def _report_progress(self, current: int, total: int, detail: str):
        """POST progress to emu manager."""
        try:
            requests.post(f"{self.emu_url}/api/scripts/fgo/progress", json={
                "instance_index": self.instance_index,
                "current": current,
                "total": total,
                "status": "running",
                "detail": detail,
            }, timeout=2)
        except Exception:
            pass  # Non-critical, don't crash on reporting failure

    def _execute(self, task):
        match task.type:
            case "operation":
                quests = task.params["quests"]
                total = sum(q["count"] for q in quests)

                # Progress hook — called from kernel after each battle
                def on_battle_complete(op):
                    quest_name = task.params.get("quest_name", "")
                    detail = f"{quest_name} T{getattr(op, '_currentBattle', {}).get('turn', '?')}"
                    self._report_progress(op.battleCount, total, detail)

                op = fgoKernel.Operation(...)
                op.on_progress = on_battle_complete  # inject callback
                op()
                self._report_progress(total, total, "Done")
                return {"battle_count": op.battleCount}
```

### Kernel Hook Point

The callback fires in `Main.__call__()` after `self.battleCount += 1`:

```python
# In fgoKernel.py, Main.__call__() battle loop

self.battleCount += 1
self.battleTurn += battle.turn
self.battleTime += battle.time

# Progress callback (if set by task queue)
if hasattr(self, 'on_progress') and self.on_progress:
    self.on_progress(self)
```

This is minimally invasive — a single `if hasattr` check in the hot path.

### Detail String Format

The `detail` field shown in the progress bar text:

| Scenario | Detail |
|----------|--------|
| Farming quest | `冬木 #3 T2` (quest name + current turn) |
| Between battles | `冬木 #3` (no turn info) |
| Wait task | `Waiting 12 min` |
| Starting emu | `Booting...` |

### Reset on Idle

When the task finishes or the queue goes idle, clear progress:

```python
self._report_progress(0, 0, "")  # status resets to idle on emu dashboard
```

---

## Implementation Plan

### Phase 1: Task type infrastructure

1. Add new task types to `fgoTaskQueue.py` `_execute()` dispatch
2. Add `wait` task (simplest — just `schedule.sleep()`)
3. Add `stop_script` task (clear queue + raise stop)
4. API: `POST /api/queue` accepts new task types

### Phase 2: Emulator control tasks

1. Add `stop_emulator` task — calls emu manager API
2. Add `start_emulator` task — calls emu manager API + waits for ready
3. FGO-py device layer: graceful handling of offline emulator
4. Remove stop-cascade from `emu/service.py`

### Phase 3: Eat apple task

1. Implement `eat_apple` in kernel (navigate AP dialog, select type, confirm)
2. Wire into task queue

### Phase 4: UI & status visibility

1. Add "Add Task" section to `queue.html` with conditional parameter forms
2. Emu dashboard: show script status regardless of emulator state
3. Emu dashboard: remove script-stop from emulator-stop flow

---

## Open Questions

1. **What happens to a running quest task when emulator stops unexpectedly?** — The screenshot call should raise a retriable error. The task goes to `error` state with a clear message. User can manually restart.

2. **Should `eat_apple` be standalone or only in quest context?** — Standalone is more flexible. Users might want to eat an apple before starting a farming session to top off AP.

3. **Emulator boot time** — `start_emulator` needs a timeout and health check (e.g., poll until ADB is responsive). What's a reasonable timeout? 60s? 120s?

4. **Should `stop_emulator` drain the current task first?** — No, it should be immediate (the previous task should already be complete since the queue is serial). If the user queues `stop_emulator` right after a quest, the quest finishes first naturally.
