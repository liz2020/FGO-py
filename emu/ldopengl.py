"""LDOpenGL — fast screenshot capture via LDPlayer's ldopengl64.dll.

LDPlayer ships ldopengl64.dll which exposes a C++ class IScreenShotClass
with a factory function CreateScreenShotInstance(index, pid). The class
provides a cap() method that returns a pointer to raw BGR pixel data
(height × width × 3 bytes, bottom-up).

Reference: MaaXYZ/EmulatorExtras LD/dnopengl/dnopengl.h
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class LDOpenGL:
    """Capture screenshots from LDPlayer via ldopengl64.dll.

    Uses the CreateScreenShotInstance factory to get an IScreenShotClass*,
    then calls cap() via the C++ vtable to get raw pixel data.
    """

    def __init__(self, install_dir: Path, index: int, pid: int = 0, width: int = 0, height: int = 0):
        """
        Args:
            install_dir: LDPlayer installation directory containing ldopengl64.dll.
            index: Emulator instance index.
            pid: Process ID of the emulator instance (from list2 output).
            width: Display width (from list2/list3 output).
            height: Display height (from list2/list3 output).
        """
        self.install_dir = install_dir
        self.index = index
        self.pid = pid
        self.width = width
        self.height = height
        self._dll = None
        self._instance = None

    def _load_dll(self):
        """Load ldopengl64.dll (or ldopengl.dll for 32-bit) and get CreateScreenShotInstance."""
        if self._dll is not None:
            return True

        # Prefer 64-bit DLL; fall back to 32-bit if running 32-bit Python
        import struct
        is_64bit = struct.calcsize("P") == 8
        candidates = (
            ["ldopengl64.dll", "ldopengl.dll"]
            if is_64bit
            else ["ldopengl.dll", "ldopengl64.dll"]
        )

        dll_path = None
        for name in candidates:
            p = self.install_dir / name
            if p.exists():
                dll_path = p
                break

        if dll_path is None:
            logger.error("No ldopengl DLL found in %s", self.install_dir)
            return False

        try:
            self._dll = ctypes.CDLL(str(dll_path))
            self._create_func = self._dll.CreateScreenShotInstance
            self._create_func.argtypes = [ctypes.c_uint, ctypes.c_uint]
            self._create_func.restype = ctypes.c_void_p
            self._ptr_size = 8 if is_64bit else 4
            logger.debug("Loaded %s", dll_path.name)
            return True
        except Exception as e:
            logger.error("Failed to load %s: %s", dll_path.name, e)
            self._dll = None
            return False

    def _get_instance(self) -> int | None:
        """Get or create the IScreenShotClass instance."""
        if self._instance is not None:
            return self._instance

        if not self._load_dll():
            return None

        ptr = self._create_func(self.index, self.pid)
        if not ptr:
            logger.error(
                "CreateScreenShotInstance returned null (index=%d, pid=%d)",
                self.index, self.pid,
            )
            return None

        self._instance = ptr
        return ptr

    def screenshot(self) -> np.ndarray | None:
        """Capture a screenshot from the emulator.

        Returns:
            BGR numpy array (H, W, 3) or None if capture fails.
        """
        try:
            return self._capture()
        except Exception as e:
            logger.error("LDOpenGL screenshot failed (index=%d): %s", self.index, e)
            return None

    def _capture(self) -> np.ndarray | None:
        """Call cap() on the IScreenShotClass instance via vtable."""
        instance_ptr = self._get_instance()
        if instance_ptr is None:
            return None

        if self.width == 0 or self.height == 0:
            logger.error("Width/height not set — cannot interpret pixel buffer")
            return None

        # IScreenShotClass vtable layout (MSVC):
        #   [0] destructor (or RTTI)
        #   [1] cap() -> void*
        #   [2] release() -> void
        # Read the vtable pointer (first ptr_size bytes of the object)
        vtable_ptr = ctypes.c_void_p.from_address(instance_ptr).value
        if not vtable_ptr:
            logger.error("vtable pointer is null")
            return None

        # cap() is at vtable[1] — pointer size depends on architecture
        ps = getattr(self, '_ptr_size', 8)
        cap_func_ptr = ctypes.c_void_p.from_address(vtable_ptr + ps).value
        if not cap_func_ptr:
            logger.error("cap function pointer is null")
            return None

        # Call cap(): takes `this` pointer, returns void*
        cap_func_type = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)
        cap_func = cap_func_type(cap_func_ptr)
        data_ptr = cap_func(instance_ptr)

        if not data_ptr:
            logger.debug("cap() returned null — no frame available")
            return None

        # Read pixel data: BGR, 3 bytes per pixel, bottom-up
        data_size = self.width * self.height * 3
        buffer = (ctypes.c_char * data_size).from_address(data_ptr)
        img = np.frombuffer(buffer, dtype=np.uint8).reshape(self.height, self.width, 3)

        # Flip vertically (framebuffer is bottom-up)
        return img[::-1].copy()

    def release(self):
        """Release the IScreenShotClass instance."""
        if self._instance is None:
            return

        try:
            # release() is at vtable[2]
            vtable_ptr = ctypes.c_void_p.from_address(self._instance).value
            ps = getattr(self, '_ptr_size', 8)
            release_func_ptr = ctypes.c_void_p.from_address(vtable_ptr + 2 * ps).value
            if release_func_ptr:
                release_func_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
                release_func = release_func_type(release_func_ptr)
                release_func(self._instance)
        except Exception as e:
            logger.debug("release() failed: %s", e)
        finally:
            self._instance = None

    def is_available(self) -> bool:
        """Check if the DLL can be loaded and an instance created."""
        return self._get_instance() is not None

    def __del__(self):
        self.release()
