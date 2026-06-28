"""Abstract base class for emulator backends.

Designed for pluggability — LDPlayer is the first implementation,
but BlueStacks/MuMu can be added later without refactoring.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from emu.models import EmulatorInfo, EmulatorInstance


class EmulatorBackend(ABC):
    """Abstract interface for an emulator backend (LDPlayer, BlueStacks, etc.)."""

    @abstractmethod
    def detect(self) -> EmulatorInfo | None:
        """Detect if this emulator brand is installed. Returns info or None."""

    @abstractmethod
    def list_instances(self) -> list[EmulatorInstance]:
        """List all configured emulator instances."""

    @abstractmethod
    def get_instance(self, index: int) -> EmulatorInstance | None:
        """Get details for a specific instance by index."""

    @abstractmethod
    def launch(self, index: int) -> bool:
        """Launch an emulator instance. Returns True on success."""

    @abstractmethod
    def stop(self, index: int) -> bool:
        """Stop an emulator instance. Returns True on success."""

    @abstractmethod
    def restart(self, index: int) -> bool:
        """Restart an emulator instance. Returns True on success."""

    @abstractmethod
    def adb_serial(self, index: int) -> str:
        """Get the ADB serial string for an instance (e.g. '127.0.0.1:5555')."""

    @abstractmethod
    def list_apps(self, index: int) -> list[str]:
        """List installed package names on an instance."""

    @abstractmethod
    def launch_app(self, index: int, package_name: str) -> bool:
        """Launch an app by package name on an instance."""

    @abstractmethod
    def kill_app(self, index: int, package_name: str) -> bool:
        """Kill an app by package name on an instance."""

    @abstractmethod
    def screenshot(self, index: int) -> np.ndarray | None:
        """Capture a screenshot from an instance. Returns BGR numpy array or None."""

    @property
    @abstractmethod
    def brand(self) -> str:
        """Short identifier for this backend (e.g. 'ldplayer')."""

    @property
    @abstractmethod
    def install_dir(self) -> Path | None:
        """Installation directory, or None if not detected."""
