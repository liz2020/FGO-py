"""Unit tests for emu.registry module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import psutil
import pytest

from emu.models import AutomationScript, ScriptProcess
from emu.registry import ScriptRegistry


@pytest.fixture
def registry():
    with patch.object(ScriptRegistry, "_port_owners", return_value=set()):
        yield ScriptRegistry()


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
    def test_start_refuses_an_occupied_port(self, registry, fgo_script):
        registry.register(fgo_script)
        with patch.object(registry, "_port_owners", return_value={456}), patch("subprocess.Popen") as popen:
            with pytest.raises(RuntimeError, match="already occupied"):
                registry.start("FGO-py", 0, "127.0.0.1:5555")
        popen.assert_not_called()

    def test_start_merges_installation_environment(self, registry, fgo_script):
        registry.register(fgo_script)
        with patch.dict("os.environ", {"PATH": "existing-path"}), patch("subprocess.Popen") as popen:
            registry.start("FGO-py", 3, "127.0.0.1:5555", env={
                "FGO_LDPLAYER_INSTALL_DIR": r"C:\leidian\LDPlayer9",
                "FGO_LDPLAYER_LOCAL_INDEX": "0",
            })
        env = popen.call_args.kwargs["env"]
        assert env["PATH"] == "existing-path"
        assert env["FGO_LDPLAYER_INSTALL_DIR"] == r"C:\leidian\LDPlayer9"
        assert env["FGO_LDPLAYER_LOCAL_INDEX"] == "0"

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

    def test_get_running_filters_windows_stale_pid_system_error(self, registry, fgo_script):
        registry.register(fgo_script)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            registry.start("FGO-py", 0, "127.0.0.1:5555")

        with patch("emu.registry.os.kill", side_effect=SystemError):
            running = registry.get_running("FGO-py")

        assert len(running) == 0


def script_process(pid, args, cwd):
    process = MagicMock(spec=psutil.Process)
    process.pid = pid
    process.info = {"cmdline": ["python.exe", *args]}
    process.cwd.return_value = str(cwd)
    return process


class TestForceStop:
    def test_finds_orphan_and_wrapper_without_killing_other_instances(self, registry, tmp_path):
        registry.register(AutomationScript(
            name="fgo", package_filter="*", base_port=15001,
            start_command="uv run python FGO-py/fgo.py web2 --device ldplayer:{index} --port {port}",
        ))
        args = ["FGO-py/fgo.py", "web2", "--device", "ldplayer:3", "--port", "15004"]
        wrapper = script_process(101, args, tmp_path)
        orphan = script_process(102, args, tmp_path / "FGO-py")
        other_instance = script_process(103, [*args[:3], "ldplayer:4", "--port", "15005"], tmp_path)
        other_repo = script_process(104, args, tmp_path / "other")
        unrelated = script_process(105, ["-c", "fgo.py", *args[1:]], tmp_path)
        repeated_device = script_process(106, [*args, "--device=ldplayer:4"], tmp_path)
        stale = ScriptProcess("fgo", 3, 15004, pid=999, status="running")
        registry._processes["fgo:3"] = stale
        with patch("emu.registry.psutil.process_iter", side_effect=[
            [wrapper, orphan, other_instance, other_repo, unrelated, repeated_device], [],
        ]), patch("emu.registry.psutil.wait_procs", return_value=([], [])), \
             patch.object(registry, "_port_owners", side_effect=[{102}, set()]):
            assert registry.force_stop("fgo", 3, "", tmp_path) == [101, 102]
        wrapper.kill.assert_called_once_with()
        orphan.kill.assert_called_once_with()
        for process in [other_instance, other_repo, unrelated, repeated_device]:
            process.kill.assert_not_called()
        assert stale.status == "stopped"
        assert stale.pid is None

    @pytest.mark.parametrize("failure", ["unverified_owner", "permission_denied", "still_alive", "port_busy"])
    def test_failure_does_not_report_a_successful_stop(self, registry, fgo_script, tmp_path, failure):
        registry.register(fgo_script)
        process = script_process(101, [], tmp_path)
        tracked = ScriptProcess("FGO-py", 0, 15001, pid=101, status="running")
        registry._processes["FGO-py:0"] = tracked
        owners = [{999}] if failure == "unverified_owner" else [{101}, {101} if failure == "port_busy" else set()]
        if failure == "permission_denied":
            process.kill.side_effect = psutil.AccessDenied(101)
        alive = [process] if failure == "still_alive" else []
        with patch.object(registry, "_find_script_processes", return_value=[process]), \
             patch.object(registry, "_port_owners", side_effect=owners), \
             patch("emu.registry.psutil.wait_procs", return_value=([], alive)):
            with pytest.raises(RuntimeError):
                registry.force_stop("FGO-py", 0, "", tmp_path)
        if failure == "unverified_owner":
            process.kill.assert_not_called()
        assert tracked.status == "running"
        assert tracked.pid == 101


def test_force_stop_releases_a_real_untracked_script_listener(tmp_path):
    import socket
    import subprocess
    import sys
    import time

    entry = tmp_path / "fgo.py"
    entry.write_text(
        "import socket, sys, time\n"
        "s = socket.socket()\n"
        "s.bind(('127.0.0.1', int(sys.argv[sys.argv.index('--port') + 1])))\n"
        "s.listen()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    registry = ScriptRegistry()
    registry.register(AutomationScript(
        name="fgo", package_filter="*", base_port=port,
        start_command=f'python "{entry}" --device ldplayer:{{index}} --port {{port}}',
    ))
    child = subprocess.Popen(
        [sys.executable, str(entry), "--device", "ldplayer:0", "--port", str(port)],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not (owners := registry._port_owners(port)):
            assert child.poll() is None, child.stderr.read().decode()
            assert time.monotonic() < deadline, "Test listener did not start"
            time.sleep(.05)
        killed = registry.force_stop("fgo", 0, "", tmp_path)
        assert child.pid in killed
        assert owners <= set(killed)
        child.wait(timeout=5)
        assert not registry._port_owners(port)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stderr.close()
