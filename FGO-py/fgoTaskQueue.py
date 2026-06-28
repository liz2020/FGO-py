"""Task queue for web UI — manages async task execution with status broadcasting.

Design:
- Pending tasks sit in a queue (FIFO)
- "Start" dequeues the top item into an active slot and executes it
- Auto-advance: after completion, next item auto-starts
- "Cancel" stops the active task, marks it cancelled, halts auto-advance
- Done tasks disappear; cancelled tasks stay in a separate list
"""
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Callable

import fgoDevice
import fgoKernel
from fgoSchedule import ScriptStop, schedule
from fgoLogging import getLogger

logger = getLogger('TaskQueue')


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: str = ""  # "operation" | "battle"
    params: dict = field(default_factory=dict)
    status: str = "pending"  # "pending" | "active" | "cancelled" | "error"
    result: dict | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self):
        return asdict(self)


class TaskQueue:
    """Thread-safe task queue with start/cancel control.

    Flow:
    - add() puts tasks into pending queue
    - start() dequeues top → active slot → worker executes
    - On success: task disappears, auto-starts next
    - cancel() stops active task → marks cancelled → halts queue
    - start() again picks next from queue
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: deque[Task] = deque()
        self._current: Task | None = None  # stays set even after cancel (shows in active slot)
        self._running = False  # True = auto-advancing through queue
        self._has_work = threading.Event()
        self._subscribers: list[Callable] = []

    @property
    def current(self) -> Task | None:
        return self._current

    @property
    def is_running(self) -> bool:
        return self._running

    def add(self, task: Task) -> Task:
        with self._lock:
            self._tasks.append(task)
        return task

    def remove(self, task_id: str) -> bool:
        """Remove a pending task or clear the cancelled active task."""
        with self._lock:
            # Clear cancelled/error task from active slot
            if self._current and self._current.id == task_id and self._current.status in ("cancelled", "error"):
                self._current = None
                return True
            for i, t in enumerate(self._tasks):
                if t.id == task_id:
                    del self._tasks[i]
                    return True
        return False

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self._tasks]

    def get_state(self) -> dict:
        """Full state snapshot for UI."""
        with self._lock:
            return {
                "active": self._current.to_dict() if self._current else None,
                "pending": [t.to_dict() for t in self._tasks],
                "running": self._running,
            }

    def start(self):
        """Start queue: dequeue top item and execute. Auto-advances."""
        # Clear any cancelled/error task from active slot
        if self._current and self._current.status in ("cancelled", "error"):
            self._current = None
        self._running = True
        schedule.reset()
        self._has_work.set()

    def cancel(self):
        """Cancel the active task and stop auto-advance."""
        self._running = False
        schedule.stop('Cancelled by user')

    def pop_next(self) -> Task | None:
        """Block until a task is available and queue is running."""
        while True:
            self._has_work.wait(timeout=1.0)
            if not self._running:
                self._has_work.clear()
                continue
            with self._lock:
                if self._tasks:
                    return self._tasks.popleft()
                # Queue empty — go idle
                self._running = False
                self._has_work.clear()
            self._broadcast({"event": "queue_idle"})

    def subscribe(self, callback: Callable):
        self._subscribers.append(callback)

    def _broadcast(self, event: dict):
        for cb in self._subscribers[:]:
            try:
                cb(event)
            except Exception:
                pass


class TaskWorker(threading.Thread):
    """Single worker thread that processes tasks from the queue serially."""

    def __init__(self, queue: TaskQueue):
        super().__init__(daemon=True, name="TaskWorker")
        self.queue = queue

    def run(self):
        logger.info("TaskWorker started")
        while True:
            task = self.queue.pop_next()
            if task is None:
                continue

            self.queue._current = task
            task.status = "active"
            task.started_at = time.time()
            self.queue._broadcast({"event": "task_started", "task": task.to_dict()})

            try:
                schedule.reset()
                result = self._execute(task)
                # Success — task disappears
                task.status = "done"
                task.finished_at = time.time()
                task.result = result or {}
                self.queue._current = None
                logger.info(f"Task {task.id} completed successfully")
            except ScriptStop as e:
                # Cancelled — stays in active slot for UI to show
                task.status = "cancelled"
                task.result = {"error": str(e)}
                task.finished_at = time.time()
                logger.info(f"Task {task.id} cancelled: {e}")
            except Exception as e:
                # Error — stays in active slot
                task.status = "error"
                task.result = {"error": repr(e)}
                task.finished_at = time.time()
                logger.exception(f"Task {task.id} failed")
            finally:
                self.queue._broadcast({
                    "event": "task_finished",
                    "task": task.to_dict(),
                    "state": self.queue.get_state(),
                })

    def _execute(self, task: Task) -> dict:
        match task.type:
            case "operation":
                quests = [(tuple(q["quest"]), q["count"]) for q in task.params["quests"]]
                apple_total = task.params.get("apple_total", 0)
                apple_kind = ["gold", "silver", "bronze", "copper", "quartz"].index(
                    task.params.get("apple_kind", "gold")
                )
                logger.info(f"Starting operation: quests={quests}, apples={apple_total}")
                op = fgoKernel.Operation(quests, apple_total, apple_kind)
                op()
                return {"battle_count": getattr(op, 'battleCount', 0)}
            case "battle":
                logger.info("Starting battle")
                fgoKernel.Battle()()
                return {}
            case _:
                raise ValueError(f"Unknown task type: {task.type}")


# Module-level singleton
task_queue = TaskQueue()
task_worker = TaskWorker(task_queue)
