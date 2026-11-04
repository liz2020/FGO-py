"""Unit tests for emu.ldplayer module with mocked ldconsole output."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from emu.ldplayer import LDConsole, LDPlayerBackend, LDPlayerMultiBackend, _detect_install_dir, _detect_install_dirs
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

    def test_returns_none_when_not_found(self):
        with patch.dict("sys.modules", {"winreg": None}), patch("emu.ldplayer.Path.is_file", return_value=False):
            assert _detect_install_dir() is None

    def test_fallback_paths_not_exist(self):
        """When no registry and no fallback paths exist, returns None."""
        with patch("emu.ldplayer.Path.is_file", return_value=False):
            assert _detect_install_dirs() == []

    def test_fallback_finds_all_versions_without_registry(self):
        paths = [Path(r"C:\leidian") / f"LDPlayer{v}" for v in ("14", "9", "4")]
        with patch.dict("sys.modules", {"winreg": None}), patch(
            "emu.ldplayer.Path.is_file", autospec=True,
            side_effect=lambda p: p.parent in paths,
        ):
            assert _detect_install_dirs() == [p.resolve() for p in paths]

    def test_registry_and_fallback_are_deduplicated(self, tmp_path):
        (tmp_path / "ldconsole.exe").touch()
        winreg = MagicMock()
        winreg.KEY_READ = 0x20019
        winreg.KEY_WOW64_64KEY = 0x100
        winreg.KEY_WOW64_32KEY = 0x200
        winreg.QueryValueEx.return_value = (str(tmp_path), 1)
        with patch.dict("sys.modules", {"winreg": winreg}), patch(
            "emu.ldplayer.Path.is_file", autospec=True,
            side_effect=lambda p: p.parent == tmp_path or p.parent == Path(r"C:\leidian") / "LDPlayer4",
        ):
            assert _detect_install_dirs() == [tmp_path.resolve(), (Path(r"C:\leidian") / "LDPlayer4").resolve()]
        views = {call.args[3] for call in winreg.OpenKey.call_args_list}
        assert winreg.KEY_READ | winreg.KEY_WOW64_32KEY in views
        assert winreg.KEY_READ | winreg.KEY_WOW64_64KEY in views


class TestLDPlayerMultiBackend:
    def _make_backend(self, tmp_path):
        paths = [tmp_path / f"LDPlayer{version}" for version in ("14", "9", "4")]
        for path in paths:
            path.mkdir()
            (path / "ldconsole.exe").touch()
        with patch("emu.ldplayer._detect_install_dirs", return_value=paths):
            return LDPlayerMultiBackend()

    def test_lists_duplicate_indices_and_names_from_all_installations(self, tmp_path):
        backend = self._make_backend(tmp_path)
        with patch.object(LDConsole, "_run", return_value=SAMPLE_LIST2_SINGLE):
            instances = backend.list_instances()
            repeated = backend.list_instances()
        assert [i.index for i in instances] == [0, 1, 2]
        assert [i.index for i in repeated] == [0, 1, 2]
        assert [i.local_index for i in instances] == [0, 0, 0]
        assert [i.emulator_name for i in instances] == ["LDPlayer 14", "LDPlayer 9", "LDPlayer 4"]
        assert len({i.install_dir for i in instances}) == 3
        assert [i.name for i in instances] == ["fgo"] * 3
        assert [i.adb_serial for i in instances] == ["127.0.0.1:5555"] * 3
        assert len(backend.detect_all()) == 3

    @pytest.mark.parametrize("method,args,command", [
        ("launch", (), ("launch", "--index", "0")),
        ("stop", (), ("quit", "--index", "0")),
        ("list_apps", (), ("adb", "--index", "0", "--command", "shell pm list packages")),
        ("launch_app", ("com.bilibili.fatego",), ("runapp", "--index", "0", "--packagename", "com.bilibili.fatego")),
        ("kill_app", ("com.bilibili.fatego",), ("killapp", "--index", "0", "--packagename", "com.bilibili.fatego")),
    ])
    def test_controls_route_to_correct_console(self, tmp_path, method, args, command):
        backend = self._make_backend(tmp_path)
        with patch.object(LDConsole, "_run", return_value=SAMPLE_LIST2_SINGLE):
            backend.list_instances()
        console, index = backend.resolve_instance(2)
        assert console.install_dir.name == "LDPlayer4"
        assert index == 0
        with patch.object(console, "_run", return_value="") as run:
            getattr(backend, method)(2, *args)
        run.assert_called_once_with(*command)

    def test_screenshot_uses_local_index_and_installation(self, tmp_path):
        backend = self._make_backend(tmp_path)
        with patch.object(LDConsole, "_run", return_value=SAMPLE_LIST2_OUTPUT):
            instances = backend.list_instances()
            target = next(i for i in instances if i.emulator_name == "LDPlayer 9" and i.local_index == 0)
            with patch("emu.ldopengl.LDOpenGL") as opengl:
                backend.screenshot(target.index)
            opengl.assert_called_once_with(target.install_dir, 0, pid=79004, width=0, height=0)

    def test_ids_survive_list_changes(self, tmp_path):
        backend = self._make_backend(tmp_path)
        with patch.object(LDConsole, "_run", return_value=SAMPLE_LIST2_SINGLE):
            original = backend.list_instances()
        with patch.object(LDConsole, "_run", return_value=SAMPLE_LIST2_OUTPUT):
            updated = backend.list_instances()
        assert len({i.index for i in updated}) == 9
        for instance in original:
            match = next(i for i in updated if i.install_dir == instance.install_dir and i.local_index == 0)
            assert match.index == instance.index

    def test_one_failed_installation_does_not_hide_others(self, tmp_path, caplog):
        backend = self._make_backend(tmp_path)
        with patch.object(LDConsole, "_run", return_value=SAMPLE_LIST2_SINGLE), patch.object(
            backend._backends[1], "list_instances", side_effect=OSError("Unavailable"),
        ):
            instances = backend.list_instances()
        assert [i.emulator_name for i in instances] == ["LDPlayer 14", "LDPlayer 4"]
        assert "Could not list instances" in caplog.text

    def test_unknown_instance_is_rejected(self, tmp_path):
        backend = self._make_backend(tmp_path)
        with patch.object(LDConsole, "_run", return_value=""), pytest.raises(ValueError, match="not found"):
            backend.launch(99)


def test_device_uses_selected_installation_and_preserves_manager_id(monkeypatch, tmp_path):
    import ctypes
    import importlib.util
    import sys
    from types import ModuleType

    monkeypatch.setattr(sys, "path", sys.path.copy())
    for name in ("fgoAndroid", "fgoDetect", "fgoLogging", "fgoSchedule"):
        module = ModuleType(name)
        module.Android = MagicMock()
        module.setup = MagicMock()
        module.getLogger = MagicMock()
        module.schedule = MagicMock()
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("FGO_LDPLAYER_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("FGO_LDPLAYER_LOCAL_INDEX", "0")
    monkeypatch.setattr(ctypes, "windll", MagicMock(), raising=False)
    spec = importlib.util.spec_from_file_location(
        "test_fgo_device", Path(__file__).parent.parent / "FGO-py" / "fgoDevice.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    console = MagicMock(install_dir=tmp_path)
    console.list_instances.return_value = [{
        "index": 0, "is_running": True, "width": 1280, "height": 720,
        "pid": 123, "bind_window_handle": 1,
    }]
    with patch("emu.ldplayer.LDPlayerBackend") as backend, patch("emu.ldopengl.LDOpenGL") as opengl, patch.object(
        module.LDPlayerDevice, "_detect_fgo_package", return_value="com.bilibili.fatego",
    ):
        backend.return_value._ensure_console.return_value = console
        device = module.LDPlayerDevice(7)
    backend.assert_called_once_with(tmp_path)
    opengl.assert_called_once_with(tmp_path, 0, pid=123, width=1280, height=720)
    assert device._index == 0
    assert device.name == "ldplayer:7"
    module.device = device
    assert module.configuredLDPlayerIndex() == 7
