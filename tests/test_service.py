"""Integration tests for emu.service FastAPI endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from emu.models import EmulatorInfo, EmulatorInstance, InstanceStatus
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
