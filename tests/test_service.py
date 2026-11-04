"""Integration tests for emu.service FastAPI endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from emu.models import EmulatorInfo, EmulatorInstance, InstanceStatus
from emu.proxy import _error_page
from emu.service import create_app, backend, registry


@pytest.fixture
def client():
    """Create test client with mocked backend."""
    mock_backend = MagicMock()
    mock_backend.detect.return_value = EmulatorInfo(
        name="LDPlayer 14",
        brand="ldplayer",
        version="14",
        install_dir=MagicMock(__str__=lambda s: r"C:\leidian\LDPlayer14"),
    )
    mock_backend.detect_all.return_value = [mock_backend.detect.return_value]
    mock_backend.list_instances.return_value = [
        EmulatorInstance(
            index=0, name="fgo", status=InstanceStatus.RUNNING,
            pid=12345, adb_serial="127.0.0.1:5555", emulator_brand="ldplayer",
        ),
        EmulatorInstance(
            index=1, name="test", status=InstanceStatus.STOPPED,
            pid=None, adb_serial="127.0.0.1:5557", emulator_brand="ldplayer",
        ),
    ]
    mock_backend.get_instance.side_effect = lambda idx: next(
        (i for i in mock_backend.list_instances() if i.index == idx), None
    )
    mock_backend.adb_serial.side_effect = lambda idx: f"127.0.0.1:{5555 + idx * 2}"
    mock_backend.launch.return_value = True
    mock_backend.stop.return_value = True

    import emu.service
    # Patch LDPlayerBackend so lifespan uses our mock
    with patch.object(emu.service, "LDPlayerBackend", return_value=mock_backend):
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as tc:
            yield tc


class TestInstanceEndpoints:
    def test_list_instances(self, client):
        resp = client.get("/api/instances")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "fgo"
        assert data[0]["status"] == "running"
        assert data[1]["status"] == "stopped"

    def test_get_instance(self, client):
        resp = client.get("/api/instances/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "fgo"
        assert data["pid"] == 12345

    def test_get_instance_not_found(self, client):
        resp = client.get("/api/instances/99")
        assert resp.status_code == 404

    def test_launch_instance(self, client):
        resp = client.post("/api/instances/1/launch")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_stop_instance(self, client):
        resp = client.post("/api/instances/0/stop")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_launch_missing_instance(self, client):
        assert client.post("/api/instances/99/launch").status_code == 404


class TestSystemEndpoints:
    def test_status(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "emulator-manager"
        assert data["emulator"]["detected"] is True
        assert data["emulator"]["brand"] == "ldplayer"

    def test_list_emulators(self, client):
        resp = client.get("/api/emulators")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["brand"] == "ldplayer"


class TestScriptEndpoints:
    def test_list_scripts(self, client):
        resp = client.get("/api/scripts")
        assert resp.status_code == 200
        data = resp.json()
        # Default FGO-py script is registered
        assert any(s["name"] == "fgo" for s in data)

    def test_adb_serial(self, client):
        resp = client.get("/api/instances/0/adb")
        assert resp.status_code == 200
        assert resp.json()["serial"] == "127.0.0.1:5555"

    def test_start_passes_installation_to_script(self, client):
        import emu.service
        from pathlib import Path
        b = emu.service.backend
        console = MagicMock(install_dir=Path(r"C:\leidian\LDPlayer9"))
        b.resolve_instance.return_value = (console, 0)
        with patch.object(emu.service.registry, "start") as start:
            start.return_value = MagicMock(status="running", pid=123, port=15002, instance_index=1)
            resp = client.post("/api/scripts/fgo/start", json={"instance_index": 1})
        assert resp.status_code == 200
        assert start.call_args.args == ("fgo", 1, "127.0.0.1:5557")
        assert start.call_args.kwargs["env"] == {
            "FGO_LDPLAYER_INSTALL_DIR": str(console.install_dir),
            "FGO_LDPLAYER_LOCAL_INDEX": "0",
        }

    def test_start_missing_instance(self, client):
        assert client.post("/api/scripts/fgo/start", json={"instance_index": 99}).status_code == 404


def test_shutdown_stops_scripts_without_stopping_emulator():
    mock_backend = MagicMock()
    mock_backend.detect.return_value = EmulatorInfo(
        name="LDPlayer 14",
        brand="ldplayer",
        version="14",
        install_dir=MagicMock(__str__=lambda s: r"C:\leidian\LDPlayer14"),
    )

    import emu.service

    with patch.object(emu.service, "LDPlayerBackend", return_value=mock_backend), \
         patch.object(emu.service.registry, "all_processes") as all_processes, \
         patch.object(emu.service.registry, "stop") as stop_script:
        proc = MagicMock()
        proc.status = "running"
        proc.script_name = "fgo"
        proc.instance_index = 0
        all_processes.return_value = [proc]

        app = create_app()
        with TestClient(app, raise_server_exceptions=False):
            pass

        stop_script.assert_called_once_with("fgo", 0)
        mock_backend.stop.assert_not_called()


def test_proxy_error_page_has_refresh_button():
    resp = _error_page("Connection failed", "The script may still be starting up.", 502)
    body = resp.body.decode()

    assert resp.status_code == 502
    assert "Refresh" in body
    assert "window.location.reload()" in body
    assert "Back to dashboard" in body


def test_multi_installation_api_keeps_duplicate_instances_separate(tmp_path):
    import emu.service
    from emu.ldplayer import LDConsole, LDPlayerMultiBackend

    paths = [tmp_path / f"LDPlayer{v}" for v in ("4", "9", "14")]
    for path in paths:
        path.mkdir()
        (path / "ldconsole.exe").touch()
    with patch("emu.ldplayer._detect_install_dirs", return_value=paths):
        multi_backend = LDPlayerMultiBackend()
    with patch.object(emu.service, "LDPlayerBackend", return_value=multi_backend), patch.object(
        LDConsole, "_run", return_value="0,fgo,0,0,0,-1,-1\n",
    ), TestClient(create_app()) as client:
        data = client.get("/api/instances").json()
        assert len(data) == 3
        assert [i["index"] for i in data] == [0, 1, 2]
        assert [i["local_index"] for i in data] == [0, 0, 0]
        assert [i["install_dir"] for i in data] == [str(p) for p in paths]
        assert [i["name"] for i in data] == ["fgo"] * 3
        assert len(client.get("/api/emulators").json()) == 3
        console, _ = multi_backend.resolve_instance(1)
        with patch.object(console, "launch", return_value=True) as launch:
            assert client.post("/api/instances/1/launch").json()["success"]
        launch.assert_called_once_with(0)
        assert client.get("/api/instances/2").json()["emulator_name"] == "LDPlayer 14"
