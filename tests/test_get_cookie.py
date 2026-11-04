"""Regresiones del login manual y sus redirecciones sin acceder a Consum."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import async_playwright, Error as PlaywrightError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import get_cookie


def fake_page(access=False):
    page = Mock()
    page.is_closed.return_value = False
    page.evaluate = AsyncMock(return_value=access)
    page.goto = AsyncMock()
    page.close = AsyncMock()
    return page


def test_prompt_keeps_async_browser_events_running(monkeypatch):
    events = []

    def user_input(message):
        time.sleep(0.1)
        assert events == ["navigation processed"]
        return ""

    monkeypatch.setattr("builtins.input", user_input)

    async def scenario():
        asyncio.get_running_loop().call_later(0.01, events.append, "navigation processed")
        assert await get_cookie.prompt("login") == ""

    asyncio.run(scenario())


def test_interrupted_navigation_can_still_verify_session():
    original, verification = fake_page(), fake_page(True)
    verification.goto.side_effect = PlaywrightError("Navigation interrupted by SSO")
    context = Mock(pages=[original, verification])
    context.new_page = AsyncMock(return_value=verification)
    assert asyncio.run(get_cookie.verify_session(context, timeout=0.1)) is True
    verification.close.assert_awaited_once()
    original.close.assert_not_called()


def test_redirect_to_login_without_ticket_access_is_not_success():
    verification = fake_page()
    context = Mock(pages=[verification])
    context.new_page = AsyncMock(return_value=verification)
    assert asyncio.run(get_cookie.verify_session(context, timeout=0.01)) is False
    verification.close.assert_awaited_once()


def test_login_completed_in_another_tab_is_recognized():
    original, popup, verification = fake_page(), fake_page(True), fake_page()
    context = Mock(pages=[original, popup, verification])
    context.new_page = AsyncMock(return_value=verification)
    assert asyncio.run(get_cookie.verify_session(context, timeout=0.1)) is True
    popup.close.assert_not_called()


def test_execution_context_destroyed_during_redirect_is_not_a_crash():
    page = fake_page()
    page.evaluate.side_effect = PlaywrightError("Execution context was destroyed")
    assert asyncio.run(get_cookie.has_ticket_access(page)) is False


def test_failed_verification_can_retry_without_overwriting_cookies(monkeypatch, tmp_path):
    output = tmp_path / "cookies.json"
    output.write_text("old session")
    page = fake_page()
    context = Mock()
    context.new_page = AsyncMock(return_value=page)
    context.cookies = AsyncMock(return_value=[{"name": "session", "value": "synthetic"}])
    answers = AsyncMock(side_effect=["", ""])
    monkeypatch.setattr(get_cookie, "prompt", answers)

    async def verify(ctx):
        assert output.read_text() == "old session"
        return answers.await_count == 2

    monkeypatch.setattr(get_cookie, "verify_session", verify)
    assert asyncio.run(get_cookie.capture_session(context, output)) == 0
    assert json.loads(output.read_text())[0]["value"] == "synthetic"
    assert not (tmp_path / ".env.cookies.tmp").exists()
    page.close.assert_not_called()


def test_cancel_after_failed_verification_preserves_previous_cookies(monkeypatch, tmp_path):
    output = tmp_path / "cookies.json"
    output.write_text("old session")
    context = Mock(new_page=AsyncMock(return_value=fake_page()))
    monkeypatch.setattr(get_cookie, "prompt", AsyncMock(side_effect=["", "salir"]))
    monkeypatch.setattr(get_cookie, "verify_session", AsyncMock(return_value=False))
    assert asyncio.run(get_cookie.capture_session(context, output)) == 1
    assert output.read_text() == "old session"
    context.cookies.assert_not_called()


def test_real_browser_login_redirects_continue_while_user_is_in_terminal(monkeypatch, tmp_path):
    output = tmp_path / "cookies.json"

    async def scenario():
        async with async_playwright() as p:
            bundled = Path(p.chromium.executable_path)
            system = shutil.which("chromium")
            if not bundled.exists() and not system:
                if os.environ.get("REQUIRE_BROWSER_TESTS") == "1":
                    pytest.fail("Chromium es obligatorio en CI")
                pytest.skip("Instala Chromium para esta regresión")
            browser = await p.chromium.launch(
                headless=True, **({} if bundled.exists() else {"executable_path": system})
            )
            context = await browser.new_context()

            async def route_request(route):
                if route.request.url == get_cookie.LOGIN_URL:
                    await route.fulfill(content_type="text/html", body=f"""
                        <script>setTimeout(() => location.href = '{get_cookie.VERIFY_URL}', 50);</script>
                        <p>Login sintético</p>""")
                elif route.request.url == get_cookie.VERIFY_URL:
                    await route.fulfill(content_type="text/html", headers={
                        "Set-Cookie": "synthetic_session=verified; Path=/; Secure; HttpOnly"
                    }, body='<iframe src="https://nomastickets.consum.es/app/index.html?pass=synthetic"></iframe>')
                else:
                    await route.fulfill(body="")

            await context.route("**/*", route_request)
            original_verify = get_cookie.verify_session

            async def verify(ctx):
                # Esta URL queda obsoleta si input() bloquea el bucle de eventos.
                assert ctx.pages[0].url == get_cookie.VERIFY_URL
                return await original_verify(ctx, timeout=1)

            def user_input(message):
                time.sleep(0.5)
                return ""

            monkeypatch.setattr("builtins.input", user_input)
            monkeypatch.setattr(get_cookie, "verify_session", verify)
            try:
                assert await get_cookie.capture_session(context, output) == 0
                assert any(c["name"] == "synthetic_session" for c in json.loads(output.read_text()))
                assert len(context.pages) == 1
            finally:
                await browser.close()

    asyncio.run(scenario())


def test_push_cookies_secret_passes_payload_via_stdin_never_argv(monkeypatch, tmp_path):
    import subprocess

    cookies_file = tmp_path / "cookies.json"
    secret_payload = '[{"name": "SUPER_SECRET_COOKIE", "value": "do-not-expose-in-argv"}]'
    cookies_file.write_text(secret_payload, encoding="utf-8")

    monkeypatch.setattr(get_cookie.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    run_mock = Mock(return_value=subprocess.CompletedProcess(["/usr/bin/gh"], 0, "", ""))
    monkeypatch.setattr(get_cookie.subprocess, "run", run_mock)

    get_cookie.push_cookies_secret(cookies_file, repo="owner/repo")
    call = run_mock.call_args
    cmd = call.args[0]
    assert cmd == ["/usr/bin/gh", "secret", "set", "CONSUM_COOKIES", "--repo", "owner/repo"]
    assert all("do-not-expose-in-argv" not in str(arg) for arg in cmd)
    assert call.kwargs["input"] == secret_payload


def test_trigger_daily_workflow_runs_gh_workflow_run(monkeypatch):
    import subprocess

    monkeypatch.setattr(get_cookie.shutil, "which", lambda name: "/usr/bin/gh")
    run_mock = Mock(return_value=subprocess.CompletedProcess(["/usr/bin/gh"], 0, "", ""))
    monkeypatch.setattr(get_cookie.subprocess, "run", run_mock)

    get_cookie.trigger_daily_workflow(repo="owner/repo")
    assert run_mock.call_args.args[0] == ["/usr/bin/gh", "workflow", "run", "daily.yml", "--repo", "owner/repo"]


def test_push_cookies_secret_reports_clear_spanish_errors_when_gh_missing_or_unauthenticated(monkeypatch, tmp_path):
    import subprocess

    cookies_file = tmp_path / "cookies.json"
    cookies_file.write_text('[{"name": "s", "value": "1"}]', encoding="utf-8")

    monkeypatch.setattr(get_cookie.shutil, "which", lambda name: None)
    with pytest.raises(get_cookie.CookieSyncError, match="No se encontró GitHub CLI"):
        get_cookie.push_cookies_secret(cookies_file)

    monkeypatch.setattr(get_cookie.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        get_cookie.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess(["gh"], 1, "", "not logged in to any github hosts; run gh auth login")),
    )
    with pytest.raises(get_cookie.CookieSyncError, match="gh auth login"):
        get_cookie.push_cookies_secret(cookies_file)
