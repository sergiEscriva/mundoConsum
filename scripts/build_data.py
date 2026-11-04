"""
build_data.py — Agrega todos los JSON de tickets en un único summary.json
que leerá el dashboard.
"""

import argparse
import calendar
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ticket_data import normalize_ticket, reconcile_ticket
from gemini_categories import (
    GeminiError,
    canonical_product_name,
    classify_tickets,
    infer_product_metadata,
    is_private_brand,
    load_product_metadata,
    product_key,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_data")

TICKETS_DIR = Path("data/tickets")
OUTPUT_FILE = Path("data/summary.json")
BUDGETS_FILE = Path("data/budgets.json")

REQUIRED_TICKET_FIELDS = {"id", "date", "total", "items", "source"}
REQUIRED_ITEM_FIELDS = {"name", "quantity", "total", "category"}

SPANISH_MONTHS = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr",
    5: "may", 6: "jun", 7: "jul", 8: "ago",
    9: "sep", 10: "oct", 11: "nov", 12: "dic",
}

NON_FOOD_CATEGORIES = {"Higiene y Limpieza", "Hogar y Menaje", "Mascotas"}
NON_FOOD_NAME_RE = re.compile(r"\b(?:BOLSA\s+COMPRA|PELUCHE|PILAS|BOMBILLA)\b", re.IGNORECASE)

NOVA_LABELS = {
    1: "NOVA 1 · Alimentos frescos o mínimamente procesados",
    2: "NOVA 2 · Ingredientes culinarios procesados",
    3: "NOVA 3 · Alimentos procesados",
    4: "NOVA 4 · Ultraprocesados",
}


def _month_label(ym: str) -> str:
    year_s, month_s = ym.split("-")
    return f"{SPANISH_MONTHS[int(month_s)]} {int(year_s)}"


def _iter_months(first_ym: str, last_ym: str) -> list[str]:
    y1, m1 = map(int, first_ym.split("-"))
    y2, m2 = map(int, last_ym.split("-"))
    months = []
    y, m = y1, m1
    while (y, m) <= (y2, m2):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _parse_pack_size(name: str, category: str = "Otros") -> tuple[str, float | None, str | None, str | None]:
    """Extrae firma base + tamaño estandarizado (g, ml o ud) para detectar reduflación."""
    norm = product_key(name or "")
    working = norm
    std_val: float | None = None
    std_unit: str | None = None
    raw_label: str | None = None

    # 1. Formato multipack tipo 2X200G, P3X210, 24X30G
    m_multi = re.search(
        r"\b(?:P-?)?(\d+)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*(KG|KGR|G|GR|GRS|ML|CL|L|LT)?\b",
        working,
        re.IGNORECASE,
    )
    # Evitar confundir dimensiones tipo 40X40 (servilletas)
    if m_multi and category not in NON_FOOD_CATEGORIES:
        count = float(m_multi.group(1))
        each = float(m_multi.group(2).replace(",", "."))
        unit_tok = (m_multi.group(3) or "G").upper()
        if unit_tok in {"KG", "KGR"}:
            std_val, std_unit = count * each * 1000.0, "g"
        elif unit_tok in {"G", "GR", "GRS"}:
            std_val, std_unit = count * each, "g"
        elif unit_tok in {"L", "LT"}:
            std_val, std_unit = count * each * 1000.0, "ml"
        elif unit_tok == "CL":
            std_val, std_unit = count * each * 10.0, "ml"
        elif unit_tok == "ML":
            std_val, std_unit = count * each, "ml"
        raw_label = m_multi.group(0).strip()
        working = (working[:m_multi.start()] + " " + working[m_multi.end():]).strip()

    # 2. Formato pack tipo 1,5 P-6
    if std_val is None:
        m_pack = re.search(r"(\d+(?:[.,]\d+)?)\s*(L|LT|ML|CL|KG|G)?\s+P-(\d+)\b", working, re.IGNORECASE)
        if m_pack:
            each = float(m_pack.group(1).replace(",", "."))
            unit_tok = (m_pack.group(2) or ("L" if category == "Bebidas" else "G")).upper()
            count = float(m_pack.group(3))
            if unit_tok in {"L", "LT"}:
                std_val, std_unit = each * count * 1000.0, "ml"
            elif unit_tok == "CL":
                std_val, std_unit = each * count * 10.0, "ml"
            elif unit_tok == "ML":
                std_val, std_unit = each * count, "ml"
            elif unit_tok == "KG":
                std_val, std_unit = each * count * 1000.0, "g"
            else:
                std_val, std_unit = each * count, "g"
            raw_label = m_pack.group(0).strip()
            working = (working[:m_pack.start()] + " " + working[m_pack.end():]).strip()

    # 3. Unidad explícita (500G, 1,5L, 2 KG, 200ML, 10U / 2 UDS)
    if std_val is None:
        m_unit = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(KG|KGR|G|GR|GRS|ML|CL|L|LT|UDS|UD|UN|U)\b\.?",
            working,
            re.IGNORECASE,
        )
        if m_unit:
            val = float(m_unit.group(1).replace(",", "."))
            unit_tok = m_unit.group(2).upper()
            if unit_tok in {"KG", "KGR"}:
                std_val, std_unit = val * 1000.0, "g"
            elif unit_tok in {"G", "GR", "GRS"}:
                std_val, std_unit = val, "g"
            elif unit_tok in {"L", "LT"}:
                std_val, std_unit = val * 1000.0, "ml"
            elif unit_tok == "CL":
                std_val, std_unit = val * 10.0, "ml"
            elif unit_tok == "ML":
                std_val, std_unit = val, "ml"
            else:
                std_val, std_unit = val, "ud"
            raw_label = m_unit.group(0).strip()
            working = (working[:m_unit.start()] + " " + working[m_unit.end():]).strip()

    # 4. Gramaje truncado al final de línea de 20 caracteres en alimentación (ej. CONS250, CON.150, BARILLA500)
    if std_val is None and category not in NON_FOOD_CATEGORIES:
        m_trunc = re.search(r"(?<=[A-ZÁÉÍÓÚÜÑ.\s])(\d{3})(?:\s+CONSUM|\s+CONSU|\s+CONS|\s+CON)?$", working)
        if m_trunc:
            grams = int(m_trunc.group(1))
            if 80 <= grams <= 999 and working[:m_trunc.start()].strip() not in {"", "1906"}:
                std_val, std_unit = float(grams), "g"
                raw_label = f"{grams}G"
                working = (working[:m_trunc.start()] + " " + working[m_trunc.end():]).strip()

    # Normalizar abreviaturas frecuentes de marca/serie en la firma base
    base = working.replace(".", ". ")
    base = re.sub(r"\bG\.\s*R(?:ES)?\.?\b", " GRAN RESERVA ", base)
    base = re.sub(r"\bTOM\.\s*FRITO\b|\bT\.\s*FRITO\b", " TOMATE FRITO ", base)
    base = re.sub(r"\b(?:CONSUM|CONSU\.?|CONS\.?|CON\.)(?=\s|$)", " CONSUM ", base)
    base = re.sub(r"\bP-\d+\b", " ", base)
    base_tokens = re.findall(r"[A-ZÁÉÍÓÚÜÑ0-9]+", base)
    base_sig = " ".join(base_tokens)
    return base_sig, std_val, std_unit, raw_label


def _format_size(std_val: float, std_unit: str) -> str:
    if std_unit == "g":
        if std_val >= 1000:
            kg = std_val / 1000.0
            return f"{kg:g} kg".replace(".", ",")
        return f"{int(round(std_val))} g"
    if std_unit == "ml":
        if std_val >= 1000:
            liters = std_val / 1000.0
            return f"{liters:g} L".replace(".", ",")
        return f"{int(round(std_val))} ml"
    return f"{std_val:g} ud"


def compute_inflation(tickets: list[dict], metadata: dict[str, dict] | None = None) -> dict:
    """Calcula el IPC personal ponderado por gasto y alertas de reduflación."""
    metadata = metadata or {}
    sku_obs: dict[tuple[str, str], dict[str, Any]] = {}
    family_obs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for t in tickets:
        t_date = t["date"]
        for item in t.get("items", []):
            qty = item.get("quantity", 0)
            total = item.get("total", 0)
            if not qty or qty <= 0 or total <= 0:
                continue
            unit_p = item.get("unit_price")
            if unit_p is None or unit_p <= 0:
                unit_p = total / qty
            if unit_p <= 0:
                continue

            raw_name = item["name"]
            cat = item.get("category", "Otros")
            pkey = product_key(raw_name)
            meta = metadata.get(pkey)
            canon = (
                item.get("canonical_name")
                or (meta["canonical_name"] if meta else None)
                or canonical_product_name(raw_name)
            )
            base_sig, std_val, std_unit, _ = _parse_pack_size(raw_name, cat)
            size_bucket = f"{std_unit}:{round(std_val, 2)}" if (std_val and std_unit) else canonical_product_name(raw_name)
            # Mantener formatos distintos separados aunque canonical_name omita el tamaño
            sku_id = (canonical_product_name(canon), size_bucket)

            entry = sku_obs.setdefault(sku_id, {
                "name": raw_name,
                "canonical_name": canon,
                "category": cat,
                "total_spent": 0.0,
                "daily": defaultdict(lambda: [0.0, 0.0]),  # [weighted_price_sum, qty_sum]
            })
            entry["name"] = raw_name
            entry["category"] = cat
            entry["total_spent"] += total
            entry["daily"][t_date][0] += unit_p * qty
            entry["daily"][t_date][1] += qty

            if base_sig and std_val and std_unit and len(base_sig) >= 4:
                scale = 1000.0 if std_unit in {"g", "ml"} else 1.0
                unit_cost = unit_p / (std_val / scale)
                family_obs[(base_sig, std_unit)].append({
                    "date": t_date,
                    "name": raw_name,
                    "canonical_name": canon,
                    "std_val": std_val,
                    "std_unit": std_unit,
                    "unit_price": round(unit_p, 4),
                    "unit_cost": unit_cost,
                })

    tracked_items = []
    rising = falling = stable = 0
    weighted_pct_sum = 0.0
    weight_sum = 0.0
    earliest_date: str | None = None
    latest_date: str | None = None

    for entry in sku_obs.values():
        if len(entry["daily"]) < 2:
            continue
        history = [
            {"date": d, "unit_price": round(vals[0] / vals[1], 4)}
            for d, vals in sorted(entry["daily"].items())
            if vals[1] > 0
        ]
        if len(history) < 2 or history[0]["unit_price"] <= 0:
            continue

        first_d, first_p = history[0]["date"], history[0]["unit_price"]
        last_d, last_p = history[-1]["date"], history[-1]["unit_price"]
        min_p = min(h["unit_price"] for h in history)
        max_p = max(h["unit_price"] for h in history)
        pct = ((last_p - first_p) / first_p) * 100.0
        pct_r = round(pct, 2)

        if pct_r > 0:
            rising += 1
        elif pct_r < 0:
            falling += 1
        else:
            stable += 1

        spent = round(entry["total_spent"], 2)
        weighted_pct_sum += spent * pct
        weight_sum += spent
        if earliest_date is None or first_d < earliest_date:
            earliest_date = first_d
        if latest_date is None or last_d > latest_date:
            latest_date = last_d

        tracked_items.append({
            "name": entry["name"],
            "canonical_name": entry["canonical_name"],
            "category": entry["category"],
            "observations": len(history),
            "first_date": first_d,
            "last_date": last_d,
            "first_unit_price": round(first_p, 2),
            "last_unit_price": round(last_p, 2),
            "min_unit_price": round(min_p, 2),
            "max_unit_price": round(max_p, 2),
            "change_pct": pct_r,
            "total_spent": spent,
            "price_history": history,
        })

    tracked_items.sort(
        key=lambda x: (-abs(x["total_spent"] * (x["change_pct"] / 100.0)), -abs(x["change_pct"]), x["name"])
    )

    personal_ipc_pct = round(weighted_pct_sum / weight_sum, 2) if weight_sum > 0 else 0.0
    annualized_ipc_pct = None
    span_days = 0
    if earliest_date and latest_date:
        span_days = (date.fromisoformat(latest_date) - date.fromisoformat(earliest_date)).days
        if span_days >= 30 and (1.0 + personal_ipc_pct / 100.0) > 0:
            annualized = ((1.0 + personal_ipc_pct / 100.0) ** (365.0 / span_days) - 1.0) * 100.0
            annualized_ipc_pct = round(annualized, 2)

    # Detección de reduflación (mismo producto/marca base, menor peso/volumen posterior y mayor €/kg o €/L)
    shrinkflation_alerts = []
    for (base_sig, std_unit), obs_list in sorted(family_obs.items()):
        obs_sorted = sorted(obs_list, key=lambda o: o["date"])
        distinct_sizes = {round(o["std_val"], 2) for o in obs_sorted}
        if len(distinct_sizes) < 2:
            continue
        first_obs = obs_sorted[0]
        last_obs = obs_sorted[-1]
        if last_obs["date"] <= first_obs["date"]:
            continue
        ratio = last_obs["std_val"] / first_obs["std_val"] if first_obs["std_val"] else 1.0
        if 0.45 <= ratio < 1.0 and last_obs["unit_cost"] > first_obs["unit_cost"] * 1.01:
            unit_label = "€/kg" if std_unit == "g" else ("€/L" if std_unit == "ml" else "€/ud")
            unit_cost_pct = ((last_obs["unit_cost"] - first_obs["unit_cost"]) / first_obs["unit_cost"]) * 100.0
            size_pct = (ratio - 1.0) * 100.0
            shrinkflation_alerts.append({
                "base_name": base_sig,
                "previous_name": first_obs["name"],
                "current_name": last_obs["name"],
                "previous_size": _format_size(first_obs["std_val"], std_unit),
                "current_size": _format_size(last_obs["std_val"], std_unit),
                "unit": unit_label,
                "first_date": first_obs["date"],
                "last_date": last_obs["date"],
                "previous_price": round(first_obs["unit_price"], 2),
                "current_price": round(last_obs["unit_price"], 2),
                "previous_unit_cost": round(first_obs["unit_cost"], 2),
                "current_unit_cost": round(last_obs["unit_cost"], 2),
                "size_change_pct": round(size_pct, 1),
                "unit_cost_change_pct": round(unit_cost_pct, 1),
            })

    shrinkflation_alerts.sort(key=lambda a: -a["unit_cost_change_pct"])

    return {
        "personal_ipc_pct": personal_ipc_pct,
        "annualized_ipc_pct": annualized_ipc_pct,
        "span_days": span_days,
        "tracked_products": len(tracked_items),
        "rising_products": rising,
        "falling_products": falling,
        "stable_products": stable,
        "items": tracked_items[:25],
        "shrinkflation_alerts": shrinkflation_alerts,
    }


def compute_budget_summary(
    monthly_data: list[dict],
    monthly_categories: dict[str, dict[str, float]],
    budgets: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict | None:
    """Resumen activo de presupuesto mensual y proyección de ritmo diario."""
    if not budgets or not isinstance(budgets, dict):
        return None
    madrid_now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/Madrid"))
    current_ym = madrid_now.strftime("%Y-%m")

    by_month = {m["month"]: m for m in monthly_data}
    active_months = [m["month"] for m in monthly_data if m.get("visits", 0) > 0]

    if current_ym in by_month and by_month[current_ym].get("visits", 0) > 0:
        ref_month = current_ym
        is_current = True
    elif active_months:
        ref_month = active_months[-1]
        is_current = False
    else:
        ref_month = current_ym
        is_current = True

    year_i, month_i = map(int, ref_month.split("-"))
    days_in_month = calendar.monthrange(year_i, month_i)[1]
    days_elapsed = max(1, madrid_now.day) if is_current else days_in_month

    cat_limits = budgets.get("monthly") if isinstance(budgets.get("monthly"), dict) else {}
    cat_spent_map = monthly_categories.get(ref_month, {})
    categories_out = []
    limits_sum = 0.0
    for cat, limit in cat_limits.items():
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit <= 0:
            continue
        limit_f = float(limit)
        limits_sum += limit_f
        spent_f = round(float(cat_spent_map.get(cat, 0.0)), 2)
        pct = round((spent_f / limit_f) * 100.0, 1)
        status = "over" if pct >= 100.0 else ("warn" if pct >= 80.0 else "ok")
        categories_out.append({
            "category": cat,
            "spent": spent_f,
            "limit": round(limit_f, 2),
            "pct": pct,
            "status": status,
        })

    overall_spent = round(float(by_month.get(ref_month, {}).get("total", 0.0)), 2)
    raw_overall_limit = budgets.get("alert_monthly_total")
    overall_limit = (
        float(raw_overall_limit)
        if not isinstance(raw_overall_limit, bool) and isinstance(raw_overall_limit, (int, float)) and raw_overall_limit > 0
        else round(limits_sum, 2)
    )
    overall_pct = round((overall_spent / overall_limit) * 100.0, 1) if overall_limit > 0 else 0.0
    overall_status = (
        "over" if overall_limit > 0 and overall_pct >= 100.0
        else ("warn" if overall_limit > 0 and overall_pct >= 80.0 else "ok")
    )
    burn_rate_daily = round(overall_spent / days_elapsed, 2)
    projected_month_end = round((overall_spent / days_elapsed) * days_in_month, 2)

    return {
        "month": ref_month,
        "label": _month_label(ref_month),
        "is_current_month": is_current,
        "categories": categories_out,
        "overall": {
            "spent": overall_spent,
            "limit": round(overall_limit, 2),
            "pct": overall_pct,
            "status": overall_status,
            "burn_rate_daily": burn_rate_daily,
            "projected_month_end": projected_month_end,
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
        },
    }


def validate_ticket(data: dict[str, Any], filename: str) -> list[str]:
    """Valida el esquema de un ticket y devuelve lista de problemas encontrados."""
    try:
        normalize_ticket(data)
    except ValueError as exc:
        issue = str(exc)
        log.warning("Validación %s: %s", filename, issue)
        return [issue]
    return []


def load_ticket_json(f: Path) -> tuple[dict, str]:
    """Carga un JSON de ticket probando UTF-8 y latin-1 como fallback."""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = f.read_text(encoding=enc)
            return json.loads(text), enc
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"No se pudo leer {f.name}")


def load_all_tickets(tickets_dir: Path | None = None) -> list[dict]:
    target_dir = TICKETS_DIR if tickets_dir is None else tickets_dir
    tickets, failures = [], []
    for f in sorted(target_dir.glob("*.json")):
        try:
            data, _ = load_ticket_json(f)
            tickets.append(normalize_ticket(data))
        except (ValueError, OSError) as exc:
            log.error("Error leyendo %s: %s", f.name, exc)
            failures.append(f.name)
    if failures:
        raise ValueError(f"Tickets inválidos ({len(failures)}); no se actualizará el resumen")
    return tickets


def load_budgets(budgets_file: Path | None = None) -> dict | None:
    target = BUDGETS_FILE if budgets_file is None else budgets_file
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError) as exc:
        log.warning("No se pudo leer %s: %s", target, exc)
        return None


def _build_store_aliases(tickets: list[dict]) -> dict[str, str]:
    seen = []
    for t in tickets:
        raw = (t.get("store") or "Consum").strip() or "Consum"
        if raw not in seen:
            seen.append(raw)
    return {name: f"Consum #{idx + 1}" for idx, name in enumerate(seen)}


def build_summary(
    tickets: list[dict],
    *,
    budgets: dict | None = None,
    metadata: dict[str, dict] | None = None,
    now: datetime | None = None,
    anonymize: bool = False,
) -> dict:
    tickets = sorted(
        (normalize_ticket(t) for t in tickets),
        key=lambda t: (t["date"], t.get("datetime_iso") or "", str(t["id"])),
    )

    # ── Gasto mensual (rellenando meses intermedios sin compras) ──
    monthly_spending = defaultdict(float)
    monthly_visits = defaultdict(int)

    for t in tickets:
        month_key = t["date"][:7]
        monthly_spending[month_key] += t.get("total") or 0
        monthly_visits[month_key] += 1

    if tickets:
        month_keys = _iter_months(tickets[0]["date"][:7], tickets[-1]["date"][:7])
    else:
        month_keys = []

    monthly_data = [
        {
            "month": k,
            "label": _month_label(k),
            "total": round(monthly_spending[k], 2),
            "visits": monthly_visits[k],
        }
        for k in month_keys
    ]

    # ── Categorías, Marca Propia y Perfil NOVA ─────────────────
    category_totals = defaultdict(float)
    category_counts = defaultdict(int)
    monthly_categories: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    private_brand_spent = 0.0
    positive_items_spent = 0.0
    nova_totals = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
    nova_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    meta_lookup = metadata or {}

    for t in tickets:
        m_key = t["date"][:7]
        for item in t.get("items", []):
            cat = item.get("category", "Otros")
            line_total = item.get("total", 0)
            category_totals[cat] += line_total
            category_counts[cat] += 1
            monthly_categories[m_key][cat] += line_total

            if line_total > 0:
                positive_items_spent += line_total
                pkey = product_key(item["name"])
                inferred = meta_lookup.get(pkey) or infer_product_metadata(item["name"], cat)
                priv = item.get("is_private_brand")
                if not isinstance(priv, bool):
                    priv = inferred["is_private_brand"]
                if priv:
                    private_brand_spent += line_total

                if cat not in NON_FOOD_CATEGORIES and not NON_FOOD_NAME_RE.search(item["name"]):
                    nova_g = item.get("nova_group")
                    if type(nova_g) is not int or nova_g not in {1, 2, 3, 4}:
                        nova_g = inferred["nova_group"]
                    nova_totals[nova_g] += line_total
                    nova_counts[nova_g] += 1

    private_brand_share_pct = (
        round((private_brand_spent / positive_items_spent) * 100.0, 1)
        if positive_items_spent > 0
        else 0.0
    )
    food_spent = sum(nova_totals.values())
    nova_distribution = [
        {
            "group": g,
            "label": NOVA_LABELS[g],
            "total": round(nova_totals[g], 2),
            "pct": round((nova_totals[g] / food_spent) * 100.0, 1) if food_spent > 0 else 0.0,
            "count": nova_counts[g],
        }
        for g in (1, 2, 3, 4)
    ]

    categories = [
        {"name": k, "total": round(v, 2), "count": category_counts[k]}
        for k, v in sorted(category_totals.items(), key=lambda x: -x[1])
    ]

    # ── Top productos ──────────────────────────────────────────
    product_freq = defaultdict(int)
    product_total = defaultdict(float)
    for t in tickets:
        for item in t["items"]:
            key = (item["name"], item["category"])
            product_freq[key] += 1
            product_total[key] += item["total"]
    products = sorted(
        [
            {
                "name": name,
                "category": category,
                "times": count,
                "total_spent": round(product_total[(name, category)], 2),
                "is_private_brand": is_private_brand(name),
            }
            for (name, category), count in product_freq.items()
        ],
        key=lambda p: (-p["times"], p["name"], p["category"]),
    )
    top_products = products[:20]

    # ── Calidad de datos y Ahorro Mundo Consum ─────────────────
    issues = []
    verified_discounts_total = 0.0
    estimated_unparsed_discounts = 0.0
    tickets_with_savings = 0

    for ticket in tickets:
        report = reconcile_ticket(ticket)
        ticket_discounts = ticket.get("discounts") or []
        disc_abs = sum(abs(d["amount"]) for d in ticket_discounts if d["amount"] < 0)
        has_saving = False
        if disc_abs > 0:
            verified_discounts_total += disc_abs
            has_saving = True
        elif (
            ticket.get("source") == "dom_extracted"
            and not ticket_discounts
            and report["items_total"] > report["ticket_total"] + 0.02
        ):
            estimated_unparsed_discounts += report["items_total"] - report["ticket_total"]
            has_saving = True
        if has_saving:
            tickets_with_savings += 1

        if report["status"] != "complete":
            issue_entry = {"date": ticket["date"], **report}
            if not anonymize:
                issue_entry = {"id": ticket["id"], **issue_entry}
            issues.append(issue_entry)

    savings_summary = {
        "verified_discounts_total": round(verified_discounts_total, 2),
        "estimated_unparsed_discounts": round(estimated_unparsed_discounts, 2),
        "total_estimated_saved": round(verified_discounts_total + estimated_unparsed_discounts, 2),
        "tickets_with_savings": tickets_with_savings,
    }

    data_quality = {
        "complete_tickets": len(tickets) - len(issues),
        "review_tickets": sum(i["status"] == "needs_review" for i in issues),
        "missing_detail_tickets": sum(i["status"] == "incomplete" for i in issues),
        "issues": issues,
    }

    # ── Comparativas mes a mes ──────────────────────────────────
    for i, m in enumerate(monthly_data):
        if i == 0:
            m["change_pct"] = None
        else:
            prev = monthly_data[i - 1]["total"]
            m["change_pct"] = round(((m["total"] - prev) / prev) * 100, 1) if prev else None
        window = monthly_data[max(0, i - 2):i + 1]
        m["moving_avg"] = round(sum(w["total"] for w in window) / len(window), 2)

    # ── Stats generales ────────────────────────────────────────
    all_totals = [t["total"] for t in tickets if t.get("total") is not None]
    total_spent = round(sum(all_totals), 2)
    avg_ticket = round(total_spent / len(all_totals), 2) if all_totals else 0
    avg_monthly = round(total_spent / len(monthly_data), 2) if monthly_data else 0

    # ── Inflación personal y Presupuestos ──────────────────────
    inflation = compute_inflation(tickets, metadata=meta_lookup)
    budget_summary = compute_budget_summary(monthly_data, monthly_categories, budgets, now=now)

    # ── Tips ───────────────────────────────────────────────────
    health_tips = generate_health_tips(category_totals, total_spent, nova_distribution=nova_distribution)
    saving_tips = generate_saving_tips(category_totals, monthly_data, avg_ticket, top_products)

    # ── Tickets recientes (últimos 10) ─────────────────────────
    store_aliases = _build_store_aliases(tickets) if anonymize else {}
    recent_tickets = [
        {
            "date": t["date"],
            "store": (
                store_aliases.get((t.get("store") or "Consum").strip() or "Consum", "Consum #1")
                if anonymize
                else t.get("store", "Consum")
            ),
            "total": t.get("total"),
            "items_count": len(t.get("items", [])),
            "payment": (t.get("payment_method") or "").strip(),
            "needs_review": reconcile_ticket(t)["status"] != "complete",
        }
        for t in reversed(tickets[-10:])
    ]

    result = {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "total_tickets": len(tickets),
        "total_spent": total_spent,
        "avg_ticket": avg_ticket,
        "avg_monthly_spent": avg_monthly,
        "savings_summary": savings_summary,
        "private_brand_share_pct": private_brand_share_pct,
        "nova_distribution": nova_distribution,
        "inflation": inflation,
        "budget_summary": budget_summary,
        "monthly_data": monthly_data,
        "categories": categories,
        "top_products": top_products,
        "products": products,
        "monthly_categories": {
            month: [{"name": cat, "total": round(total, 2)}
                    for cat, total in sorted(cats.items())]
            for month, cats in sorted(monthly_categories.items())
        },
        "recent_tickets": recent_tickets,
        "data_quality": data_quality,
        "health_tips": health_tips,
        "saving_tips": saving_tips,
    }
    if anonymize:
        result["anonymized"] = True
    return result


def generate_health_tips(
    category_totals: dict,
    total_spent: float,
    *,
    nova_distribution: list[dict] | None = None,
) -> list[dict]:
    tips = []
    denom = max(total_spent, sum(category_totals.values()))
    if denom == 0:
        return [{"icon": "", "type": "success", "title": "Sin datos aún", "text": "Ejecuta el scraper para ver recomendaciones."}]

    fv_pct = (category_totals.get("Frutas y Verduras", 0) / denom) * 100
    snack_pct = (category_totals.get("Snacks y Dulces", 0) / denom) * 100
    processed_pct = (category_totals.get("Congelados y Preparados", 0) / denom) * 100
    beverages_pct = (category_totals.get("Bebidas", 0) / denom) * 100

    if fv_pct < 10:
        tips.append({
            "icon": "", "type": "warning", "title": "Poca fruta y verdura",
            "text": f"Solo el {fv_pct:.1f}% de tu gasto es en frutas/verduras. Los expertos recomiendan al menos 5 raciones al día.",
        })
    elif fv_pct >= 20:
        tips.append({
            "icon": "", "type": "success", "title": "¡Genial consumo de vegetales!",
            "text": f"El {fv_pct:.1f}% de tu gasto va a frutas y verduras. ¡Sigue así!",
        })

    if snack_pct > 15:
        tips.append({
            "icon": "", "type": "warning", "title": "Alto consumo de snacks",
            "text": f"El {snack_pct:.1f}% de tu gasto son snacks y dulces. Intenta sustituirlos por fruta o frutos secos.",
        })

    if processed_pct > 20:
        tips.append({
            "icon": "", "type": "info", "title": "Muchos alimentos procesados",
            "text": f"El {processed_pct:.1f}% es en congelados/preparados. Cocinar más en casa suele ser más sano y económico.",
        })

    if nova_distribution:
        nova4 = next((g for g in nova_distribution if g.get("group") == 4), None)
        if nova4 and nova4.get("pct", 0) > 25.0:
            tips.append({
                "icon": "", "type": "warning", "title": "Alto peso de ultraprocesados (NOVA 4)",
                "text": f"El {nova4['pct']:.1f}% del gasto alimentario corresponde a ultraprocesados (NOVA 4). Priorizar frescos (NOVA 1) mejora tu perfil nutricional.",
            })

    if beverages_pct > 10:
        tips.append({
            "icon": "", "type": "info", "title": "Gasto elevado en bebidas",
            "text": f"El {beverages_pct:.1f}% en bebidas. El agua del grifo filtrada puede ahorrarte dinero.",
        })

    if not tips:
        tips.append({
            "icon": "", "type": "success", "title": "Cesta equilibrada",
            "text": "Tu distribución de compra parece variada y equilibrada.",
        })

    return tips


def generate_saving_tips(category_totals, monthly_data, avg_ticket, top_products) -> list[dict]:
    tips = []

    if len(monthly_data) >= 3:
        last3 = monthly_data[-3:]
        if last3[0]["total"] > 0 and last3[-1]["total"] > last3[0]["total"] * 1.15:
            pct = ((last3[-1]["total"] / last3[0]["total"]) - 1) * 100
            tips.append({
                "icon": "", "type": "warning", "title": "Gasto creciente",
                "text": f"Tu gasto ha subido un {pct:.0f}% en los últimos 3 meses.",
            })

    consum_brand = sum(1 for p in top_products if is_private_brand(p["name"]))
    if consum_brand < 3 and len(top_products) > 5:
        tips.append({
            "icon": "", "type": "info", "title": "Prueba la marca propia",
            "text": "Los productos de marca propia (Consum, Kyrey, Bonté) suelen ser un 20-30% más económicos con calidad similar.",
        })

    if monthly_data:
        last = monthly_data[-1]
        if last["visits"] >= 8:
            tips.append({
                "icon": "", "type": "info", "title": "Muchas visitas al súper",
                "text": f"Fuiste {last['visits']} veces el último mes. Comprar menos veces y con lista suele reducir el gasto impulsivo.",
            })

    if category_totals.get("Bebidas", 0) > 20:
        tips.append({
            "icon": "", "type": "info", "title": "Ahorra en agua embotellada",
            "text": "Un filtro de grifo cuesta ~15€ y se amortiza en pocas semanas si compras agua regularmente.",
        })

    if not tips:
        tips.append({
            "icon": "", "type": "success", "title": "Buen control del gasto",
            "text": "No se detectan patrones de gasto excesivo. ¡Sigue con tu lista!",
        })

    return tips


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generar el resumen del dashboard")
    parser.add_argument("--require-gemini", action="store_true")
    parser.add_argument("--refresh-categories", action="store_true")
    parser.add_argument("--anonymize", action="store_true", help="Ocultar tiendas concretas, direcciones e IDs de ticket")
    parser.add_argument("--out", type=Path, default=None, help="Ruta de salida opcional para summary.json")
    args = parser.parse_args(argv)
    try:
        log.info("Cargando tickets...")
        tickets = load_all_tickets()
        log.info("%d tickets cargados", len(tickets))
        classification = None
        if Path("data/categories.json").exists():
            tickets, classification = classify_tickets(
                tickets, root=Path.cwd(), strict=args.require_gemini, refresh=args.refresh_categories
            )
            if classification["enabled"]:
                log.info(
                    "Gemini: %d/%d productos clasificados; %d pendientes",
                    classification["classified_products"],
                    classification["total_products"],
                    classification["pending_products"],
                )
        elif args.require_gemini:
            raise GeminiError("Falta data/categories.json para definir las categorías permitidas")
        if not tickets:
            log.warning("No hay tickets. Se generará un resumen vacío.")
        budgets = load_budgets()
        metadata = load_product_metadata(Path.cwd())
        summary = build_summary(
            tickets,
            budgets=budgets,
            metadata=metadata,
            anonymize=args.anonymize,
        )
        if classification is not None:
            summary["classification"] = classification
        target_out = args.out if args.out is not None else OUTPUT_FILE
        target_out.parent.mkdir(parents=True, exist_ok=True)
        temporary = target_out.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(target_out)
        log.info("summary.json generado: %d tickets", summary["total_tickets"])
        return 0
    except (GeminiError, ValueError, OSError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
