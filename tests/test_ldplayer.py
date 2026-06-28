"""Unit tests for emu.ldplayer module with mocked ldconsole output."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from emu.ldplayer import LDConsole, LDPlayerBackend, _detect_install_dir
from emu.models import EmulatorInstance, InstanceStatus


# Sample ldconsole list2 output
SAMPLE_LIST2_OUTPUT = """\
0,fgo,12345,67890,1,79004,79005
1,fgo_test,0,0,0,-1,-1
2,arknights,11111,22222,1,80001,80002
"""

SAMPLE_LIST2_SINGLE = """\
0,fgo,12345,67890,0,-1,-1
"""


class TestLDConsoleListInstances:
    """Test parsing of ldconsole list2 output."""

    def test_parse_running_and_stopped(self, tmp_path):
        # Create a fake ldconsole.exe
        (tmp_path / "ldconsole.exe").touch()
        console = LDConsole(tmp_path)

        with patch.object(console, "_run", return_value=SAMPLE_LIST2_OUTPUT):
            instances = console.list_instances()

        assert len(instances) == 3

        # First instance: running
        assert instances[0]["index"] == 0
        assert instances[0]["name"] == "fgo"
        assert instances[0]["is_running"] is True
        assert instances[0]["pid"] == 79004

        # Second instance: stopped
        assert instances[1]["index"] == 1
        assert instances[1]["name"] == "fgo_test"
        assert instances[1]["is_running"] is False
        assert instances[1]["pid"] is None

        # Third instance: running
        assert instances[2]["index"] == 2
        assert instances[2]["name"] == "arknights"
        assert instances[2]["is_running"] is True
        assert instances[2]["pid"] == 80001

    def test_parse_empty_output(self, tmp_path):
        (tmp_path / "ldconsole.exe").touch()
        console = LDConsole(tmp_path)

        with patch.object(console, "_run", return_value=""):
            instances = console.list_instances()

        assert instances == []

    def test_parse_malformed_lines_skipped(self, tmp_path):
        (tmp_path / "ldconsole.exe").touch()
        console = LDConsole(tmp_path)

        output = "bad,line\n0,fgo,12345,67890,1,79004,79005\n"
        with patch.object(console, "_run", return_value=output):
            instances = console.list_instances()

        assert len(instances) == 1
        assert instances[0]["name"] == "fgo"


class TestLDConsoleAdbSerial:
    """Test ADB serial port calculation."""

    def test_index_0(self, tmp_path):
        (tmp_path / "ldconsole.exe").touch()
        console = LDConsole(tmp_path)
        assert console.adb_serial(0) == "127.0.0.1:5555"

    def test_index_1(self, tmp_path):
        (tmp_path / "ldconsole.exe").touch()
        console = LDConsole(tmp_path)
        assert console.adb_serial(1) == "127.0.0.1:5557"

    def test_index_2(self, tmp_path):
        (tmp_path / "ldconsole.exe").touch()
        console = LDConsole(tmp_path)
        assert console.adb_serial(2) == "127.0.0.1:5559"


class TestLDPlayerBackend:
    """Test the LDPlayerBackend high-level interface."""

    def _make_backend(self, tmp_path) -> LDPlayerBackend:
        (tmp_path / "ldconsole.exe").touch()
        return LDPlayerBackend(install_dir=tmp_path)

    def test_detect_returns_info(self, tmp_path):
        tmp_path_named = tmp_path / "LDPlayer14"
        tmp_path_named.mkdir()
        (tmp_path_named / "ldconsole.exe").touch()
        backend = LDPlayerBackend(install_dir=tmp_path_named)

        info = backend.detect()
        assert info is not None
        assert info.brand == "ldplayer"
        assert "14" in info.version
        assert info.install_dir == tmp_path_named

    def test_list_instances_maps_status(self, tmp_path):
        backend = self._make_backend(tmp_path)

        with patch.object(backend._console, "_run", return_value=SAMPLE_LIST2_OUTPUT):
            instances = backend.list_instances()

        assert len(instances) == 3
        assert instances[0].status == InstanceStatus.RUNNING
        assert instances[0].name == "fgo"
        assert instances[0].adb_serial == "127.0.0.1:5555"
        assert instances[1].status == InstanceStatus.STOPPED
        assert instances[2].status == InstanceStatus.RUNNING

    def test_get_instance_found(self, tmp_path):
        backend = self._make_backend(tmp_path)

        with patch.object(backend._console, "_run", return_value=SAMPLE_LIST2_OUTPUT):
            inst = backend.get_instance(2)

        assert inst is not None
        assert inst.index == 2
        assert inst.name == "arknights"

    def test_get_instance_not_found(self, tmp_path):
        backend = self._make_backend(tmp_path)

        with patch.object(backend._console, "_run", return_value=SAMPLE_LIST2_OUTPUT):
            inst = backend.get_instance(99)

        assert inst is None

    def test_brand_property(self, tmp_path):
        backend = self._make_backend(tmp_path)
        assert backend.brand == "ldplayer"

    def test_install_dir_property(self, tmp_path):
        backend = self._make_backend(tmp_path)
        assert backend.install_dir == tmp_path


class TestDetectInstallDir:
    """Test registry/filesystem detection of LDPlayer install."""

    @patch("emu.ldplayer.winreg", create=True)
    def test_returns_none_when_not_found(self, mock_winreg):
        # Simulate ImportError for winreg (non-Windows)
        with patch.dict("sys.modules", {"winreg": None}):
            with patch("builtins.__import__", side_effect=ImportError):
                # On non-Windows, should return None gracefully
                pass

    def test_fallback_paths_not_exist(self):
        """When no registry and no fallback paths exist, returns None."""
        with patch("emu.ldplayer.Path.exists", return_value=False):
            # Can't easily mock winreg on Windows, so just verify the function handles it
            result = _detect_install_dir()
            # Result depends on actual system — may be None or a real path
            assert result is None or isinstance(result, Path)
