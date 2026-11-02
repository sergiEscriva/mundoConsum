"""Regresiones de API y persistencia sin credenciales ni llamadas a Consum."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import scraper


def api_ticket(ticket_id=1, total=8.39):
    return {"Id": ticket_id, "SaleTimestamp": "2026-01-15 10:00:00Z", "TotalPrice": total}


def api_context(responses):
    page = MagicMock()
    page.goto = AsyncMock()
    page.evaluate = AsyncMock(side_effect=responses)
    page.close = AsyncMock()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    return context, page


@pytest.mark.parametrize("status,exception", [
    (401, scraper.SessionError), (403, scraper.SessionError),
    (429, scraper.ScrapeError), (500, scraper.ScrapeError),
])
def test_http_failure_is_not_an_empty_list(status, exception):
    context, page = api_context([{"status": status, "body": "[]"}])
    with pytest.raises(exception):
        asyncio.run(scraper.fetch_tickets_list(context, "unused", "token"))
    page.close.assert_awaited_once()


@pytest.mark.parametrize("body", ["not json", "null", "{}", '[{"wrong": 1}]'])
def test_invalid_api_response_fails(body):
    context, _ = api_context([{"status": 200, "body": body}])
    with pytest.raises(scraper.ScrapeError):
        asyncio.run(scraper.fetch_tickets_list(context, "unused", "token"))


def test_empty_success_response_is_a_real_noop():
    context, _ = api_context([{"status": 200, "body": "[]"}])
    assert asyncio.run(scraper.fetch_tickets_list(context, "unused", "token")) == []


def test_pagination_and_form_encoding():
    first = [api_ticket(i) for i in range(10)]
    second = [api_ticket(10)]
    context, page = api_context([
        {"status": 200, "body": json.dumps(first)},
        {"status": 200, "body": json.dumps(second)},
    ])
    result = asyncio.run(scraper.fetch_tickets_list(context, "unused", "a+b&c="))
    assert len(result) == 11
    assert "password=a%2Bb%26c%3D" in page.evaluate.await_args_list[0].args[1]["body"]
    assert "page=1" in page.evaluate.await_args_list[1].args[1]["body"]


def test_failure_on_later_page_does_not_return_partial_result():
    context, _ = api_context([
        {"status": 200, "body": json.dumps([api_ticket(i) for i in range(10)])},
        {"status": 500, "body": "server error"},
    ])
    with pytest.raises(scraper.ScrapeError):
        asyncio.run(scraper.fetch_tickets_list(context, "unused", "token"))


def test_only_reconciled_tickets_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "TICKETS_DIR", tmp_path)
    for name, total in [("complete", 2), ("partial", 5)]:
        ticket = scraper.build_ticket_from_api(api_ticket(total=total))
        ticket["items"] = [{"name": "LECHE", "quantity": 1, "total": 2, "category": "Lácteos"}]
        (tmp_path / f"{name}.json").write_text(json.dumps(ticket))
    (tmp_path / "broken.json").write_text("invalid json")
    assert scraper.already_with_items() == {"complete"}


@pytest.mark.parametrize("quantity,total", [("1", "-1,20"), ("-1", "-1.20")])
def test_returns_are_parsed(quantity, total):
    pre = f"<normal-pequenya>{quantity}</normal-pequenya>LECHE ENTERA <normal-pequenya>1,20</normal-pequenya> {total}"
    items = scraper.parse_items_from_pre(pre)
    assert len(items) == 1
    assert items[0]["total"] == -1.2
    assert items[0]["quantity"] == float(quantity)


@pytest.fixture
def mocked_main(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "TICKETS_DIR", tmp_path)
    monkeypatch.setattr(scraper, "load_cookies", lambda: [{"name": "dummy"}])
    context = MagicMock(add_cookies=AsyncMock())
    browser = MagicMock(new_context=AsyncMock(return_value=context), close=AsyncMock())
    runtime = MagicMock()
    runtime.chromium.launch = AsyncMock(return_value=browser)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=runtime)
    manager.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(scraper, "async_playwright", lambda: manager)
    monkeypatch.setattr(scraper, "get_iframe_info", AsyncMock(return_value=("url", "token", "1")))
    monkeypatch.setattr(scraper, "fetch_tickets_list", AsyncMock(return_value=[api_ticket()]))
    detail = AsyncMock(return_value=None)
    monkeypatch.setattr(scraper, "fetch_ticket_detail", detail)
    return detail, browser


@pytest.mark.parametrize("pre", [None, "<pre>unrecognized format</pre>"])
def test_missing_detail_is_not_success(mocked_main, tmp_path, pre):
    detail, browser = mocked_main
    detail.return_value = pre
    with pytest.raises(scraper.ScrapeError, match="incompletos"):
        asyncio.run(scraper.main())
    saved = json.loads((tmp_path / "1.json").read_text())
    assert saved["extraction_status"] == "incomplete"
    assert scraper.already_with_items() == set()
    assert not list(tmp_path.glob("*.tmp"))
    browser.close.assert_awaited_once()


def test_successful_retry_replaces_incomplete_ticket(mocked_main, tmp_path):
    detail, browser = mocked_main
    previous = scraper.build_ticket_from_api(api_ticket())
    previous["items"] = [{"name": "LECHE", "quantity": 1, "total": 1, "category": "Lácteos"}]
    (tmp_path / "1.json").write_text(json.dumps(previous))
    detail.return_value = "<normal-pequenya>1</normal-pequenya>POLLO FRESCO <normal-pequenya>8,39</normal-pequenya> 8,39"
    asyncio.run(scraper.main())
    saved = json.loads((tmp_path / "1.json").read_text())
    assert saved["extraction_status"] == "complete"
    assert saved["items"][0]["total"] == 8.39
    assert scraper.already_with_items() == {"1"}
    browser.close.assert_awaited_once()


def test_failed_retry_preserves_previous_lines(mocked_main, tmp_path):
    previous = scraper.build_ticket_from_api(api_ticket())
    previous["items"] = [{"name": "LECHE", "quantity": 1, "total": 1, "category": "Lácteos"}]
    (tmp_path / "1.json").write_text(json.dumps(previous))
    with pytest.raises(scraper.ScrapeError):
        asyncio.run(scraper.main())
    saved = json.loads((tmp_path / "1.json").read_text())
    assert saved["items"] == previous["items"]
    assert saved["extraction_status"] == "incomplete"


def test_repeated_full_api_page_is_an_error():
    body = json.dumps([api_ticket(i) for i in range(10)])
    context, _ = api_context([{"status": 200, "body": body}] * 2)
    with pytest.raises(scraper.ScrapeError, match="repite"):
        asyncio.run(scraper.fetch_tickets_list(context, "unused", "token"))


def test_spanish_thousands_in_negative_amount():
    pre = "<normal-pequenya>1</normal-pequenya>PRODUCTO TEST <normal-pequenya>1.234,56</normal-pequenya> -1.234,56"
    assert scraper.parse_items_from_pre(pre)[0]["total"] == -1234.56
