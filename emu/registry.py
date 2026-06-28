"""Script registry — manages automation script registration and process lifecycle."""

from __future__ import annotations

import fnmatch
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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
    ) -> ScriptProcess:
        """Start a script process targeting a specific emulator instance.

        Args:
            script_name: Name of the registered script.
            index: Emulator instance index.
            adb_serial: ADB serial for the instance (e.g. '127.0.0.1:5555').
            cwd: Working directory for the subprocess.

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

    def all_processes(self) -> list[ScriptProcess]:
        """Get all tracked processes (running or stopped)."""
        return list(self._processes.values())

    @staticmethod
    def _is_alive(pid: int) -> bool:
        """Check if a process is still running."""
        try:
            import os
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
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
