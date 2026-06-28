"""Emulator Manager — a standalone service for managing Android emulator instances.

Provides:
- LDPlayer backend (ldconsole/ldopengl integration)
- REST API for instance lifecycle management
- WebSocket live status updates
- Reverse proxy for automation script UIs
"""

from emu.models import EmulatorInfo, EmulatorInstance, InstanceStatus

__all__ = ["EmulatorInfo", "EmulatorInstance", "InstanceStatus"]
