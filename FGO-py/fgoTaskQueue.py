"""Task queue for web UI — manages async task execution with status broadcasting.

Design:
- Pending tasks sit in a queue (FIFO)
- "Start" dequeues the top item into an active slot and executes it
- Auto-advance: after completion, next item auto-starts
- "Cancel" stops the active task, marks it cancelled, halts auto-advance
- Done tasks disappear; cancelled tasks stay in a separate list
"""
import json
import threading
import time
import urllib.request
import uuid
import copy
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Callable

import fgoDevice
import fgoKernel
from fgoSchedule import ScriptStop, schedule
from fgoLogging import getLogger

logger = getLogger('TaskQueue')

# --- Progress reporting to emu manager ---

_emu_manager_url: str | None = None
_instance_index: int = 0


def configure_progress(emu_manager_url: str, instance_index: int):
    """Set emu manager URL and instance index for progress reporting."""
    global _emu_manager_url, _instance_index
    _emu_manager_url = emu_manager_url
    _instance_index = instance_index


def _report_progress(current: int, total: int, status: str = "running", detail: str = ""):
    """Report farming progress to emu manager and broadcast to WebSocket clients."""
    # Update active task's progress for local WebSocket clients
    if task_queue._current and task_queue._current.status == "active":
        task_queue._current.progress = {"current": current, "total": total, "detail": detail}
        task_queue._broadcast({"event": "task_progress", "progress": {"current": current, "total": total, "detail": detail}})

    # Report to emu manager
    if not _emu_manager_url:
        return
    try:
        data = json.dumps({
            "instance_index": _instance_index,
            "current": current,
            "total": total,
            "status": status,
            "detail": detail,
        }).encode()
        req = urllib.request.Request(
            f"{_emu_manager_url}/api/scripts/fgo/progress",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: str = ""  # "operation" | "battle" | "loop" | ...
    params: dict = field(default_factory=dict)
    status: str = "pending"  # "pending" | "active" | "cancelled" | "error"
    result: dict | None = None
    progress: dict | None = None  # {current, total, detail}
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    children: list['Task'] = field(default_factory=list)  # only used by "loop" tasks

    def to_dict(self):
        d = asdict(self)
        # asdict recursively converts children, but they become plain dicts —
        # which is exactly what we want for serialization
        return d


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

    def reorder(self, ids: list[str]) -> bool:
        """Reorder pending tasks to match the given ID order."""
        with self._lock:
            if set(ids) != {t.id for t in self._tasks}:
                return False
            index = {t.id: t for t in self._tasks}
            self._tasks = deque(index[i] for i in ids)
            return True

    def is_busy(self) -> bool:
        """True if there is an actively running task."""
        return self._current is not None and self._current.status == "active"

    def clear_pending(self):
        """Remove all pending tasks from the queue."""
        with self._lock:
            self._tasks.clear()

    def find_loop(self, loop_id: str) -> Task | None:
        """Find a loop task in the pending queue by ID."""
        with self._lock:
            for t in self._tasks:
                if t.id == loop_id and t.type == "loop":
                    return t
        return None

    def update_loop(self, loop_id: str, remaining: int | None = None, infinite: bool | None = None) -> bool:
        """Update a pending loop task's remaining count and/or infinite flag."""
        with self._lock:
            for t in self._tasks:
                if t.id == loop_id and t.type == "loop":
                    if remaining is not None:
                        t.params["remaining"] = max(1, remaining)
                    if infinite is not None:
                        t.params["infinite"] = infinite
                    return True
        return False

    def add_child_to_loop(self, loop_id: str, task_id: str) -> bool:
        """Move a pending task into a loop's children list."""
        with self._lock:
            loop = None
            task_idx = None
            for t in self._tasks:
                if t.id == loop_id and t.type == "loop":
                    loop = t
                if t.id == task_id:
                    task_idx = t
            if not loop or not task_idx or task_idx.type == "loop":
                return False
            # Remove from queue and add to loop's children
            self._tasks = deque(t for t in self._tasks if t.id != task_id)
            loop.children.append(task_idx)
            return True

    def remove_child_from_loop(self, loop_id: str, child_id: str) -> bool:
        """Remove a child from a loop (deletes it entirely)."""
        with self._lock:
            for i, t in enumerate(self._tasks):
                if t.id == loop_id and t.type == "loop":
                    for j, child in enumerate(t.children):
                        if child.id == child_id:
                            t.children.pop(j)
                            return True
        return False

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
            self._broadcast({"event": "queue_idle", "state": self.get_state()})
            _report_progress(0, 0, "idle", "")

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

            # Handle loop tasks: unfold one iteration, don't execute
            if task.type == "loop":
                self._unfold_loop(task)
                continue

            self.queue._current = task
            task.status = "active"
            task.started_at = time.time()
            self.queue._broadcast({"event": "task_started", "task": task.to_dict(), "state": self.queue.get_state()})

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
                cancel_detail = task.params.get("quest_name", "") or task.type
                # Preserve last progress values so emu manager can show partial bar
                prev = task.progress or {}
                _report_progress(
                    prev.get("current", 0), prev.get("total", 0),
                    "cancelled", f"{cancel_detail} — Cancelled"
                )
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

    def _unfold_loop(self, task: Task):
        """Handle a loop task: unfold one iteration or discard if done."""
        infinite = task.params.get("infinite", False)
        remaining = task.params.get("remaining", 0)

        if (infinite or remaining > 0) and task.children:
            if not infinite:
                task.params["remaining"] = remaining - 1
            # Deep-copy children with fresh IDs
            copies = []
            for child in task.children:
                c = copy.deepcopy(child)
                c.id = uuid.uuid4().hex[:8]
                c.status = "pending"
                c.result = None
                c.progress = None
                c.started_at = None
                c.finished_at = None
                copies.append(c)
            with self.queue._lock:
                for item in reversed(copies + [task]):
                    self.queue._tasks.appendleft(item)
            logger.info(f"Loop {task.id} unfolded: {'∞' if infinite else remaining - 1} remaining, {len(copies)} children inserted")
        else:
            logger.info(f"Loop {task.id} finished (remaining=0)")

        self.queue._broadcast({"event": "state_updated", "state": self.queue.get_state()})

    def _execute(self, task: Task) -> dict:
        match task.type:
            case "operation":
                quests = [(tuple(q["quest"]), q["count"]) for q in task.params["quests"]]
                apple_total = task.params.get("apple_total", 0)
                apple_kind = ["gold", "silver", "bronze", "copper", "quartz"].index(
                    task.params.get("apple_kind", "gold")
                )
                normal_attack_only = task.params.get("normal_attack_only", False)
                total = sum(q["count"] for q in task.params["quests"])
                quest_name = task.params.get("quest_name", "")
                logger.info(f"Starting operation: quests={quests}, apples={apple_total}, normal_attack_only={normal_attack_only}")
                fgoKernel.Turn.normalAttackOnly = normal_attack_only
                try:
                    op = fgoKernel.Operation(quests, apple_total, apple_kind)
                    # Inject progress callback
                    op.on_progress = lambda op_inst: _report_progress(
                        op_inst.battleCount, total, "running", quest_name
                    )
                    _report_progress(0, total, "running", quest_name)
                    op()
                    _report_progress(total, total, "done", "Complete")
                finally:
                    fgoKernel.Turn.normalAttackOnly = False
                return {"battle_count": getattr(op, 'battleCount', 0)}
            case "battle":
                logger.info("Starting battle")
                _report_progress(0, 0, "running", "Battle in progress")
                fgoKernel.Battle()()
                return {}
            case "wait":
                minutes = task.params.get("minutes", 1)
                logger.info(f"Waiting {minutes} minutes")
                total_seconds = minutes * 60
                elapsed = 0
                while elapsed < total_seconds:
                    remaining = (total_seconds - elapsed) // 60
                    _report_progress(elapsed, total_seconds, "running", f"Waiting — {remaining} min left")
                    chunk = min(30, total_seconds - elapsed)
                    schedule.sleep(chunk)
                    elapsed += chunk
                _report_progress(total_seconds, total_seconds, "done", "Wait complete")
                return {"waited_minutes": minutes}
            case "stop_emulator":
                logger.info("Stopping emulator")
                _report_progress(0, 0, "running", "Stopping emulator")
                self._call_emu_manager("stop")
                # Reset device to disconnected state (stale handles would crash)
                fgoDevice.device = fgoDevice.Device()
                return {"stopped": True}
            case "start_emulator":
                logger.info("Starting emulator")
                _report_progress(0, 0, "running", "Starting emulator")
                self._call_emu_manager("launch")
                # Wait for emulator to be ready, then reconnect device
                self._wait_and_reconnect(timeout=120)
                return {"started": True}
            case "eat_apple":
                apple_kind = task.params.get("apple_kind", "gold")
                logger.info(f"Eating apple: {apple_kind}")
                kind_index = ["gold", "silver", "bronze", "copper", "quartz"].index(apple_kind)
                # Create a minimal Main instance to use eatApple
                m = fgoKernel.Main(appleTotal=1, appleKind=kind_index)
                m.eatApple()
                return {"apple_kind": apple_kind}
            case _:
                raise ValueError(f"Unknown task type: {task.type}")

    def _call_emu_manager(self, action: str):
        """Call emu manager to start/stop the emulator instance."""
        if not _emu_manager_url:
            raise RuntimeError("Emu manager URL not configured")
        try:
            req = urllib.request.Request(
                f"{_emu_manager_url}/api/instances/{_instance_index}/{action}",
                data=b"",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            raise RuntimeError(f"Failed to {action} emulator: {e}")

    def _wait_for_device(self, timeout: int = 120):
        """Wait until the device becomes available after emulator start."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            schedule.sleep(5)
            if fgoDevice.device.available:
                logger.info("Device is ready")
                return
        raise RuntimeError(f"Device not ready after {timeout}s")

    def _wait_and_reconnect(self, timeout: int = 120):
        """Wait for emulator to boot, reconnect device, and verify screenshot works."""
        pending = getattr(fgoDevice, '_pending_device_name', None)
        if not pending:
            raise RuntimeError("No device name configured for reconnection")
        deadline = time.time() + timeout
        # Phase 1: reconnect device
        while time.time() < deadline:
            time.sleep(5)
            _report_progress(0, 0, "running", "Waiting for emulator...")
            try:
                fgoDevice.device = fgoDevice.Device(pending)
                logger.info("Device reconnected: %s", fgoDevice.device.name)
                break
            except Exception:
                continue
        else:
            raise RuntimeError(f"Device not ready after {timeout}s")
        # Phase 2: wait until screenshot succeeds
        while time.time() < deadline:
            _report_progress(0, 0, "running", "Waiting for screen...")
            try:
                img = fgoDevice.device.screenshot()
                if img is not None and img.size > 0:
                    logger.info("Screenshot verified — emulator fully ready")
                    return
            except Exception:
                pass
            time.sleep(3)
        raise RuntimeError(f"Screenshot not available after {timeout}s")


# Module-level singleton
task_queue = TaskQueue()
task_worker = TaskWorker(task_queue)


# Auto-battle: one-off battle execution outside the queue
_auto_battle_lock = threading.Lock()
_auto_battle_active = False


def is_auto_battle_active() -> bool:
    return _auto_battle_active


def cancel_auto_battle():
    """Cancel a running auto-battle by triggering schedule.stop()."""
    if _auto_battle_active:
        schedule.stop('Auto battle cancelled')


def run_auto_battle(broadcast: Callable):
    """Run a one-off Battle() in a new thread. Broadcasts start/finish events."""
    global _auto_battle_active
    with _auto_battle_lock:
        if _auto_battle_active or task_queue.is_busy():
            return False
        _auto_battle_active = True

    def _run():
        global _auto_battle_active
        broadcast({"event": "auto_battle_started", "state": task_queue.get_state()})
        try:
            schedule.reset()
            fgoKernel.Battle()()
            logger.info("Auto battle completed")
        except ScriptStop as e:
            logger.info(f"Auto battle cancelled: {e}")
        except Exception:
            logger.exception("Auto battle failed")
        finally:
            _auto_battle_active = False
            broadcast({"event": "auto_battle_finished", "state": task_queue.get_state()})

    threading.Thread(target=_run, daemon=True, name="AutoBattle").start()
    return True
