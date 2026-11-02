"""Tests para funciones puras de build_data.py."""

import sys
from pathlib import Path

import json
import subprocess
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_data import (
    build_summary,
    generate_health_tips,
    generate_saving_tips,
    validate_ticket,
)


def _make_ticket(date="2026-01-15", total=20.0, items=None, source="dom_extracted"):
    return {
        "id": "1",
        "date": date,
        "total": total,
        "items": items or [],
        "source": source,
        "store": "Consum Test",
        "payment_method": "Tarjeta",
    }


def _make_item(name="POLLO", qty=1, total=5.0, category="Carne y Pescado"):
    return {"name": name, "quantity": qty, "total": total, "category": category}


class TestBuildSummary:
    def test_empty_tickets(self):
        summary = build_summary([])
        assert summary["total_tickets"] == 0
        assert summary["total_spent"] == 0

    def test_single_ticket(self):
        t = _make_ticket(total=15.0)
        summary = build_summary([t])
        assert summary["total_tickets"] == 1
        assert summary["total_spent"] == 15.0
        assert summary["avg_ticket"] == 15.0

    def test_monthly_data(self):
        tickets = [
            _make_ticket(date="2026-01-10", total=10),
            _make_ticket(date="2026-01-20", total=20),
            _make_ticket(date="2026-02-05", total=30),
        ]
        summary = build_summary(tickets)
        assert len(summary["monthly_data"]) == 2
        jan = summary["monthly_data"][0]
        assert jan["month"] == "2026-01"
        assert jan["total"] == 30.0
        assert jan["visits"] == 2

    def test_categories(self):
        items = [_make_item(category="Bebidas", total=3.0), _make_item(category="Bebidas", total=2.0)]
        t = _make_ticket(items=items)
        summary = build_summary([t])
        cats = {c["name"]: c for c in summary["categories"]}
        assert "Bebidas" in cats
        assert cats["Bebidas"]["total"] == 5.0
        assert cats["Bebidas"]["count"] == 2

    def test_top_products(self):
        items = [_make_item(name="LECHE", total=1.5)] * 3
        t = _make_ticket(items=items)
        summary = build_summary([t])
        assert summary["top_products"][0]["name"] == "LECHE"
        assert summary["top_products"][0]["times"] == 3

    def test_recent_tickets_order(self):
        tickets = [
            _make_ticket(date="2026-01-01", total=10),
            _make_ticket(date="2026-01-05", total=20),
        ]
        summary = build_summary(tickets)
        assert summary["recent_tickets"][0]["date"] == "2026-01-05"

    def test_has_tips(self):
        t = _make_ticket(total=100, items=[_make_item(total=100)])
        summary = build_summary([t])
        assert isinstance(summary["health_tips"], list)
        assert isinstance(summary["saving_tips"], list)
        assert len(summary["health_tips"]) > 0

    def test_monthly_categories_do_not_include_other_months(self):
        summary = build_summary([
            _make_ticket(date="2026-01-10", items=[_make_item(total=8)]),
            _make_ticket(date="2026-02-10", items=[_make_item(total=3)]),
        ])
        assert summary["monthly_categories"]["2026-02"] == [
            {"name": "Carne y Pescado", "total": 3}
        ]
        assert summary["categories"][0]["total"] == 11

    def test_search_catalog_is_not_limited_to_top_twenty(self):
        items = [_make_item(name=f"Producto {i}") for i in range(25)]
        summary = build_summary([_make_ticket(items=items)])
        assert len(summary["top_products"]) == 20
        assert len(summary["products"]) == 25
        assert all(p["category"] == "Carne y Pescado" for p in summary["products"])

    def test_zero_ticket_counts_in_average(self):
        summary = build_summary([_make_ticket(total=10), _make_ticket(total=0)])
        assert summary["avg_ticket"] == 5

    def test_numeric_strings_are_normalized_without_mutating_input(self):
        ticket = _make_ticket(total="12,50", items=[_make_item(total="12.50", qty="2")])
        summary = build_summary([ticket])
        assert summary["total_spent"] == 12.5
        assert summary["categories"][0]["total"] == 12.5
        assert ticket["total"] == "12,50"
        assert ticket["items"][0]["quantity"] == "2"

    @pytest.mark.parametrize("total", [None, True, "bad", float("nan"), float("inf")])
    def test_invalid_totals_are_rejected(self, total):
        with pytest.raises(ValueError):
            build_summary([_make_ticket(total=total)])

    @pytest.mark.parametrize("date", ["2026-13-01", "20260101", "2026-1-1", None])
    def test_invalid_dates_are_rejected(self, date):
        with pytest.raises(ValueError):
            build_summary([_make_ticket(date=date)])


def test_invalid_file_prevents_partial_summary(monkeypatch, tmp_path):
    import build_data
    (tmp_path / "good.json").write_text(json.dumps(_make_ticket()))
    (tmp_path / "broken.json").write_text('{"date":')
    monkeypatch.setattr(build_data, "TICKETS_DIR", tmp_path)
    with pytest.raises(ValueError, match="no se actualizará"):
        build_data.load_all_tickets()


def test_loading_does_not_rewrite_original_encoding(monkeypatch, tmp_path):
    import build_data
    path = tmp_path / "ticket.json"
    raw = json.dumps(_make_ticket(), ensure_ascii=False).encode("utf-8-sig")
    path.write_bytes(raw)
    monkeypatch.setattr(build_data, "TICKETS_DIR", tmp_path)
    assert len(build_data.load_all_tickets()) == 1
    assert path.read_bytes() == raw


def test_cli_preserves_existing_summary_on_invalid_input(tmp_path):
    tickets = tmp_path / "data/tickets"
    tickets.mkdir(parents=True)
    (tickets / "bad.json").write_text(json.dumps(_make_ticket(total="invalid")))
    summary = tmp_path / "data/summary.json"
    summary.write_text('{"previous": true}')
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts/build_data.py")],
        cwd=tmp_path, capture_output=True,
    )
    assert result.returncode != 0
    assert summary.read_text() == '{"previous": true}'


def test_cli_clears_stale_summary_when_no_tickets_exist(tmp_path):
    (tmp_path / "data").mkdir()
    summary = tmp_path / "data/summary.json"
    summary.write_text('{"previous": true}')
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts/build_data.py")],
        cwd=tmp_path, check=True, capture_output=True,
    )
    assert json.loads(summary.read_text())["total_tickets"] == 0


class TestGenerateHealthTips:
    def test_no_data(self):
        tips = generate_health_tips({}, 0)
        assert len(tips) == 1
        assert tips[0]["type"] == "success"

    def test_low_fruit_warning(self):
        tips = generate_health_tips({"Frutas y Verduras": 5}, 100)
        types = [t["type"] for t in tips]
        assert "warning" in types

    def test_high_fruit_success(self):
        tips = generate_health_tips({"Frutas y Verduras": 25}, 100)
        types = [t["type"] for t in tips]
        assert "success" in types

    def test_high_snacks_warning(self):
        tips = generate_health_tips({"Snacks y Dulces": 20}, 100)
        assert any("snack" in t["title"].lower() for t in tips)

    def test_balanced_basket(self):
        tips = generate_health_tips({"Frutas y Verduras": 15, "Carne y Pescado": 30}, 100)
        assert len(tips) >= 1


class TestGenerateSavingTips:
    def test_no_data(self):
        tips = generate_saving_tips({}, [], 0, [])
        assert len(tips) == 1
        assert tips[0]["type"] == "success"

    def test_increasing_spending_warning(self):
        monthly = [
            {"month": "2026-01", "total": 100, "visits": 4},
            {"month": "2026-02", "total": 110, "visits": 4},
            {"month": "2026-03", "total": 120, "visits": 4},
        ]
        tips = generate_saving_tips({}, monthly, 30, [])
        assert any("creciente" in t["title"].lower() for t in tips)

    def test_many_visits_tip(self):
        monthly = [{"month": "2026-03", "total": 200, "visits": 10}]
        tips = generate_saving_tips({}, monthly, 20, [{"name": "A", "times": 1}])
        assert any("visitas" in t["title"].lower() for t in tips)


class TestValidateTicket:
    def test_valid_ticket(self):
        t = _make_ticket()
        issues = validate_ticket(t, "test.json")
        assert issues == []

    def test_missing_fields(self):
        issues = validate_ticket({"id": "1"}, "test.json")
        assert len(issues) > 0

    def test_invalid_total_type(self):
        t = _make_ticket()
        t["total"] = "not a number"
        issues = validate_ticket(t, "test.json")
        assert any("numérico" in i for i in issues)

    def test_items_not_list(self):
        t = _make_ticket()
        t["items"] = "invalid"
        issues = validate_ticket(t, "test.json")
        assert any("lista" in i for i in issues)

    def test_item_missing_fields(self):
        t = _make_ticket(items=[{"name": "TEST"}])
        issues = validate_ticket(t, "test.json")
        assert any("Item 0" in i for i in issues)
