"""LDOpenGL — fast screenshot capture via LDPlayer's shared memory interface.

LDPlayer exposes emulator framebuffer data through a shared memory mechanism
(via the ldopengl.dll library). This module reads the framebuffer directly,
which is significantly faster than ADB-based screenshot methods (~5-15ms vs ~500ms).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Windows API constants
FILE_MAP_READ = 0x0004


class LDOpenGL:
    """Capture screenshots from LDPlayer via shared memory.

    LDPlayer creates a shared memory section named "ldopengl{index}" that
    contains the framebuffer in BGRA format. The first 8 bytes are width and
    height as 32-bit integers, followed by pixel data.
    """

    def __init__(self, install_dir: Path, index: int):
        self.install_dir = install_dir
        self.index = index
        self._shared_mem_name = f"ldopengl{index}"

    def screenshot(self) -> np.ndarray | None:
        """Capture a screenshot from the emulator's shared memory.

        Returns:
            BGR numpy array (H, W, 3) or None if capture fails.
        """
        try:
            return self._read_shared_memory()
        except Exception as e:
            logger.error("LDOpenGL screenshot failed (index=%d): %s", self.index, e)
            return None

    def _read_shared_memory(self) -> np.ndarray | None:
        """Read framebuffer from Windows shared memory."""
        kernel32 = ctypes.windll.kernel32

        # Open the shared memory mapping
        handle = kernel32.OpenFileMappingW(
            FILE_MAP_READ,
            False,
            self._shared_mem_name,
        )
        if not handle:
            logger.debug("Cannot open shared memory '%s' — instance may not be running", self._shared_mem_name)
            return None

        try:
            # Map view of the file mapping
            ptr = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
            if not ptr:
                logger.error("MapViewOfFile failed for '%s'", self._shared_mem_name)
                return None

            try:
                # Read header: width (4 bytes) + height (4 bytes)
                header = (ctypes.c_uint32 * 2).from_address(ptr)
                width = header[0]
                height = header[1]

                if width == 0 or height == 0:
                    logger.debug("Shared memory header reports 0x0 — no frame available")
                    return None

                # Read pixel data (BGRA, 4 bytes per pixel)
                data_offset = 8  # skip 8-byte header
                data_size = width * height * 4
                buffer = (ctypes.c_char * data_size).from_address(ptr + data_offset)

                # Convert to numpy array
                img = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4)

                # Convert BGRA to BGR (drop alpha channel) and flip vertically
                # LDPlayer stores framebuffer bottom-up
                img_bgr = img[::-1, :, :3].copy()

                return img_bgr

            finally:
                kernel32.UnmapViewOfFile(ptr)
        finally:
            kernel32.CloseHandle(handle)

    def is_available(self) -> bool:
        """Check if the shared memory is accessible (instance is running with OpenGL)."""
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenFileMappingW(
                FILE_MAP_READ,
                False,
                self._shared_mem_name,
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
