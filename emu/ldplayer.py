"""LDPlayer backend — wraps ldconsole CLI and Windows registry for instance management."""

from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np

from emu.base import EmulatorBackend
from emu.models import EmulatorInfo, EmulatorInstance, InstanceStatus

logger = logging.getLogger(__name__)

# Registry paths where LDPlayer stores its install location
_REGISTRY_PATHS = [
    (r"SOFTWARE\leidian\LDPlayer",),
    (r"SOFTWARE\leidian\LDPlayer14",),
    (r"SOFTWARE\leidian\LDPlayer9",),
    (r"SOFTWARE\leidian\LDPlayer4",),
]


def _detect_install_dirs() -> list[Path]:
    """Find all installations, deduplicating registry and filesystem results."""
    installs: list[Path] = []

    def add(value: str | Path) -> None:
        path = Path(value)
        if (path / "ldconsole.exe").is_file() and path.resolve() not in installs:
            installs.append(path.resolve())

    try:
        import winreg
    except ImportError:
        winreg = None

    if winreg is not None:
        for (key_path,) in _REGISTRY_PATHS:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as key:
                            value, _ = winreg.QueryValueEx(key, "InstallDir")
                            add(value)
                    except OSError:
                        continue

    # Fallback: check common install locations
    for root in (Path(r"C:\leidian"), Path(r"C:\Program Files\leidian"),
                 Path(r"C:\Program Files (x86)\leidian")):
        for version in ("14", "9", "4"):
            add(root / f"LDPlayer{version}")
    return installs


def _detect_install_dir() -> Path | None:
    """Return the preferred installation for standalone device connections."""
    installs = _detect_install_dirs()
    return installs[0] if installs else None


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
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        # ldconsole on Chinese Windows outputs GBK; fall back to utf-8 then latin-1
        stdout_bytes = result.stdout
        if result.returncode != 0:
            stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            logger.warning("ldconsole %s failed (rc=%d): %s", args[0], result.returncode, stderr_text)
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return stdout_bytes.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return stdout_bytes.decode("utf-8", errors="replace")

    def list_instances(self) -> list[dict]:
        """Parse `ldconsole list2` output into instance dicts.

        Output format (LDPlayer 14):
            index,name,top_window_handle,bind_window_handle,is_running,pid,vbox_pid,width,height,dpi
        Older format (7 fields):
            index,name,top_window_handle,bind_window_handle,is_running,pid,vbox_pid
        """
        output = self._run("list2")
        instances = []
        for line in output.strip().splitlines():
            parts = line.split(",")
            if len(parts) < 7:
                continue
            item = {
                "index": int(parts[0]),
                "name": parts[1],
                "top_window_handle": int(parts[2]),
                "bind_window_handle": int(parts[3]),
                "is_running": parts[4] != "0",
                "pid": int(parts[5]) if parts[5] != "-1" and parts[5] != "0" else None,
                "vbox_pid": int(parts[6]) if parts[6] != "-1" and parts[6] != "0" else None,
                "width": int(parts[7]) if len(parts) > 7 else 0,
                "height": int(parts[8]) if len(parts) > 8 else 0,
                "dpi": int(parts[9]) if len(parts) > 9 else 0,
            }
            instances.append(item)
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
        output = self._run("adb", "--index", str(index), "--command", "shell pm list packages")
        return [
            line.removeprefix("package:").strip()
            for line in output.strip().splitlines()
            if line.startswith("package:")
        ]

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
            resolution = f"{item['width']}x{item['height']}" if item.get("width") else ""
            inst = EmulatorInstance(
                index=item["index"],
                name=item["name"],
                status=status,
                pid=item["pid"],
                resolution=resolution,
                dpi=item.get("dpi", 0),
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

    def resolve_instance(self, index: int) -> tuple[LDConsole | None, int]:
        return self._ensure_console(), index

    def launch(self, index: int) -> bool:
        console, index = self.resolve_instance(index)
        return console.launch(index) if console else False

    def stop(self, index: int) -> bool:
        console, index = self.resolve_instance(index)
        return console.stop(index) if console else False

    def restart(self, index: int) -> bool:
        console, index = self.resolve_instance(index)
        return console.restart(index) if console else False

    def adb_serial(self, index: int) -> str:
        console, index = self.resolve_instance(index)
        return console.adb_serial(index) if console else ""

    def list_apps(self, index: int) -> list[str]:
        console, index = self.resolve_instance(index)
        return console.list_apps(index) if console else []

    def launch_app(self, index: int, package_name: str) -> bool:
        console, index = self.resolve_instance(index)
        return console.launch_app(index, package_name) if console else False

    def kill_app(self, index: int, package_name: str) -> bool:
        console, index = self.resolve_instance(index)
        return console.kill_app(index, package_name) if console else False

    def screenshot(self, index: int) -> np.ndarray | None:
        """Capture screenshot using LDOpenGL DLL (CreateScreenShotInstance)."""
        from emu.ldopengl import LDOpenGL

        console, index = self.resolve_instance(index)
        if console is None:
            return None

        # Get instance info for pid and resolution
        raw = console.list_instances()
        item = next((i for i in raw if i["index"] == index), None)
        if item is None:
            return None

        pid = item.get("pid") or 0
        width = item.get("width") or 0
        height = item.get("height") or 0

        if not item.get("is_running"):
            logger.debug("Instance %d is not running", index)
            return None

        try:
            opengl = LDOpenGL(console.install_dir, index, pid=pid, width=width, height=height)
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


class LDPlayerMultiBackend(LDPlayerBackend):
    """Aggregate installations with stable, manager-local numeric instance IDs."""

    def __init__(self):
        super().__init__()
        self._backends = [LDPlayerBackend(path) for path in _detect_install_dirs()]
        self._routes: dict[int, tuple[LDConsole, int]] = {}
        self._ids: dict[tuple[Path, int], int] = {}
        self._route_lock = threading.RLock()

    def detect_all(self) -> list[EmulatorInfo]:
        return [info for backend in self._backends if (info := backend.detect())]

    def detect(self) -> EmulatorInfo | None:
        infos = self.detect_all()
        return infos[0] if infos else None

    def list_instances(self) -> list[EmulatorInstance]:
        with self._route_lock:
            snapshots = []
            for backend in self._backends:
                try:
                    snapshots.append((backend, backend.list_instances()))
                except (OSError, subprocess.SubprocessError, ValueError):
                    logger.exception("Could not list instances at %s", backend.install_dir)
            instances = []
            # Reserve the primary installation's native IDs before assigning others.
            for backend, items in snapshots:
                console = backend._ensure_console()
                info = backend.detect()
                if console is None or info is None:
                    continue
                for item in items:
                    key = (console.install_dir, item.index)
                    if key not in self._ids:
                        index = item.index if backend is self._backends[0] else 0
                        if index in self._routes:
                            index = max(self._routes) + 1
                        self._ids[key] = index
                        self._routes[index] = (console, item.index)
                    instances.append(replace(
                        item,
                        index=self._ids[key],
                        local_index=item.index,
                        install_dir=console.install_dir,
                        emulator_name=info.name,
                    ))
            return instances

    def resolve_instance(self, index: int) -> tuple[LDConsole, int]:
        with self._route_lock:
            if index not in self._routes:
                self.list_instances()
            if index not in self._routes:
                raise ValueError(f"Instance {index} not found")
            return self._routes[index]

    @property
    def install_dir(self) -> Path | None:
        info = self.detect()
        return info.install_dir if info else None
