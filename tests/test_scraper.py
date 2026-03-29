"""Tests para funciones puras de scraper.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scraper import categorize, parse_items_from_pre, build_ticket_from_api, SKIP


class TestCategorize:
    def test_known_meat_product(self):
        assert categorize("PECHUGA DE POLLO") == "Carne y Pescado"

    def test_known_dairy_product(self):
        assert categorize("LECHE ENTERA") == "Lácteos y Huevos"

    def test_known_beverage(self):
        assert categorize("AGUA CONSUM 1,5L") == "Bebidas"

    def test_known_fruit(self):
        assert categorize("MANZANA GOLDEN") == "Frutas y Verduras"

    def test_known_bread(self):
        assert categorize("BARRA ESPIGA TRADIC") == "Pan y Bollería"

    def test_known_frozen(self):
        assert categorize("PIZZA MARGARITA") == "Congelados y Preparados"

    def test_known_pasta(self):
        assert categorize("ESPAGUETI N5") == "Pasta, Arroz y Legumbres"

    def test_known_sauce(self):
        assert categorize("KETCHUP CONSUM") == "Conservas y Salsas"

    def test_known_snack(self):
        assert categorize("CHOCOLATE MILKA") == "Snacks y Dulces"

    def test_known_hygiene(self):
        assert categorize("DETERGENTE LIQUIDO") == "Higiene y Limpieza"

    def test_unknown_returns_otros(self):
        assert categorize("XYZPRODUCTO RARO") == "Otros"

    def test_case_insensitive(self):
        assert categorize("pollo limpio") == "Carne y Pescado"


SAMPLE_PRE = (
    '<normal-pequenya>1</normal-pequenya>'
    'PECHUGA DE POLLO   '
    '<normal-pequenya>5,99</normal-pequenya>'
    ' 5,99'
    '\n'
    '<normal-pequenya>2</normal-pequenya>'
    'LECHE ENTERA       '
    '<normal-pequenya>1,20</normal-pequenya>'
    ' 2,40'
)


class TestParseItemsFromPre:
    def test_parses_two_items(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert len(items) == 2

    def test_first_item_name(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert items[0]["name"] == "PECHUGA DE POLLO"

    def test_first_item_quantity(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert items[0]["quantity"] == 1.0

    def test_first_item_total(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert items[0]["total"] == 5.99

    def test_first_item_category(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert items[0]["category"] == "Carne y Pescado"

    def test_second_item_quantity(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert items[1]["quantity"] == 2.0

    def test_second_item_total(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert items[1]["total"] == 2.40

    def test_skips_invoice_lines(self):
        pre = (
            '<normal-pequenya>1</normal-pequenya>'
            'TOTAL              '
            '<normal-pequenya>10,00</normal-pequenya>'
            ' 10,00'
        )
        items = parse_items_from_pre(pre)
        assert len(items) == 0

    def test_empty_input(self):
        assert parse_items_from_pre("") == []

    def test_html_entities_unescaped(self):
        pre = (
            '<normal-pequenya>1</normal-pequenya>'
            'JAM&Oacute;N SERRANO '
            '<normal-pequenya>3,50</normal-pequenya>'
            ' 3,50'
        )
        items = parse_items_from_pre(pre)
        assert len(items) == 1
        assert "JAMÓN" in items[0]["name"]

    def test_sold_by_kg_flag(self):
        items = parse_items_from_pre(SAMPLE_PRE)
        assert items[0]["sold_by_kg"] is True


class TestBuildTicketFromApi:
    def test_basic_fields(self):
        api_data = {
            "Id": 12345,
            "TicketCode": "TC001",
            "Store": "Consum Test",
            "StoreAddress": "Calle Test 1",
            "SaleTimestamp": "2026-01-15 10:30:00Z",
            "TotalPrice": 25.50,
            "PaymentMethod": "Tarjeta",
        }
        ticket = build_ticket_from_api(api_data)
        assert ticket["id"] == "12345"
        assert ticket["store"] == "Consum Test"
        assert ticket["total"] == 25.50
        assert ticket["date"] == "2026-01-15"
        assert ticket["source"] == "api_only"
        assert ticket["items"] == []

    def test_timezone_conversion_winter(self):
        """En invierno, Europe/Madrid = UTC+1."""
        api_data = {
            "Id": 1,
            "SaleTimestamp": "2026-01-15 10:00:00Z",
            "TotalPrice": 10,
        }
        ticket = build_ticket_from_api(api_data)
        assert ticket["date"] == "2026-01-15"
        assert "11:00" in ticket["datetime_iso"]

    def test_timezone_conversion_summer(self):
        """En verano, Europe/Madrid = UTC+2."""
        api_data = {
            "Id": 2,
            "SaleTimestamp": "2026-07-15 10:00:00Z",
            "TotalPrice": 10,
        }
        ticket = build_ticket_from_api(api_data)
        assert ticket["date"] == "2026-07-15"
        assert "12:00" in ticket["datetime_iso"]

    def test_missing_timestamp(self):
        api_data = {"Id": 3, "TotalPrice": 5}
        ticket = build_ticket_from_api(api_data)
        assert ticket["date"] is None
        assert ticket["datetime_iso"] is None

    def test_default_payment_method(self):
        api_data = {"Id": 4, "TotalPrice": 5, "PaymentMethod": None}
        ticket = build_ticket_from_api(api_data)
        assert ticket["payment_method"] == "Tarjeta"


class TestSkipSet:
    def test_invoice_keywords_present(self):
        for kw in ("FACTURA", "IMPORTE", "TOTAL", "IVA", "BASE"):
            assert kw in SKIP

    def test_card_keywords_present(self):
        for kw in ("VISA", "BBVA", "TARJ"):
            assert kw in SKIP
