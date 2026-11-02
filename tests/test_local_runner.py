"""Regresiones del lanzador local; sin acceso a Consum ni instalación de paquetes."""

from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("consum_runner", ROOT / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def commands(monkeypatch):
    python = ROOT / ".venv/bin/python"
    monkeypatch.setattr(runner, "prepare_python", Mock(return_value=python))
    for name in ("prepare_browser", "build", "login", "update", "serve"):
        monkeypatch.setattr(runner, name, Mock())
    return python


def test_default_starts_without_downloading_chromium(commands):
    assert runner.main(["--no-browser"]) == 0
    runner.build.assert_called_once_with(commands)
    runner.serve.assert_called_once_with(8000, False)
    runner.prepare_browser.assert_not_called()


@pytest.mark.parametrize("command", ["setup", "login", "update"])
def test_browser_is_prepared_for_live_access(commands, command):
    assert runner.main([command, "--with-deps"]) == 0
    runner.prepare_browser.assert_called_once_with(commands, True)
    runner.serve.assert_not_called()


def test_test_command_preserves_failure_status(commands, monkeypatch):
    monkeypatch.setattr(runner, "execute", Mock(return_value=5))
    assert runner.main(["test"]) == 5


def test_setup_failure_does_not_start_service(commands):
    runner.prepare_python.side_effect = runner.LocalError("installation failed")
    assert runner.main([]) == 1
    runner.serve.assert_not_called()


def test_subprocess_always_runs_from_project_root(monkeypatch):
    execute = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(runner.subprocess, "run", execute)
    runner.execute(["python", "script.py"])
    assert execute.call_args.kwargs["cwd"] == ROOT


@pytest.fixture
def update_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "build", Mock())
    monkeypatch.setattr(runner, "login", Mock())
    monkeypatch.setattr(runner.sys, "stdin", Mock(isatty=Mock(return_value=False)))
    monkeypatch.delenv("CONSUM_COOKIES", raising=False)
    (tmp_path / "cookies.json").write_text("[]")
    return tmp_path


@pytest.mark.parametrize("code", [0, 2])
def test_update_rebuilds_after_success_or_noop(update_setup, monkeypatch, code):
    monkeypatch.setattr(runner, "execute", Mock(return_value=code))
    runner.update("python")
    runner.build.assert_called_once_with("python")


@pytest.mark.parametrize("code", [1, 3])
def test_failed_update_does_not_rebuild(update_setup, monkeypatch, code):
    monkeypatch.setattr(runner, "execute", Mock(return_value=code))
    with pytest.raises(runner.LocalError):
        runner.update("python")
    runner.build.assert_not_called()


def test_expired_environment_cookies_are_replaced_for_retry(update_setup, monkeypatch):
    monkeypatch.setenv("CONSUM_COOKIES", "dummy-expired")
    monkeypatch.setattr(runner.sys.stdin, "isatty", Mock(return_value=True))
    execute = Mock(side_effect=[1, 0])
    monkeypatch.setattr(runner, "execute", execute)
    runner.update("python")
    runner.login.assert_called_once_with("python")
    # La copia entregada al primer proceso no debe alterarse al preparar el reintento.
    assert execute.call_args_list[0].kwargs["env"]["CONSUM_COOKIES"] == "dummy-expired"
    assert "CONSUM_COOKIES" not in execute.call_args_list[1].kwargs["env"]
    runner.build.assert_called_once()


def test_missing_cookies_noninteractive_reports_next_step(update_setup):
    (update_setup / "cookies.json").unlink()
    with pytest.raises(runner.LocalError, match="python run.py login"):
        runner.update("python")
    runner.login.assert_not_called()


def test_http_serves_dashboard_but_not_cookies_or_ticket_files(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard/index.html").write_text("<h1>Dashboard</h1>")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/summary.json").write_text(json.dumps({"total_tickets": 0}))
    (tmp_path / "cookies.json").write_text("private")
    with runner.create_server(0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, status in [
                ("/dashboard/", 200), ("/data/summary.json?t=1", 200),
                ("/cookies.json", 404), ("/.git/config", 404),
                ("/data/tickets/1.json", 404), ("/dashboard/../cookies.json", 404),
                ("/dashboard/%2e%2e/cookies.json", 404),
            ]:
                conn = HTTPConnection("127.0.0.1", server.server_port)
                conn.request("GET", path)
                response = conn.getresponse()
                assert response.status == status, path
                assert response.getheader("Cache-Control") == "no-store"
                response.read()
                conn.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_busy_port_gets_another_available_port():
    with runner.create_server(0) as first:
        with runner.create_server(first.server_port) as second:
            assert second.server_port != first.server_port


def test_failed_installation_is_not_marked_as_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    (tmp_path / "requirements.txt").write_text("pytest==8.3.4\n")
    monkeypatch.setattr(runner.venv, "EnvBuilder", Mock())
    monkeypatch.setattr(runner.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 1)))
    with pytest.raises(runner.LocalError, match="dependencias"):
        runner.prepare_python()
    assert not (tmp_path / ".venv/.consum-requirements.sha256").exists()
