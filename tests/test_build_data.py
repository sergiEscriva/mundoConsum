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

    def test_differences_are_visible_without_changing_original_total(self):
        tickets = [
            _make_ticket(total=8, items=[_make_item(total=10)]),
            _make_ticket(total=4, items=[]),
            _make_ticket(total=3, items=[_make_item(total=3)]),
        ]
        summary = build_summary(tickets)
        assert summary["total_spent"] == 15
        quality = summary["data_quality"]
        assert quality["complete_tickets"] == 1
        assert quality["review_tickets"] == 1
        assert quality["missing_detail_tickets"] == 1
        assert quality["issues"][0]["difference"] == -2
        assert quality["issues"][0]["ticket_total"] == 8
        assert summary["categories"][0]["total"] == 13

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


class TestSmartBasketAnalytics:
    def test_savings_summary_combines_verified_and_estimated_discounts(self):
        tickets = [
            {
                **_make_ticket(date="2026-01-10", total=6.85, items=[_make_item(total=10.00)]),
                "id": "t1",
                "discounts": [{"name": "CHEQUE AHORRO SOCIO", "amount": -3.15}],
            },
            {
                **_make_ticket(date="2026-01-15", total=7.20, items=[_make_item(total=10.00)]),
                "id": "t2",
            },
            {
                **_make_ticket(date="2026-01-20", total=5.00, items=[_make_item(total=5.00)]),
                "id": "t3",
            },
        ]
        summary = build_summary(tickets)
        sav = summary["savings_summary"]
        assert sav["verified_discounts_total"] == 3.15
        assert sav["estimated_unparsed_discounts"] == 2.80
        assert sav["total_estimated_saved"] == 5.95
        assert sav["tickets_with_savings"] == 2
        assert summary["data_quality"]["review_tickets"] == 1

    def test_missing_months_filled_with_spanish_labels_and_intraday_chronological_sort(self):
        tickets = [
            {
                **_make_ticket(date="2026-01-10", total=10.0),
                "id": "99",
                "datetime_iso": "2026-01-10T18:30:00+01:00",
                "store": "Tienda Tarde",
            },
            {
                **_make_ticket(date="2026-01-10", total=5.0),
                "id": "100",
                "datetime_iso": "2026-01-10T09:15:00+01:00",
                "store": "Tienda Mañana",
            },
            {
                **_make_ticket(date="2026-03-05", total=15.0),
                "id": "101",
                "datetime_iso": "2026-03-05T12:00:00+01:00",
                "store": "Tienda Marzo",
            },
        ]
        summary = build_summary(tickets)
        assert [m["month"] for m in summary["monthly_data"]] == ["2026-01", "2026-02", "2026-03"]
        assert [m["label"] for m in summary["monthly_data"]] == ["ene 2026", "feb 2026", "mar 2026"]
        assert summary["monthly_data"][1] == {
            "month": "2026-02",
            "label": "feb 2026",
            "total": 0.0,
            "visits": 0,
            "change_pct": -100.0,
            "moving_avg": 7.5,
        }
        # El ticket de la tarde (18:30) debe quedar antes que el de la mañana (09:15) en recent_tickets
        assert [t["store"] for t in summary["recent_tickets"]] == [
            "Tienda Marzo",
            "Tienda Tarde",
            "Tienda Mañana",
        ]

    def test_personal_inflation_and_shrinkflation_detection(self):
        tickets = [
            {
                **_make_ticket(
                    date="2026-01-05",
                    total=10.0,
                    items=[
                        {"name": "ACEITE OLIVA CONSUM 1L", "quantity": 1, "unit_price": 4.00, "total": 4.00, "category": "Conservas y Salsas"},
                        {"name": "LECHE ENTERA 1L", "quantity": 2, "unit_price": 1.00, "total": 2.00, "category": "Lácteos y Huevos"},
                        {"name": "ARROZ REDONDO CONSUM 500G", "quantity": 2, "unit_price": 1.50, "total": 3.00, "category": "Pasta, Arroz y Legumbres"},
                        {"name": "PAN MOLDE 1U", "quantity": 1, "unit_price": 1.00, "total": 1.00, "category": "Pan y Bollería"},
                    ],
                ),
                "id": "1",
            },
            {
                **_make_ticket(
                    date="2026-03-10",
                    total=9.20,
                    items=[
                        {"name": "ACEITE OLIVA CONSUM 1L", "quantity": 1, "unit_price": 5.00, "total": 5.00, "category": "Conservas y Salsas"},
                        {"name": "LECHE ENTERA 1L", "quantity": 2, "unit_price": 0.85, "total": 1.70, "category": "Lácteos y Huevos"},
                        {"name": "ARROZ REDONDO CONSUM 450G", "quantity": 1, "unit_price": 1.50, "total": 1.50, "category": "Pasta, Arroz y Legumbres"},
                        {"name": "PAN MOLDE 1U", "quantity": 1, "unit_price": 1.00, "total": 1.00, "category": "Pan y Bollería"},
                    ],
                ),
                "id": "2",
            },
        ]
        summary = build_summary(tickets)
        inf = summary["inflation"]
        assert inf["tracked_products"] == 3
        assert inf["rising_products"] == 1
        assert inf["falling_products"] == 1
        assert inf["stable_products"] == 1
        assert inf["personal_ipc_pct"] > 0
        assert inf["annualized_ipc_pct"] is not None
        assert len(inf["shrinkflation_alerts"]) == 1
        alert = inf["shrinkflation_alerts"][0]
        assert alert["previous_size"] == "500 g"
        assert alert["current_size"] == "450 g"
        assert alert["unit_cost_change_pct"] > 10.0

    def test_budget_summary_burn_rate_and_category_statuses(self):
        from datetime import datetime, timezone

        tickets = [
            _make_ticket(
                date="2026-04-05",
                total=90.0,
                items=[
                    _make_item(name="POLLO", total=50.0, category="Carne y Pescado"),
                    _make_item(name="AGUA", total=25.0, category="Bebidas"),
                    _make_item(name="MANZANA", total=15.0, category="Frutas y Verduras"),
                ],
            )
        ]
        budgets = {
            "monthly": {"Carne y Pescado": 50.0, "Bebidas": 30.0, "Frutas y Verduras": 40.0},
            "alert_monthly_total": 300.0,
        }
        now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        summary = build_summary(tickets, budgets=budgets, now=now)
        bsum = summary["budget_summary"]
        assert bsum["month"] == "2026-04"
        assert bsum["is_current_month"] is True
        assert bsum["overall"]["spent"] == 90.0
        assert bsum["overall"]["burn_rate_daily"] == 9.0
        assert bsum["overall"]["projected_month_end"] == 270.0
        statuses = {c["category"]: c["status"] for c in bsum["categories"]}
        assert statuses == {"Carne y Pescado": "over", "Bebidas": "warn", "Frutas y Verduras": "ok"}

    def test_private_brand_share_and_nova_4_health_warning(self):
        tickets = [
            _make_ticket(
                date="2026-03-10",
                total=20.0,
                items=[
                    _make_item(name="REFRESCO COLA CONSUM 2L", total=8.0, category="Bebidas"),
                    _make_item(name="PATATAS FRITAS BONTE", total=6.0, category="Snacks y Dulces"),
                    _make_item(name="MANZANA GOLDEN", total=6.0, category="Frutas y Verduras"),
                ],
            )
        ]
        summary = build_summary(tickets)
        assert summary["private_brand_share_pct"] == 70.0
        nova_by_group = {g["group"]: g for g in summary["nova_distribution"]}
        assert nova_by_group[4]["pct"] == 70.0
        assert any("NOVA 4" in tip["title"] for tip in summary["health_tips"])

    def test_anonymize_hides_store_names_and_ticket_ids(self):
        tickets = [
            {
                **_make_ticket(date="2026-03-01", total=8.0, items=[_make_item(total=10.0)]),
                "id": "secret-ticket-999",
                "store": "Consum Calle Secreta 42",
                "store_address": "Calle Secreta 42, Valencia",
            }
        ]
        summary = build_summary(tickets, anonymize=True)
        serialized = json.dumps(summary, ensure_ascii=False)
        assert summary["anonymized"] is True
        assert summary["recent_tickets"][0]["store"] == "Consum #1"
        assert "id" not in summary["data_quality"]["issues"][0]
        assert "secret-ticket-999" not in serialized
        assert "Calle Secreta" not in serialized
