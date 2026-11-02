"""Normalización y validación compartidas de los tickets locales."""

import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation


def number(value) -> float:
    """Acepta números y decimales de API/locale; rechaza NaN e infinitos."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("importe no numérico")
    if isinstance(value, str):
        value = value.strip()
        if "," in value:
            if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+),\d+", value):
                raise ValueError("decimal no válido")
            value = value.replace(".", "").replace(",", ".")
        elif not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
            raise ValueError("decimal no válido")
    try:
        result = float(Decimal(str(value)))
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("importe no numérico") from exc
    if not math.isfinite(result):
        raise ValueError("importe no finito")
    return result


def normalize_ticket(data: dict) -> dict:
    """Devuelve una copia válida sin alterar el JSON original."""
    if not isinstance(data, dict):
        raise ValueError("el ticket no es un objeto")
    required = {"id", "date", "total", "items", "source"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Campos faltantes: {sorted(missing)}")
    try:
        if not isinstance(data["date"], str) or date.fromisoformat(data["date"]).isoformat() != data["date"]:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("fecha no válida; se requiere YYYY-MM-DD") from None
    if not isinstance(data["items"], list):
        raise ValueError("'items' no es una lista")
    if not str(data["id"] or "").strip() or not isinstance(data["source"], str):
        raise ValueError("id/source no válidos")
    ticket = dict(data)
    try:
        ticket["total"] = number(data["total"])
    except ValueError as exc:
        raise ValueError("'total' no es numérico válido") from exc
    ticket["items"] = []
    for index, raw in enumerate(data["items"]):
        try:
            if not isinstance(raw, dict) or {"name", "quantity", "total", "category"} - raw.keys():
                raise ValueError("campos faltantes")
            item = dict(raw)
            for field in ("name", "category"):
                if not isinstance(item[field], str) or not item[field].strip():
                    raise ValueError(f"{field} no válido")
            for field in ("quantity", "total"):
                item[field] = number(item[field])
            ticket["items"].append(item)
        except ValueError as exc:
            raise ValueError(f"Item {index}: {exc}") from exc
    return ticket


def reconcile_ticket(ticket: dict) -> dict:
    """Informar del descuadre sin inventar descuentos ni modificar el total original."""
    ticket = normalize_ticket(ticket)
    total = Decimal(str(ticket["total"]))
    item_total = sum((Decimal(str(i["total"])) for i in ticket["items"]), Decimal(0))
    difference = total - item_total
    status = "complete" if abs(difference) <= Decimal("0.02") else "needs_review"
    if not ticket["items"]:
        status = "incomplete"
    return {"status": status, "ticket_total": float(total),
            "items_total": float(item_total.quantize(Decimal("0.01"))),
            "difference": float(difference.quantize(Decimal("0.01")))}


def extraction_complete(ticket: dict) -> bool:
    """Un detalle solo se considera completo si sus líneas concilian a céntimos."""
    try:
        return reconcile_ticket(ticket)["status"] == "complete"
    except ValueError:
        return False
