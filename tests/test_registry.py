"""Unit tests for emu.registry module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from emu.models import AutomationScript, ScriptProcess
from emu.registry import ScriptRegistry


@pytest.fixture
def registry():
    return ScriptRegistry()


@pytest.fixture
def fgo_script():
    return AutomationScript(
        name="FGO-py",
        package_filter="com.aniplex.fategrandorder*",
        base_port=15001,
        start_command="python fgo.py --web --device {serial} --port {port}",
    )


class TestScriptRegistration:
    def test_register_and_list(self, registry, fgo_script):
        registry.register(fgo_script)
        assert len(registry.scripts) == 1
        assert registry.scripts[0].name == "FGO-py"

    def test_get_script(self, registry, fgo_script):
        registry.register(fgo_script)
        assert registry.get_script("FGO-py") is fgo_script
        assert registry.get_script("nonexistent") is None

    def test_unregister(self, registry, fgo_script):
        registry.register(fgo_script)
        registry.unregister("FGO-py")
        assert len(registry.scripts) == 0

    def test_matching_scripts(self, registry, fgo_script):
        registry.register(fgo_script)

        # Should match JP and NA versions
        matches = registry.matching_scripts("com.aniplex.fategrandorder")
        assert len(matches) == 1

        matches = registry.matching_scripts("com.aniplex.fategrandorder.en")
        assert len(matches) == 1

        # Should not match unrelated packages
        matches = registry.matching_scripts("com.hypergryph.arknights")
        assert len(matches) == 0


class TestPortAllocation:
    def test_port_calculation(self, registry, fgo_script):
        registry.register(fgo_script)
        assert registry.port_for("FGO-py", 0) == 15001
        assert registry.port_for("FGO-py", 1) == 15002
        assert registry.port_for("FGO-py", 2) == 15003

    def test_port_for_unknown_script_raises(self, registry):
        with pytest.raises(ValueError, match="Unknown script"):
            registry.port_for("nonexistent", 0)


class TestProcessLifecycle:
    def test_start_creates_process(self, registry, fgo_script):
        registry.register(fgo_script)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            result = registry.start("FGO-py", 0, "127.0.0.1:5555")

        assert result.status == "running"
        assert result.pid == 12345
        assert result.port == 15001
        assert result.instance_index == 0

    def test_start_formats_command(self, registry, fgo_script):
        registry.register(fgo_script)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            registry.start("FGO-py", 2, "127.0.0.1:5559")

        call_args = mock_popen.call_args
        assert "127.0.0.1:5559" in call_args[0][0]
        assert "15003" in call_args[0][0]

    def test_stop_process(self, registry, fgo_script):
        registry.register(fgo_script)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            registry.start("FGO-py", 0, "127.0.0.1:5555")

        with patch.object(ScriptRegistry, "_is_alive", return_value=True):
            with patch.object(ScriptRegistry, "_kill_process") as mock_kill:
                result = registry.stop("FGO-py", 0)

        assert result is True
        mock_kill.assert_called_once_with(12345)

    def test_get_running_filters_dead_processes(self, registry, fgo_script):
        registry.register(fgo_script)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            registry.start("FGO-py", 0, "127.0.0.1:5555")

        # Process died
        with patch.object(ScriptRegistry, "_is_alive", return_value=False):
            running = registry.get_running("FGO-py")

        assert len(running) == 0
