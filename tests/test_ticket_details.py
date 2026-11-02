"""Sesión de la app, parámetros del iframe y diagnósticos del detalle."""

import asyncio
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import async_playwright, Error as PlaywrightError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import scraper

PRE = '<normal-pequenya>1</normal-pequenya>LECHE ENTERA <normal-pequenya>1,20</normal-pequenya> 1,20'
IFRAME = "https://nomastickets.consum.es/app/index.html?pass=synthetic&dni=synthetic-id&other=keep&v=2026"


def test_detail_url_preserves_iframe_parameters_and_encodes_token_once():
    url = scraper.detail_url("123", "a+b&login=ok", "2026", IFRAME)
    params = parse_qs(urlsplit(url).query)
    assert params == {"ticketId": ["123"], "pass": ["a+b&login=ok"], "v": ["2026"],
                      "dni": ["synthetic-id"], "other": ["keep"]}


def test_detail_url_rejects_unexpected_domain():
    with pytest.raises(scraper.ScrapeError):
        scraper.detail_url("123", "token", "2026", "https://example.invalid/app/index.html")


def detail_page(status=200):
    page = MagicMock()
    page.url = "https://nomastickets.consum.es/app/ticketdetail.html?pass=login-is-part-of-token"
    page.goto = AsyncMock(return_value=SimpleNamespace(status=status))
    page.wait_for_url = AsyncMock()
    page.close = AsyncMock()
    pre = MagicMock(wait_for=AsyncMock(), inner_html=AsyncMock(return_value=PRE))
    page.locator.return_value = pre
    return page


def test_shared_application_tab_is_not_closed_and_uses_locator():
    page = detail_page()
    context = MagicMock()
    result = asyncio.run(scraper.fetch_ticket_detail(context, "123", "synthetic", "2026", page=page, iframe_url=IFRAME))
    assert result == PRE
    page.close.assert_not_called()
    page.wait_for_function.assert_not_called()
    assert page.goto.await_args.kwargs["referer"] == IFRAME
    context.new_page.assert_not_called()


@pytest.mark.parametrize("status,exception", [
    (401, scraper.SessionError), (403, scraper.SessionError),
    (404, scraper.ScrapeError), (429, scraper.ScrapeError),
])
def test_http_error_is_reported_instead_of_repeating_for_every_ticket(status, exception):
    page = detail_page(status)
    with pytest.raises(exception, match=f"HTTP {status}"):
        asyncio.run(scraper.fetch_ticket_detail(MagicMock(), "123", "synthetic", "2026", page=page))
    assert page.goto.await_count == 1


def test_navigation_failure_logs_cause_without_token_or_call_log(caplog):
    page = detail_page()
    page.goto.side_effect = PlaywrightError(
        'Page.goto: net::ERR_ABORTED at https://nomastickets.consum.es/app/ticketdetail.html?pass=private-token&dni=private-id\n'
        'Call log:\nsecret diagnostic must not be printed'
    )
    context = MagicMock(new_page=AsyncMock(return_value=page))
    assert asyncio.run(scraper.fetch_ticket_detail(context, "123", "private-token", "2026")) is None
    assert "net::ERR_ABORTED" in caplog.text
    for private in ("private-token", "private-id", "secret diagnostic"):
        assert private not in caplog.text
    page.close.assert_awaited_once()


@pytest.mark.parametrize("fail_first", [False, True])
def test_real_browser_preserves_session_storage_and_reinitializes_after_failure(fail_first):
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
            detail_requests, initializations = [], []

            async def route_request(route):
                url = urlsplit(route.request.url)
                if url.path == "/app/index.html":
                    initializations.append(route.request.url)
                    await route.fulfill(content_type="text/html", body="<script>sessionStorage.setItem('app-ready', 'yes');</script>")
                elif url.path == "/api/ticket/v2":
                    await route.fulfill(content_type="application/json", body='[{"Id": 123}]')
                elif url.path == "/app/ticketdetail.html":
                    detail_requests.append(route.request.url)
                    if fail_first and len(detail_requests) == 1:
                        await route.abort("failed")
                        return
                    params = parse_qs(url.query)
                    assert params["dni"] == ["synthetic-id"]
                    assert params["other"] == ["keep"]
                    await route.fulfill(content_type="text/html", body=f"""
                        <pre id="ticketpre"></pre><script>
                        if (sessionStorage.getItem('app-ready') === 'yes') {{
                            setTimeout(() => document.getElementById('ticketpre').innerHTML = {PRE!r}, 100);
                        }}
                        </script>""")
                else:
                    await route.abort()

            await context.route("**/*", route_request)
            page = await context.new_page()
            try:
                tickets = await scraper.fetch_tickets_list(context, IFRAME, "synthetic", page=page)
                assert tickets == [{"Id": 123}]
                result = await scraper.fetch_ticket_detail(context, "123", "synthetic", "2026", page=page, iframe_url=IFRAME)
                assert scraper.parse_items_from_pre(result)[0]["total"] == 1.2
                assert len(context.pages) == 1
                assert len(initializations) == (2 if fail_first else 1)
                assert len(detail_requests) == (2 if fail_first else 1)
            finally:
                await browser.close()

    asyncio.run(scenario())
