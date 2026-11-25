"""Dashboard launcher readiness checks without opening a real browser."""

from io import BytesIO
import threading
from unittest.mock import MagicMock
from urllib.error import URLError

import pytest

from emu import __main__ as launcher


def test_browser_waits_for_manager_readiness(monkeypatch):
    ready = MagicMock()
    ready.__enter__.return_value = BytesIO(b'{"service":"emulator-manager"}')
    request = MagicMock(side_effect=[URLError("Starting"), ready])
    browser = MagicMock(return_value=True)
    monkeypatch.setattr(launcher.urllib.request, "urlopen", request)
    monkeypatch.setattr(launcher.webbrowser, "open", browser)
    stopped = MagicMock()
    stopped.is_set.return_value = False

    launcher._open_browser_when_ready("http://127.0.0.1:15100", stopped)

    assert request.call_count == 2
    stopped.wait.assert_called_once_with(.25)
    browser.assert_called_once_with("http://127.0.0.1:15100")


def test_stopped_launcher_does_not_open_browser(monkeypatch):
    request, browser = MagicMock(), MagicMock()
    monkeypatch.setattr(launcher.urllib.request, "urlopen", request)
    monkeypatch.setattr(launcher.webbrowser, "open", browser)
    stopped = threading.Event()
    stopped.set()
    launcher._open_browser_when_ready("http://127.0.0.1:15100", stopped)
    request.assert_not_called()
    browser.assert_not_called()


@pytest.mark.parametrize("response", [b"Not JSON", b'{"service":"another-app"}'])
def test_unexpected_service_does_not_open_browser(monkeypatch, response, caplog):
    request = MagicMock()
    request.return_value.__enter__.return_value = BytesIO(response)
    browser = MagicMock()
    monkeypatch.setattr(launcher.urllib.request, "urlopen", request)
    monkeypatch.setattr(launcher.webbrowser, "open", browser)
    clock = iter([0, 0, 121])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    stopped = MagicMock()
    stopped.is_set.return_value = False
    launcher._open_browser_when_ready("http://127.0.0.1:15100", stopped)
    browser.assert_not_called()
    assert caplog.records


@pytest.mark.parametrize("open_browser", [False, True])
def test_cli_preserves_server_options_and_stops_browser_waiter(monkeypatch, open_browser):
    import uvicorn

    args = ["emu", "--port", "15109"]
    if open_browser:
        args.append("--open-browser")
    monkeypatch.setattr(launcher.sys, "argv", args)
    server = MagicMock()
    thread = MagicMock()
    monkeypatch.setattr(uvicorn, "run", server)
    monkeypatch.setattr(launcher.threading, "Thread", thread)
    monkeypatch.setattr(launcher, "_manager_is_ready", lambda url: False)

    launcher.main()

    assert server.call_args.kwargs["port"] == 15109
    assert server.call_args.kwargs["host"] == "0.0.0.0"
    if open_browser:
        assert thread.call_args.kwargs["args"][0] == "http://127.0.0.1:15109"
        assert thread.call_args.kwargs["args"][1].is_set()
        thread.return_value.start.assert_called_once_with()
    else:
        thread.assert_not_called()


@pytest.mark.parametrize("browser_available", [True, False])
def test_existing_manager_opens_browser_without_starting_server(monkeypatch, browser_available):
    import uvicorn

    monkeypatch.setattr(launcher.sys, "argv", ["emu", "--open-browser", "--port", "15109"])
    response = MagicMock()
    response.__enter__.return_value = BytesIO(b'{"service":"emulator-manager"}')
    request = MagicMock(return_value=response)
    browser = MagicMock(return_value=browser_available)
    server, thread = MagicMock(), MagicMock()
    monkeypatch.setattr(launcher.urllib.request, "urlopen", request)
    monkeypatch.setattr(launcher.webbrowser, "open", browser)
    monkeypatch.setattr(uvicorn, "run", server)
    monkeypatch.setattr(launcher.threading, "Thread", thread)

    if browser_available:
        launcher.main()
    else:
        with pytest.raises(SystemExit) as error:
            launcher.main()
        assert error.value.code == 1

    request.assert_called_once_with("http://127.0.0.1:15109/api/status", timeout=1)
    browser.assert_called_once_with("http://127.0.0.1:15109")
    server.assert_not_called()
    thread.assert_not_called()
