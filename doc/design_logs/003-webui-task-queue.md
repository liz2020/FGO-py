# Design: Web UI Task Queue & Navigation

**Date**: 2026-06-28  
**Status**: Draft  
**Author**: @liz2020

## Problem

The current Web UI has two critical limitations:

1. **No navigation** — only CLI can do `goto(quest)` and `Operation(queue)`. The Web UI can only "start battle at current quest."
2. **Blocking HTTP** — `POST /api/run/main` blocks the connection for minutes/hours until all battles complete. No progress feedback, no way to queue multiple tasks.

## Proposed Architecture

Replace blocking HTTP calls with a **server-side task queue** + **WebSocket status stream**.

```
┌─────────────────────────────────────────────────────┐
│  Web UI (browser)                                    │
│                                                      │
│  [Quest Selector] → POST /api/queue/add             │
│  [Status Panel]   ← WebSocket /ws/status            │
│  [Queue Manager]  → POST /api/queue/remove|reorder  │
└────────────────────────────┬────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────┐
│  Web Server (FastAPI)                                │
│                                                      │
│  TaskQueue (thread-safe deque)                       │
│  ┌────────┬────────┬────────┬────────┐             │
│  │ Task 1 │ Task 2 │ Task 3 │  ...   │             │
│  └────┬───┴────────┴────────┴────────┘             │
│       │                                              │
│  Worker Thread (single, serial execution)            │
│       │                                              │
│       ├── goto(quest)     ← navigation               │
│       ├── Main(apple...)  ← battle loop              │
│       ├── fpSummon()      ← utility tasks            │
│       └── emit status → WebSocket broadcast          │
└──────────────────────────────────────────────────────┘
```

## Task Types

Each queue item is a typed task:

```python
@dataclass
class Task:
    id: str              # UUID
    type: str            # "battle" | "operation"
    params: dict         # type-specific parameters
    status: str          # "pending" | "active" | "error"
    result: dict | None  # filled on completion
    created_at: float
    started_at: float | None
    finished_at: float | None
```

### Task type: `operation` (navigate + battle)

The primary use case — equivalent to CLI's `main -q 1-0-3 5 -q 2-1-0 3`:

```json
{
    "type": "operation",
    "params": {
        "quests": [
            {"quest": [1, 0, 3], "count": 5},
            {"quest": [2, 1, 0], "count": 3}
        ],
        "apple_total": 10,
        "apple_kind": "bronze"
    }
}
```

Internally this calls `Operation([(quest, count), ...], appleTotal, appleKind)`.

**Friend selection**: Always picks the first available friend (no template matching). More sophisticated friend selection (class filter, servant matching) deferred to future work.

### Task type: `battle`

Just finish current battle using smart battle (Turn AI). Classic battle is excluded from web UI for now (pending future redesign).

```json
{
    "type": "battle",
    "params": {}
}
```

### Task type: `call` (future, CLI-only for now)

Utility functions like `fpSummon`, `lottery`, `mail`, `synthesis` remain CLI-only.
They can be added to the web queue later if needed.

## API Design

### REST endpoints (task management)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/queue` | List all tasks (pending + running + recent completed) |
| POST | `/api/queue` | Add task to queue (returns task ID) |
| DELETE | `/api/queue/{id}` | Cancel/remove a pending task |
| POST | `/api/queue/{id}/move` | Reorder (body: `{"position": 0}`) |
| POST | `/api/control/start` | Start processing the queue (resume if paused) |
| POST | `/api/control/pause` | Pause current execution (queue stops after current task) |
| POST | `/api/control/stop` | Stop current task immediately |
| GET | `/api/quests` | List available quests for navigation |

### WebSocket (real-time status)

`WS /ws/status` — pushes JSON events:

```json
{"event": "task_started", "task": {...}}
{"event": "task_progress", "task_id": "...", "battle_count": 3, "battle_total": null, "turn": 5}
{"event": "task_completed", "task": {...}}
{"event": "task_error", "task_id": "...", "error": "AP Empty"}
{"event": "queue_updated", "queue": [...]}
{"event": "screenshot", "image": "base64..."}  // optional live feed
```

## Web UI Layout (Mobile-First)

Single-column, top-to-bottom layout optimized for phone screens:

```
┌─────────────────────────────────┐
│  [← Manager]          FGO-py    │
├─────────────────────────────────┤
│  📷 Game Screen (full width)    │
│  ┌─────────────────────────────┐│
│  │                             ││
│  │      (live screenshot)      ││
│  │                             ││
│  └─────────────────────────────┘│
│  [● Live] toggle button         │
├─────────────────────────────────┤
│  🎮 Controls                    │
│  [▶ Start] [⏸ Pause]           │
│  Status: 雅戈泰 #3 — 2/5 T3    │
├─────────────────────────────────┤
│  📝 Queue                       │
│  1. 雅戈泰 #3 ×5         [🗑] │
│  2. 冬木 #7 ×10           [🗑] │
│  3. 新宿 #2 ×3            [🗑] │
├─────────────────────────────────┤
│  📋 Add Quest                   │
│  Part:    [Part 2 ▼]           │
│  Chapter: [雅戈泰 ▼]           │
│  Quest:   [Node 3 ▼]           │
│  Repeat:  [5]  [+ Add]         │
└─────────────────────────────────┘
```

- Navigation back to emu manager at top
- Game screenshot with live toggle (auto-refresh every 2s when enabled)
- Controls + status immediately below
- Queue list above quest selector (more frequently viewed)
- Quest selector at bottom (used less often once queue is set up)
- On wider screens (tablet/desktop), controls + queue can sit side-by-side below the screenshot



The Web UI needs a quest catalog to present to the user.

**Data source**: FGO-py already has complete human-readable quest name mappings in the i18n translation files (`fgoI18n.zh.ts`, `fgoI18n.en.ts`, `fgoI18n.ja.ts`). These are Qt `.ts` XML files with entries like:

```xml
<context>
  <name>quest</name>
  <message><source>1-0</source><translation>冬木</translation></message>        <!-- chapter -->
  <message><source>1-0-0-0</source><translation>未确认坐标X-A</translation></message> <!-- quest node -->
</context>
```

**Plan**: Parse the i18n files at startup to build a structured quest catalog. Cross-reference with `fgoReishift.place` keys to know which quests are navigable. Serve as `GET /api/quests` so the frontend can render a quest picker with proper names in the user's language.

## Worker Thread Design

```python
class TaskWorker(threading.Thread):
    def __init__(self, queue: TaskQueue):
        super().__init__(daemon=True)
        self.queue = queue
        self.current: Task | None = None

    def run(self):
        while True:
            task = self.queue.pop_next()  # blocks until available
            self.current = task
            task.status = "running"
            task.started_at = time.time()
            self._broadcast({"event": "task_started", "task": task.to_dict()})
            
            try:
                result = self._execute(task)
                task.status = "done"
                task.result = result
            except ScriptStop as e:
                task.status = "error"
                task.result = {"error": str(e)}
            except Exception as e:
                task.status = "error"
                task.result = {"error": repr(e)}
            finally:
                task.finished_at = time.time()
                self.current = None
                self._broadcast({"event": "task_completed", "task": task.to_dict()})

    def _execute(self, task: Task) -> dict:
        match task.type:
            case "operation":
                op = fgoKernel.Operation(
                    [(q["quest"], q["count"]) for q in task.params["quests"]],
                    task.params.get("apple_total", 0),
                    ["gold","silver","bronze","copper","quartz"].index(task.params.get("apple_kind", "gold")),
                )
                Main.teamIndex = task.params.get("team_index", 0)
                op()
                return op.result
            case "battle":
                b = fgoKernel.Battle()
                b()
                return b.result
            case "call":
                return getattr(fgoKernel, task.params["func"])() or {}
```

## Progress Reporting

The worker needs to emit progress during long-running tasks. Two approaches:

**Option A**: Poll from the Main/Battle object (current `_run_with_progress` pattern):
```python
# Background monitor thread watches battleCount, turn, etc.
while task.status == "running":
    broadcast({"event": "task_progress", ...})
    time.sleep(2)
```

**Option B**: Inject a callback into the kernel (more invasive but cleaner):
```python
# Future: add event hooks to Battle/Main classes
```

**Recommendation**: Start with Option A (polling) since it requires no kernel changes.

## Migration Path

### Phase 1: Queue backend (no UI changes yet)

1. Add `TaskQueue` + `TaskWorker` classes
2. Add new REST endpoints alongside existing ones
3. Add WebSocket endpoint for status
4. Keep old blocking endpoints working (deprecated)

### Phase 2: Quest catalog API

1. Extract quest data from `fgoReishift.place` into a structured catalog
2. Serve as `GET /api/quests` with human-readable names
3. Add quest name metadata (currently quests are just tuples)

### Phase 3: Web UI overhaul

1. Quest selector (tree: Part → Chapter → Quest)
2. Task queue panel (add, reorder, remove, status)
3. Live status (current task, battle count, turn, screenshot)
4. Replace blocking "肝" button with "add to queue"

## Framework Choice

Current server uses **Flask** (synchronous). For WebSocket support + async:

**Option A**: Switch to **FastAPI** + `uvicorn` (already in `pyproject.toml` dependencies!)
- ✅ Native WebSocket support
- ✅ Async endpoints for non-blocking
- ✅ Already a dependency
- Worker thread stays synchronous (kernel is thread-based)

**Option B**: Keep Flask + add `flask-sock`
- ✅ Minimal change
- ❌ Less natural async support

**Recommendation**: Use FastAPI — it's already in dependencies and the project has `uvicorn` too. The existing Flask server seems to be legacy; FastAPI is clearly intended as the future.

## Open Questions

1. **Quest naming**: ✅ Resolved — use existing i18n `.ts` files for human-readable names.
2. **Classic vs Smart battle**: ✅ Resolved — web UI uses smart battle (Turn AI) only. Classic battle excluded pending future redesign.
3. **Multiple queues**: Should daily tasks (FP summon, mail) be in the same queue as farming, or separate? → Deferred, utility tasks are CLI-only for now.
4. **Persistence**: Should the queue survive server restart? (Probably not needed — it's a live automation tool)
