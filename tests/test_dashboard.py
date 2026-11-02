"""Pruebas del dashboard en navegador, sin depender de CDNs ni datos privados."""

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_data import build_summary


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        executable = Path(p.chromium.executable_path)
        system = shutil.which("chromium")
        if executable.exists():
            browser = p.chromium.launch(headless=True)
        elif system:
            browser = p.chromium.launch(executable_path=system, headless=True)
        else:
            if os.environ.get("REQUIRE_BROWSER_TESTS") == "1":
                pytest.fail("Chromium es obligatorio en CI")
            pytest.skip("Instala Chromium con python -m playwright install chromium")
        yield browser
        browser.close()


def ticket(month, total, items):
    return {"id": month, "date": month + "-10", "total": total,
            "source": "dom_extracted", "items": items}


def item(name, category, total):
    return {"name": name, "category": category, "total": total, "quantity": 1}


@pytest.fixture
def dashboard(browser):
    page = browser.new_page()
    # En Madrid ya es noviembre; UTC todavía es octubre.
    page.clock.install(time=datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc))
    items = [item(f"Producto {i:02}", "Comida", 1) for i in range(25)]
    items.append(item("AGUA", "Bebidas", 3))
    summary = build_summary([
        ticket("2026-10", 100, [item("ANTIGUO", "Bebidas", 100)]),
        ticket("2026-11", 28, items),
    ])
    resources = {
        "/dashboard/": ("text/html", (ROOT / "dashboard/index.html").read_text()),
        "/data/summary.json": ("application/json", json.dumps(summary)),
        "/data/budgets.json": ("application/json", json.dumps({
            "monthly": {"Bebidas": 20}, "alert_monthly_total": 50
        })),
    }

    def route_request(route):
        from urllib.parse import urlsplit
        url = urlsplit(route.request.url)
        if url.hostname == "dashboard.test" and url.path in resources:
            content_type, body = resources[url.path]
            route.fulfill(status=200, content_type=content_type, body=body)
        else:
            route.abort()

    page.route("**/*", route_request)
    yield page
    page.close()


def load(page):
    page.goto("http://dashboard.test/dashboard/", wait_until="networkidle")
    page.wait_for_function('document.querySelectorAll("#kpi-grid .kpi").length === 4')


def test_current_month_budgets_and_kpis_use_madrid(dashboard):
    load(dashboard)
    assert dashboard.evaluate("currentMonth()") == "2026-11"
    assert "28.00" in dashboard.locator("#kpi-grid .kpi").nth(3).inner_text()
    assert "3.00" in dashboard.locator("#budget-bars").inner_text()
    assert "103.00" not in dashboard.locator("#budget-bars").inner_text()
    assert not dashboard.locator("#spending-alert").is_visible()


def test_category_filter_and_product_outside_top_twenty(dashboard):
    load(dashboard)
    dashboard.locator("#search-input").fill("Producto 24")
    assert "Producto 24" in dashboard.locator("#search-results").inner_text()
    dashboard.locator("#search-cat").select_option("Bebidas")
    assert dashboard.locator("#search-results").inner_text() == "Sin resultados"
    dashboard.locator("#search-input").fill("")
    assert "AGUA" in dashboard.locator("#search-results").inner_text()
    assert "Producto" not in dashboard.locator("#search-results").inner_text()


def test_missing_chart_library_preserves_tables_search_and_tips(dashboard):
    load(dashboard)
    assert not dashboard.locator("#error-msg").is_visible()
    assert dashboard.locator("#chart-monthly-error").is_visible()
    assert dashboard.locator("#chart-categories-error").is_visible()
    assert dashboard.locator("#tickets-body tr").count() == 2
    assert dashboard.locator("#top-products .product-row").count() > 0
    assert dashboard.locator("#health-tips .tip").count() > 0
    assert dashboard.locator("#search-cat option").count() == 3


def test_chart_constructor_failure_is_isolated(dashboard):
    dashboard.add_init_script('window.Chart = function () { throw new Error("render failed"); };')
    load(dashboard)
    assert dashboard.locator("#chart-monthly-error").is_visible()
    assert dashboard.locator("#chart-categories-error").is_visible()
    assert dashboard.locator("#tickets-body tr").count() == 2
    assert not dashboard.locator("#error-msg").is_visible()


def test_no_current_month_activity_shows_zero(dashboard):
    load(dashboard)
    dashboard.evaluate('''() => {
        DATA.monthly_data = DATA.monthly_data.filter(m => m.month !== currentMonth());
        DATA.monthly_categories = {};
        renderKPIs(DATA);
        renderBudgets(DATA, BUDGETS);
        renderSpendingAlert(DATA, BUDGETS);
    }''')
    assert "0.00" in dashboard.locator("#kpi-grid .kpi").nth(3).inner_text()
    assert "0.00" in dashboard.locator("#budget-bars").inner_text()
    assert not dashboard.locator("#spending-alert").is_visible()
