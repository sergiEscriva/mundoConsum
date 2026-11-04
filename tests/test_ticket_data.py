"""Tests unitarios para normalización, descuentos y reconciliación en ticket_data.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ticket_data import extraction_complete, normalize_ticket, reconcile_ticket


def _base_ticket(**overrides):
    ticket = {
        "id": "1001",
        "date": "2026-03-15",
        "total": 10.00,
        "source": "dom_extracted",
        "items": [
            {"name": "ACEITE OLIVA 1L", "quantity": 1, "total": 6.50, "category": "Conservas y Salsas"},
            {"name": "PECHUGA POLLO", "quantity": 1.25, "unit_price": 5.32, "sold_by_kg": True, "total": 6.65, "category": "Carne y Pescado"},
        ],
    }
    ticket.update(overrides)
    return ticket


def test_reconcile_ticket_without_discounts_marks_needs_review_when_total_differs():
    ticket = _base_ticket()  # items = 13.15, total = 10.00
    report = reconcile_ticket(ticket)
    assert report["ticket_total"] == 10.00
    assert report["items_total"] == 13.15
    assert report["discounts_total"] == 0.0
    assert report["difference"] == -3.15
    assert report["status"] == "needs_review"
    assert extraction_complete(ticket) is False


def test_reconcile_ticket_with_cheque_ahorro_reconciles_to_complete():
    ticket = _base_ticket(
        discounts=[{"name": "CHEQUE AHORRO SOCIO", "amount": "-3,15"}],
    )
    normalized = normalize_ticket(ticket)
    assert normalized["discounts"] == [{"name": "CHEQUE AHORRO SOCIO", "amount": -3.15}]

    report = reconcile_ticket(normalized)
    assert report["ticket_total"] == 10.00
    assert report["items_total"] == 13.15
    assert report["discounts_total"] == -3.15
    assert report["difference"] == 0.0
    assert report["status"] == "complete"
    assert extraction_complete(normalized) is True


def test_multiple_discounts_sum_accurately_with_decimal():
    ticket = _base_ticket(
        total=9.15,
        discounts=[
            {"name": "CHEQUE AHORRO", "amount": -3.15},
            {"name": "CUPÓN MI CONSUM", "amount": -0.85},
        ],
    )
    report = reconcile_ticket(ticket)
    assert report["discounts_total"] == -4.00
    assert report["difference"] == 0.0
    assert report["status"] == "complete"


@pytest.mark.parametrize("bad_discounts", [
    "no-es-lista",
    [{"name": "DTO POSITIVO", "amount": 2.50}],
    [{"name": "", "amount": -1.00}],
    [{"name": "DTO BOOL", "amount": True}],
    ["cadena"],
])
def test_invalid_discounts_raise_value_error(bad_discounts):
    with pytest.raises(ValueError):
        normalize_ticket(_base_ticket(discounts=bad_discounts))
