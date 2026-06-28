"""LDPlayer backend — wraps ldconsole CLI and Windows registry for instance management."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

from emu.base import EmulatorBackend
from emu.models import EmulatorInfo, EmulatorInstance, InstanceStatus

logger = logging.getLogger(__name__)

# Registry paths where LDPlayer stores its install location
_REGISTRY_PATHS = [
    (r"SOFTWARE\leidian\LDPlayer",),
    (r"SOFTWARE\leidian\LDPlayer9",),
    (r"SOFTWARE\leidian\LDPlayer4",),
]


def _detect_install_dir() -> Path | None:
    """Detect LDPlayer install directory from Windows registry."""
    try:
        import winreg
    except ImportError:
        return None

    for (key_path,) in _REGISTRY_PATHS:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, "InstallDir")
                    p = Path(value)
                    if p.exists() and (p / "ldconsole.exe").exists():
                        return p
            except (OSError, FileNotFoundError):
                continue

    # Fallback: check common install locations
    for candidate in [
        Path(r"C:\leidian\LDPlayer14"),
        Path(r"C:\leidian\LDPlayer9"),
        Path(r"C:\Program Files\leidian\LDPlayer14"),
        Path(r"C:\Program Files\leidian\LDPlayer9"),
    ]:
        if candidate.exists() and (candidate / "ldconsole.exe").exists():
            return candidate

    return None


class LDConsole:
    """Wrapper around ldconsole.exe CLI tool."""

    def __init__(self, install_dir: Path):
        self.install_dir = install_dir
        self.ldconsole = install_dir / "ldconsole.exe"
        if not self.ldconsole.exists():
            raise FileNotFoundError(f"ldconsole.exe not found at {self.ldconsole}")

    @classmethod
    def auto_detect(cls) -> LDConsole | None:
        """Auto-detect LDPlayer installation and return LDConsole instance."""
        install_dir = _detect_install_dir()
        if install_dir is None:
            return None
        return cls(install_dir)

    def _run(self, *args: str, timeout: float = 30) -> str:
        """Run ldconsole command and return stdout."""
        cmd = [str(self.ldconsole), *args]
        logger.debug("Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode != 0:
            logger.warning("ldconsole %s failed (rc=%d): %s", args[0], result.returncode, result.stderr.strip())
        return result.stdout

    def list_instances(self) -> list[dict]:
        """Parse `ldconsole list2` output into instance dicts.

        Output format: index,name,top_window_handle,bind_window_handle,is_running,pid,vbox_pid
        """
        output = self._run("list2")
        instances = []
        for line in output.strip().splitlines():
            parts = line.split(",")
            if len(parts) < 7:
                continue
            instances.append({
                "index": int(parts[0]),
                "name": parts[1],
                "top_window_handle": int(parts[2]),
                "bind_window_handle": int(parts[3]),
                "is_running": parts[4] == "1",
                "pid": int(parts[5]) if parts[5] != "-1" and parts[5] != "0" else None,
                "vbox_pid": int(parts[6]) if parts[6] != "-1" and parts[6] != "0" else None,
            })
        return instances

    def get_instance_prop(self, index: int, key: str) -> str:
        """Get a property of an instance via `ldconsole getprop`."""
        output = self._run("getprop", "--index", str(index), "--key", key)
        return output.strip()

    def get_instance_resolution(self, index: int) -> tuple[int, int, int]:
        """Get resolution (width, height, dpi) from instance config."""
        width = self.get_instance_prop(index, "resolution.width") or "0"
        height = self.get_instance_prop(index, "resolution.height") or "0"
        dpi = self.get_instance_prop(index, "resolution.dpi") or "0"
        try:
            return int(width), int(height), int(dpi)
        except ValueError:
            return 0, 0, 0

    def launch(self, index: int) -> bool:
        """Launch an instance."""
        self._run("launch", "--index", str(index))
        return True

    def stop(self, index: int) -> bool:
        """Stop an instance."""
        self._run("quit", "--index", str(index))
        return True

    def restart(self, index: int) -> bool:
        """Restart an instance (stop + launch)."""
        self.stop(index)
        self.launch(index)
        return True

    def adb_serial(self, index: int) -> str:
        """Get ADB connection string for an instance.

        LDPlayer uses sequential ports starting from 5555 for index 0,
        5557 for index 1, etc. (port = 5555 + index * 2)
        """
        port = 5555 + index * 2
        return f"127.0.0.1:{port}"

    def list_apps(self, index: int) -> list[str]:
        """List installed packages on an instance."""
        output = self._run("listpackages", "--index", str(index))
        return [line.strip() for line in output.strip().splitlines() if line.strip()]

    def launch_app(self, index: int, package_name: str) -> bool:
        """Launch an app on an instance."""
        self._run("runapp", "--index", str(index), "--packagename", package_name)
        return True

    def kill_app(self, index: int, package_name: str) -> bool:
        """Kill an app on an instance."""
        self._run("killapp", "--index", str(index), "--packagename", package_name)
        return True


class LDPlayerBackend(EmulatorBackend):
    """LDPlayer emulator backend implementation."""

    def __init__(self, install_dir: Path | None = None):
        if install_dir:
            self._install_dir = install_dir
            self._console = LDConsole(install_dir)
        else:
            self._install_dir = None
            self._console = None

    def _ensure_console(self) -> LDConsole | None:
        if self._console is None:
            self._console = LDConsole.auto_detect()
            if self._console:
                self._install_dir = self._console.install_dir
        return self._console

    def detect(self) -> EmulatorInfo | None:
        console = self._ensure_console()
        if console is None:
            return None
        # Try to determine version from directory name
        dir_name = console.install_dir.name  # e.g. "LDPlayer14"
        version = "".join(c for c in dir_name if c.isdigit()) or "unknown"
        return EmulatorInfo(
            name=f"LDPlayer {version}",
            brand="ldplayer",
            version=version,
            install_dir=console.install_dir,
        )

    def list_instances(self) -> list[EmulatorInstance]:
        console = self._ensure_console()
        if console is None:
            return []
        raw = console.list_instances()
        instances = []
        for item in raw:
            status = InstanceStatus.RUNNING if item["is_running"] else InstanceStatus.STOPPED
            inst = EmulatorInstance(
                index=item["index"],
                name=item["name"],
                status=status,
                pid=item["pid"],
                adb_serial=console.adb_serial(item["index"]),
                emulator_brand="ldplayer",
            )
            instances.append(inst)
        return instances

    def get_instance(self, index: int) -> EmulatorInstance | None:
        for inst in self.list_instances():
            if inst.index == index:
                return inst
        return None

    def launch(self, index: int) -> bool:
        console = self._ensure_console()
        return console.launch(index) if console else False

    def stop(self, index: int) -> bool:
        console = self._ensure_console()
        return console.stop(index) if console else False

    def restart(self, index: int) -> bool:
        console = self._ensure_console()
        return console.restart(index) if console else False

    def adb_serial(self, index: int) -> str:
        console = self._ensure_console()
        return console.adb_serial(index) if console else ""

    def list_apps(self, index: int) -> list[str]:
        console = self._ensure_console()
        return console.list_apps(index) if console else []

    def launch_app(self, index: int, package_name: str) -> bool:
        console = self._ensure_console()
        return console.launch_app(index, package_name) if console else False

    def kill_app(self, index: int, package_name: str) -> bool:
        console = self._ensure_console()
        return console.kill_app(index, package_name) if console else False

    def screenshot(self, index: int) -> np.ndarray | None:
        """Capture screenshot using LDOpenGL shared memory."""
        from emu.ldopengl import LDOpenGL

        console = self._ensure_console()
        if console is None:
            return None
        try:
            opengl = LDOpenGL(console.install_dir, index)
            return opengl.screenshot()
        except Exception as e:
            logger.error("Screenshot failed for instance %d: %s", index, e)
            return None

    @property
    def brand(self) -> str:
        return "ldplayer"

    @property
    def install_dir(self) -> Path | None:
        self._ensure_console()
        return self._install_dir
