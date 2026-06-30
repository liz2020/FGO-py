# 009 — Wrapper Task: Repeatable Task Groups

**Date**: 2026-06-29  
**Status**: Draft  
**Author**: @liz2020

## Problem

The current task queue is a flat list — each task executes once, in order. Users who want to repeat a *group* of tasks (e.g., farm → wait → farm again, looped 5 times) must manually enqueue 15 individual tasks. There is no way to express "run these N tasks K times."

### Example Pain Point

A daily overnight farming routine:
1. Farm quest ×10
2. Wait 120 min (AP regen)
3. Farm quest ×10
4. Wait 120 min
5. Farm quest ×10

The user must add 5 separate tasks. If they want 10 cycles, that's 20 tasks — tedious and error-prone.

## Proposed Design: Wrapper Task

Introduce a **wrapper task** (`type: "loop"`) that holds a group of child tasks and a repeat counter. When the wrapper reaches the front of the queue, it *unfolds* one iteration of its children, then decrements its counter.

### Data Model

```python
@dataclass
class Task:
    id: str
    type: str           # existing types + "loop"
    params: dict
    status: str
    # ... existing fields ...
    children: list[Task] = field(default_factory=list)  # only used by "loop" tasks
```

A `loop` task's `params` contain:
```json
{
    "type": "loop",
    "params": {
        "remaining": 3
    },
    "children": [
        {"type": "operation", "params": {"quests": [...]}},
        {"type": "wait", "params": {"minutes": 120}}
    ]
}
```

### Lifecycle

```
Queue state at start:
  [ loop(remaining=3, children=[A, B]) ]

Step 1 — Worker pops the loop task. remaining > 0, so:
  - Copy children [A', B'] and insert them BEFORE the loop task
  - Decrement remaining → 2
  - Push the loop task back to front (after the copies)
  Queue: [ A', B', loop(remaining=2, children=[A, B]) ]

Step 2 — Worker pops A', executes it normally.
Step 3 — Worker pops B', executes it normally.

Step 4 — Worker pops the loop task again. remaining > 0, so:
  - Copy children [A'', B''] before itself
  - Decrement remaining → 1
  Queue: [ A'', B'', loop(remaining=1, children=[A, B]) ]

... (A'' and B'' execute) ...

Step 6 — Worker pops the loop task again. remaining > 0, so:
  - Copy children [A''', B'''] before itself
  - Decrement remaining → 0
  Queue: [ A''', B''', loop(remaining=0, children=[A, B]) ]

Step 8 — Worker pops the loop task. remaining == 0:
  - Discard the loop task (done)
  Queue: []
```

### Sequence Diagram

```
┌─────────┐          ┌──────────┐         ┌────────┐
│ Worker   │          │ TaskQueue│         │ Queue  │
└────┬─────┘          └────┬─────┘         └───┬────┘
     │  pop_next()         │                   │
     │────────────────────►│                   │
     │  loop(rem=3,[A,B])  │                   │
     │◄────────────────────│                   │
     │                     │                   │
     │  _unfold_loop()     │                   │
     │─────────────────────────────────────────►│
     │  insert [A',B'] then loop(rem=2)        │
     │                     │                   │
     │  pop_next()         │                   │
     │────────────────────►│  returns A'       │
     │◄────────────────────│                   │
     │  execute(A')        │                   │
     │─ ─ ─ ─ ─ ─ ─ ─ ─ ─►                   │
     │                     │                   │
     │  pop_next()         │                   │
     │────────────────────►│  returns B'       │
     │◄────────────────────│                   │
     │  execute(B')        │                   │
     │─ ─ ─ ─ ─ ─ ─ ─ ─ ─►                   │
     │                     │                   │
     │  pop_next()         │                   │
     │────────────────────►│                   │
     │  loop(rem=2,[A,B])  │                   │
     │◄────────────────────│                   │
     │  _unfold_loop()     │  ... repeats ...  │
```

### Queue State Visualization

```
Initial:                    After 1st unfold:           After A' done:
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│ loop(3) [A, B]   │        │ A' (copy)        │        │ B' (copy)        │
└──────────────────┘        │ B' (copy)        │        │ loop(2) [A, B]   │
                            │ loop(2) [A, B]   │        └──────────────────┘
                            └──────────────────┘

After 2nd unfold:           After last unfold:          Final:
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│ A'' (copy)       │        │ A''' (copy)      │        │ (empty)          │
│ B'' (copy)       │        │ B''' (copy)      │        └──────────────────┘
│ loop(1) [A, B]   │        │ loop(0) [A, B]   │
└──────────────────┘        └──────────────────┘
```

## Implementation

### 1. TaskQueue Changes

The loop task unfolds inside the worker's main loop, not inside `_execute()`. When the worker pops a `loop` task, it handles it specially before moving to execution:

```python
# In TaskWorker.run()

task = self.queue.pop_next()
if task is None:
    continue

# Handle loop tasks: unfold one iteration
if task.type == "loop":
    remaining = task.params.get("remaining", 0)
    if remaining > 0:
        task.params["remaining"] = remaining - 1
        # Deep-copy children with fresh IDs and insert before the loop
        import copy
        copies = []
        for child in task.children:
            c = copy.deepcopy(child)
            c.id = uuid.uuid4().hex[:8]
            c.status = "pending"
            copies.append(c)
        with self.queue._lock:
            # Insert copies + loop back at the front
            for item in reversed(copies + [task]):
                self.queue._tasks.appendleft(item)
        self.queue._broadcast({
            "event": "state_updated",
            "state": self.queue.get_state(),
        })
        continue  # Go back to pop_next(), which will get the first copy
    else:
        # remaining == 0 → loop is done, discard it
        self.queue._broadcast({
            "event": "state_updated",
            "state": self.queue.get_state(),
        })
        continue

# Normal task execution follows...
```

### 2. Task Serialization

`to_dict()` must include children for loop tasks:

```python
def to_dict(self):
    d = asdict(self)
    if self.children:
        d["children"] = [c.to_dict() for c in self.children]
    return d
```

### 3. API Changes

#### Adding a loop task

```
POST /api/queue
{
    "type": "loop",
    "params": {"remaining": 3},
    "children": [
        {"type": "operation", "params": {"quests": [{"quest": [1,0,3,0], "count": 5}]}},
        {"type": "wait", "params": {"minutes": 120}}
    ]
}
```

The server validates:
- `remaining` ≥ 1
- `children` is non-empty
- Each child has a valid task type (no nested loops — see Open Questions)

#### Modifying a loop task

Two new endpoints:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/queue/{loop_id}/add-child` | Move a pending task into the loop's children |
| POST | `/api/queue/{loop_id}/remove-child` | Remove a child from the loop and put it back in the main queue |
| PATCH | `/api/queue/{loop_id}` | Update `remaining` count |

#### Moving tasks into a loop

```
POST /api/queue/{loop_id}/add-child
{"task_id": "abc123"}
```

This removes task `abc123` from the pending queue and appends it to the loop's `children` list. The task must be pending (not active).

#### Removing children from a loop

```
POST /api/queue/{loop_id}/remove-child
{"task_id": "child456"}
```

Removes the child from the loop and places it back in the pending queue (after the loop task).

### 4. Cancellation Behavior

When a queue cancel happens during a loop iteration:
- The currently executing child task gets cancelled (existing behavior via `schedule.stop()`)
- The unfolded copies that haven't run yet stay in the queue as pending
- The loop task itself stays in the queue with its current `remaining` count
- When the user hits Start again, execution resumes from where it left off

This is consistent with existing cancel behavior — the queue state is preserved.

### 5. UI Changes

#### Queue List Rendering

Loop tasks render as a collapsible group in the queue:

```
┌─ Queue ───────────────────────────────┐
│ 1. Farm 冬木 ×5                 [🗑]  │  ← normal task
│                                       │
│ 2. 🔁 Loop ×3                  [🗑]  │  ← loop wrapper
│    ├─ Farm 雅戈泰 ×10                 │
│    └─ Wait 120 min                    │
│                                       │
│ 3. Stop Emulator                [🗑]  │  ← normal task
└───────────────────────────────────────┘
```

When a loop is unfolded and running, the queue shows both the unfolded copies and the remaining loop:

```
┌─ Queue ───────────────────────────────┐
│ ▶ Farm 雅戈泰 ×10         [active]   │  ← unfolded copy (executing)
│                                       │
│ 1. Wait 120 min              [🗑]    │  ← unfolded copy
│                                       │
│ 2. 🔁 Loop ×2 remaining      [🗑]   │  ← loop, decremented
│    ├─ Farm 雅戈泰 ×10                │
│    └─ Wait 120 min                   │
│                                       │
│ 3. Stop Emulator              [🗑]   │
└───────────────────────────────────────┘
```

#### "Add Loop" UI

Add a "wrap as loop" action. The user can:

1. **Create loop from scratch**: A new "🔁 Add Loop" button creates an empty loop task. The user then drags existing tasks into it or adds children via the existing Add Quest / Add Task forms (with a "target: loop" selector).

2. **Wrap selected tasks**: Multi-select pending tasks → "Wrap in loop" button → prompts for repeat count → creates a loop containing those tasks.

```
┌─ Add Task ────────────────────────────┐
│ Task Type  [Loop (Repeat Group) ▾]    │
│                                       │
│ Repeat Count: [3  ]                   │
│                                       │
│ ℹ Add tasks to the loop after         │
│   creating it by dragging them in.    │
│                                       │
│ [        + Add to Queue             ] │
└───────────────────────────────────────┘
```

#### describeTask for loop

```js
function describeTask(task) {
    if (task.type === 'loop') {
        const n = task.children ? task.children.length : 0;
        const rem = task.params.remaining || 0;
        return `🔁 Loop ×${rem} (${n} tasks)`;
    }
    // ... existing cases
}
```

## Edge Cases

### Loop with remaining=0 at creation
Reject at API level — must be ≥ 1.

### All children removed from a loop
If children is empty when the loop reaches the front, skip it (discard as no-op).

### Loop task deleted
Remove the loop task and all its children from the queue. Unfolded copies that are already in the main queue stay (they are independent tasks at that point).

### Reorder with loops
Loop tasks participate in drag-and-drop reorder like any other task. The children order within a loop is fixed (FIFO) and must be reordered via a separate children-reorder endpoint if needed.

### Cancel mid-loop
See §4 above. The partially-unfolded state is preservable and resumable.

## Open Questions

1. **Nested loops** — Should a loop be allowed to contain another loop? This enables complex schedules but adds complexity. **Recommendation**: Disallow for now. Validate at API level that children cannot be type `loop`.

2. **Infinite loops** — Should `remaining` accept a sentinel value (e.g., -1) meaning "repeat forever until cancelled"? Useful for overnight farming. **Recommendation**: Yes, support `remaining: -1` as infinite. The only way to stop is cancel.

3. **Editing children of a running loop** — If the loop has already unfolded once, editing its children affects only *future* iterations. The currently-unfolded copies are independent. This is intuitive but should be documented in the UI.

4. **Progress reporting** — How should the emu manager progress bar reflect loop progress? Options:
   - Show overall: `iteration 2/3, task 1/2`
   - Show only current task progress (simpler)
   - **Recommendation**: Show current task progress as today, but add loop iteration info to the detail string: `冬木 ×10 (loop 2/3)`

5. **Drag children into/out of loop** — Should the UI support drag-and-drop to move tasks between the main queue and a loop's children list? This is the most intuitive UX but requires more complex drag-and-drop handling. **Recommendation**: Support it as a Phase 2 UI enhancement. Phase 1 uses the API endpoints (add-child/remove-child buttons).
