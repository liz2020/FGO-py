"""Script registry — manages automation script registration and process lifecycle."""

from __future__ import annotations

import fnmatch
import logging
import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from emu.models import AutomationScript, ScriptProcess

logger = logging.getLogger(__name__)


class ScriptRegistry:
    """Manages registered automation scripts and their running processes.

    Each script can target multiple emulator instances simultaneously,
    with one subprocess per instance.
    """

    def __init__(self):
        self._scripts: dict[str, AutomationScript] = {}
        self._processes: dict[str, ScriptProcess] = {}  # key: "{script_name}:{index}"
        self._lifecycle_lock = threading.RLock()

    def register(self, script: AutomationScript) -> None:
        """Register an automation script."""
        self._scripts[script.name] = script
        logger.info("Registered script: %s (filter: %s)", script.name, script.package_filter)

    def unregister(self, name: str) -> None:
        """Unregister a script and stop all its processes."""
        if name in self._scripts:
            # Stop all running processes for this script
            to_remove = [k for k in self._processes if k.startswith(f"{name}:")]
            for key in to_remove:
                self.stop(name, int(key.split(":")[1]))
            del self._scripts[name]

    @property
    def scripts(self) -> list[AutomationScript]:
        """List all registered scripts."""
        return list(self._scripts.values())

    def get_script(self, name: str) -> AutomationScript | None:
        """Get a script by name."""
        return self._scripts.get(name)

    def matching_scripts(self, package_name: str) -> list[AutomationScript]:
        """Find scripts whose package_filter matches the given package name."""
        return [
            s for s in self._scripts.values()
            if fnmatch.fnmatch(package_name, s.package_filter)
        ]

    def _process_key(self, script_name: str, index: int) -> str:
        return f"{script_name}:{index}"

    def port_for(self, script_name: str, index: int) -> int:
        """Calculate the port for a script targeting a specific instance."""
        script = self._scripts.get(script_name)
        if script is None:
            raise ValueError(f"Unknown script: {script_name}")
        return script.base_port + index

    def start(
        self,
        script_name: str,
        index: int,
        adb_serial: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> ScriptProcess:
        with self._lifecycle_lock:
            return self._start(script_name, index, adb_serial, cwd, env)

    def _start(
        self,
        script_name: str,
        index: int,
        adb_serial: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> ScriptProcess:
        """Start a script process targeting a specific emulator instance.

        Args:
            script_name: Name of the registered script.
            index: Emulator instance index.
            adb_serial: ADB serial for the instance (e.g. '127.0.0.1:5555').
            cwd: Working directory for the subprocess.
            env: Installation-specific overrides for the subprocess environment.

        Returns:
            ScriptProcess with process info.
        """
        key = self._process_key(script_name, index)

        # If already running, return existing
        existing = self._processes.get(key)
        if existing and existing.status == "running":
            # Check if process is still alive
            if existing.pid and self._is_alive(existing.pid):
                return existing
            # Process died — clean up
            existing.status = "stopped"

        script = self._scripts.get(script_name)
        if script is None:
            raise ValueError(f"Unknown script: {script_name}")

        port = self.port_for(script_name, index)
        if owners := self._port_owners(port):
            raise RuntimeError(
                f"Port {port} is already occupied (PIDs: {owners}). "
                "Use Force kill if this instance's FGO-py is stuck."
            )
        command = script.start_command.format(
            serial=adb_serial,
            port=port,
            index=index,
        )

        logger.info("Starting %s for instance %d: %s", script_name, index, command)

        try:
            # Use DEVNULL to avoid blocking on full pipe buffers
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                env={**os.environ, **env} if env is not None else None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            process = ScriptProcess(
                script_name=script_name,
                instance_index=index,
                port=port,
                pid=proc.pid,
                status="running",
            )
            self._processes[key] = process
            return process
        except Exception as e:
            logger.error("Failed to start %s for instance %d: %s", script_name, index, e)
            process = ScriptProcess(
                script_name=script_name,
                instance_index=index,
                port=port,
                status="error",
            )
            self._processes[key] = process
            return process

    def stop(self, script_name: str, index: int) -> bool:
        """Stop a running script process."""
        key = self._process_key(script_name, index)
        process = self._processes.get(key)
        if process is None or process.status != "running":
            return False

        if process.pid:
            try:
                self._kill_process(process.pid)
                logger.info("Stopped %s for instance %d (pid=%d)", script_name, index, process.pid)
            except Exception as e:
                logger.error("Failed to stop pid %d: %s", process.pid, e)
                return False

        process.status = "stopped"
        process.pid = None
        return True

    def get_running(self, script_name: str, index: int | None = None) -> list[ScriptProcess]:
        """Get running processes for a script, optionally filtered by instance."""
        results = []
        for key, proc in self._processes.items():
            if proc.script_name == script_name and proc.status == "running":
                if index is None or proc.instance_index == index:
                    # Verify still alive
                    if proc.pid and self._is_alive(proc.pid):
                        results.append(proc)
                    else:
                        proc.status = "stopped"
        return results

    @staticmethod
    def _port_owners(port: int) -> set[int | None]:
        return {
            connection.pid for connection in psutil.net_connections(kind="tcp")
            if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port
        }

    @staticmethod
    def _argument(args: list[str], *names: str) -> str | None:
        value = None
        for i, arg in enumerate(args):
            for name in names:
                if arg == name:
                    value = args[i + 1] if i + 1 < len(args) else None
                if arg.startswith(name + "="):
                    value = arg.split("=", 1)[1]
        return value

    def _find_script_processes(
        self, script_name: str, index: int, adb_serial: str, cwd: Path,
    ) -> list[psutil.Process]:
        script = self.get_script(script_name)
        if script is None:
            raise ValueError(f"Unknown script: {script_name}")
        command = shlex.split(script.start_command.format(
            index=index, port=self.port_for(script_name, index), serial=adb_serial,
        ), posix=sys.platform != "win32")
        entry = next((arg.strip('"') for arg in command if arg.strip('"').endswith(".py")), None)
        device = self._argument(command, "--device", "-d")
        if entry is None or device is None:
            raise ValueError("Force kill requires a Python script with an explicit device argument")
        expected_path = (cwd / entry).resolve()
        port = str(self.port_for(script_name, index))
        matches = []
        for process in psutil.process_iter(["cmdline"]):
            args = process.info["cmdline"] or []
            if (
                len(args) < 2 or not Path(args[0]).stem.casefold().startswith("python")
                or Path(args[1]).name != expected_path.name
                or self._argument(args[2:], "--port", "-p") != port
                or self._argument(args[2:], "--device", "-d") != device
            ):
                continue
            try:
                entry_path = Path(args[1])
                if entry_path.is_absolute():
                    same_script = entry_path.resolve() == expected_path
                else:
                    # fgo.py changes cwd to its own directory after starting.
                    process_cwd = Path(process.cwd()).resolve()
                    same_script = (
                        (process_cwd / entry_path).resolve() == expected_path
                        or process_cwd == expected_path.parent and entry_path == Path(entry)
                    )
                if same_script:
                    matches.append(process)
            except psutil.NoSuchProcess:
                logger.debug("Process %d exited during force-kill discovery", process.pid)
            except psutil.AccessDenied as exc:
                raise RuntimeError(f"Cannot verify script identity for PID {process.pid}") from exc
        return matches

    def force_stop(
        self, script_name: str, index: int, adb_serial: str, cwd: Path,
    ) -> list[int]:
        """Kill verified script processes, including ones missing from the registry."""
        with self._lifecycle_lock:
            return self._force_stop(script_name, index, adb_serial, cwd)

    def _force_stop(
        self, script_name: str, index: int, adb_serial: str, cwd: Path,
    ) -> list[int]:
        processes = self._find_script_processes(script_name, index, adb_serial, cwd)
        pids = {process.pid for process in processes}
        port = self.port_for(script_name, index)
        if unrelated := self._port_owners(port) - pids:
            raise RuntimeError(f"Refusing to kill: port {port} has an unverified owner (PIDs: {unrelated})")
        for process in reversed(processes):
            try:
                logger.warning("Force-killing %s on instance %d (pid=%d)", script_name, index, process.pid)
                process.kill()
            except psutil.NoSuchProcess:
                logger.debug("Process %d already exited", process.pid)
            except psutil.AccessDenied as exc:
                raise RuntimeError(f"Permission denied killing PID {process.pid}") from exc
        _, alive = psutil.wait_procs(processes, timeout=5)
        if alive:
            raise RuntimeError(f"Script processes did not exit: {[process.pid for process in alive]}")
        if owners := self._port_owners(port):
            raise RuntimeError(f"Port {port} is still occupied (PIDs: {owners})")
        if self._find_script_processes(script_name, index, adb_serial, cwd):
            raise RuntimeError("Matching script processes are still running; force kill did not finish")
        process = self._processes.get(self._process_key(script_name, index))
        if process is not None:
            process.status = "stopped"
            process.pid = None
        return sorted(pids)

    def all_processes(self) -> list[ScriptProcess]:
        """Get all tracked processes (running or stopped)."""
        return list(self._processes.values())

    @staticmethod
    def _is_alive(pid: int) -> bool:
        """Check if a process is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError, SystemError):
            return False

    @staticmethod
    def _kill_process(pid: int) -> None:
        """Kill a process and its children."""
        import signal

        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            import os
            os.killpg(os.getpgid(pid), signal.SIGTERM)
