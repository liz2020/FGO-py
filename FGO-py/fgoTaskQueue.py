"""Task queue for web UI — manages async task execution with status broadcasting."""
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

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
    status: str = "pending"  # "pending" | "running" | "done" | "error" | "cancelled"
    result: dict | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self):
        return asdict(self)


class TaskQueue:
    """Thread-safe task queue with start/pause control."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: deque[Task] = deque()
        self._history: list[Task] = []  # completed/errored tasks
        self._current: Task | None = None
        self._running = False
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
            if self._running:
                self._has_work.set()
        self._broadcast({"event": "queue_updated", "queue": self.list_all()})
        return task

    def remove(self, task_id: str) -> bool:
        with self._lock:
            for i, t in enumerate(self._tasks):
                if t.id == task_id:
                    t.status = "cancelled"
                    del self._tasks[i]
                    self._history.append(t)
                    break
            else:
                return False
        self._broadcast({"event": "queue_updated", "queue": self.list_all()})
        return True

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self._tasks]

    def list_all(self) -> list[dict]:
        with self._lock:
            result = []
            if self._current:
                result.append(self._current.to_dict())
            result.extend(t.to_dict() for t in self._tasks)
            return result

    def start(self):
        """Start or resume queue processing."""
        self._running = True
        if self._tasks:
            self._has_work.set()
        schedule.reset()
        self._broadcast({"event": "queue_started"})

    def pause(self):
        """Pause queue processing (finishes current task's current action, then waits)."""
        schedule.pause()
        self._broadcast({"event": "queue_paused"})

    def stop_current(self):
        """Stop the currently running task, move to next."""
        schedule.stop('Stopped by user')

    def stop_all(self):
        """Stop current task and clear the queue."""
        with self._lock:
            for t in self._tasks:
                t.status = "cancelled"
                self._history.append(t)
            self._tasks.clear()
        schedule.stop('Stopped all')
        self._broadcast({"event": "queue_updated", "queue": self.list_all()})

    def pop_next(self) -> Task | None:
        """Block until a task is available and queue is running. Returns None if stopped."""
        while True:
            self._has_work.wait(timeout=1.0)
            if not self._running:
                continue
            with self._lock:
                if self._tasks:
                    return self._tasks.popleft()
                self._has_work.clear()

    def subscribe(self, callback: Callable):
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

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
        self._progress_thread: threading.Thread | None = None

    def run(self):
        logger.info("TaskWorker started")
        while True:
            task = self.queue.pop_next()
            if task is None:
                continue

            self.queue._current = task
            task.status = "running"
            task.started_at = time.time()
            self.queue._broadcast({"event": "task_started", "task": task.to_dict()})

            try:
                schedule.reset()
                result = self._execute(task)
                task.status = "done"
                task.result = result or {}
            except ScriptStop as e:
                task.status = "error"
                task.result = {"error": str(e)}
                logger.info(f"Task {task.id} stopped: {e}")
            except Exception as e:
                task.status = "error"
                task.result = {"error": repr(e)}
                logger.exception(f"Task {task.id} failed")
            finally:
                task.finished_at = time.time()
                self.queue._current = None
                self.queue._history.append(task)
                self.queue._broadcast({"event": "task_completed", "task": task.to_dict()})

    def _execute(self, task: Task) -> dict:
        match task.type:
            case "operation":
                quests = [(tuple(q["quest"]), q["count"]) for q in task.params["quests"]]
                apple_total = task.params.get("apple_total", 0)
                apple_kind = ["gold", "silver", "bronze", "copper", "quartz"].index(
                    task.params.get("apple_kind", "gold")
                )
                # Start progress monitor
                self._start_progress_monitor(task)
                op = fgoKernel.Operation(quests, apple_total, apple_kind)
                op()
                return {"battle_count": getattr(op, 'battleCount', 0)}
            case "battle":
                self._start_progress_monitor(task)
                fgoKernel.Battle()()
                return {}
            case _:
                raise ValueError(f"Unknown task type: {task.type}")

    def _start_progress_monitor(self, task: Task):
        """Poll progress from the kernel and broadcast updates."""
        def monitor():
            while task.status == "running":
                time.sleep(2)
                if task.status != "running":
                    break
                self.queue._broadcast({
                    "event": "task_progress",
                    "task_id": task.id,
                    "elapsed": time.time() - (task.started_at or time.time()),
                })

        t = threading.Thread(target=monitor, daemon=True, name="ProgressMonitor")
        t.start()


# Module-level singleton
task_queue = TaskQueue()
task_worker = TaskWorker(task_queue)
