"""Data models for the emulator manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class InstanceStatus(str, Enum):
    """Emulator instance lifecycle status."""

    STOPPED = "stopped"
    RUNNING = "running"
    LAUNCHING = "launching"
    STOPPING = "stopping"
    UNKNOWN = "unknown"


@dataclass
class EmulatorInfo:
    """Detected emulator installation on the host system."""

    name: str  # e.g. "LDPlayer 14"
    brand: str  # e.g. "ldplayer"
    version: str  # e.g. "14.0.3.1"
    install_dir: Path  # e.g. C:\leidian\LDPlayer14


@dataclass
class EmulatorInstance:
    """A single emulator instance (virtual device)."""

    index: int
    name: str
    status: InstanceStatus = InstanceStatus.UNKNOWN
    pid: int | None = None
    resolution: str = ""  # e.g. "1280x720"
    dpi: int = 0
    adb_serial: str = ""  # e.g. "127.0.0.1:5555"
    running_app: str = ""  # package name of foreground app
    emulator_brand: str = ""  # e.g. "ldplayer"

    @property
    def is_running(self) -> bool:
        return self.status == InstanceStatus.RUNNING


@dataclass
class AutomationScript:
    """A registered automation script that can target emulator instances."""

    name: str  # e.g. "FGO-py"
    package_filter: str  # glob match, e.g. "com.aniplex.fategrandorder*"
    base_port: int  # each instance gets base_port + offset
    start_command: str  # template with {serial}, {port}, {index}
    display_name: str = ""  # human-friendly name for UI

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name


@dataclass
class ScriptProcess:
    """A running instance of an automation script."""

    script_name: str
    instance_index: int
    port: int
    pid: int | None = None
    status: str = "stopped"  # "running", "stopped", "error"
