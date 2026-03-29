"""
build_data.py — Agrega todos los JSON de tickets en un único summary.json
que leerá el dashboard.
"""

import json
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_data")

TICKETS_DIR = Path("data/tickets")
OUTPUT_FILE = Path("data/summary.json")

REQUIRED_TICKET_FIELDS = {"id", "date", "total", "items", "source"}
REQUIRED_ITEM_FIELDS = {"name", "quantity", "total", "category"}


def validate_ticket(data: dict[str, Any], filename: str) -> list[str]:
    """Valida el esquema de un ticket y devuelve lista de problemas encontrados."""
    issues: list[str] = []
    missing = REQUIRED_TICKET_FIELDS - set(data.keys())
    if missing:
        issues.append(f"Campos faltantes: {missing}")
    if not isinstance(data.get("items"), list):
        issues.append("'items' no es una lista")
    elif data["items"]:
        for i, item in enumerate(data["items"]):
            item_missing = REQUIRED_ITEM_FIELDS - set(item.keys())
            if item_missing:
                issues.append(f"Item {i}: campos faltantes {item_missing}")
    if data.get("total") is not None and not isinstance(data["total"], (int, float)):
        issues.append(f"'total' no es numérico: {type(data['total']).__name__}")
    if issues:
        for issue in issues:
            log.warning("Validación %s: %s", filename, issue)
    return issues


def load_ticket_json(f: Path) -> tuple[dict, str]:
    """Carga un JSON de ticket probando UTF-8 y latin-1 como fallback."""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = f.read_text(encoding=enc)
            return json.loads(text), enc
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"No se pudo leer {f.name}")


def load_all_tickets() -> list[dict]:
    tickets = []
    for f in sorted(TICKETS_DIR.glob("*.json")):
        try:
            data, used_encoding = load_ticket_json(f)

            # Normalizar a UTF-8 si hacía falta
            if used_encoding != "utf-8":
                log.info("Normalizando %s desde %s a utf-8", f.name, used_encoding)
                f.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            validate_ticket(data, f.name)

            if data.get("date"):
                tickets.append(data)
        except Exception as e:
            log.error("Error leyendo %s: %s", f.name, e)
    return tickets


def build_summary(tickets: list[dict]) -> dict:
    tickets.sort(key=lambda t: t["date"])

    # ── Gasto mensual ──────────────────────────────────────────
    monthly_spending = defaultdict(float)
    monthly_visits   = defaultdict(int)

    for t in tickets:
        month_key = t["date"][:7]
        monthly_spending[month_key] += t.get("total") or 0
        monthly_visits[month_key]   += 1

    monthly_data = [
        {
            "month":  k,
            "label":  datetime.strptime(k, "%Y-%m").strftime("%b %Y"),
            "total":  round(v, 2),
            "visits": monthly_visits[k],
        }
        for k, v in sorted(monthly_spending.items())
    ]

    # ── Categorías ─────────────────────────────────────────────
    category_totals = defaultdict(float)
    category_counts = defaultdict(int)

    for t in tickets:
        for item in t.get("items", []):
            cat = item.get("category", "Otros")
            category_totals[cat] += item.get("total", 0)
            category_counts[cat] += 1

    categories = [
        {"name": k, "total": round(v, 2), "count": category_counts[k]}
        for k, v in sorted(category_totals.items(), key=lambda x: -x[1])
    ]

    # ── Top productos ──────────────────────────────────────────
    product_freq  = defaultdict(int)
    product_total = defaultdict(float)

    for t in tickets:
        for item in t.get("items", []):
            name = item["name"]
            product_freq[name]  += 1
            product_total[name] += item.get("total", 0)

    top_products = sorted(
        [
            {"name": k, "times": v, "total_spent": round(product_total[k], 2)}
            for k, v in product_freq.items()
        ],
        key=lambda x: -x["times"],
    )[:20]

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
    all_totals  = [t["total"] for t in tickets if t.get("total")]
    total_spent = round(sum(all_totals), 2)
    avg_ticket  = round(total_spent / len(all_totals), 2) if all_totals else 0
    avg_monthly = round(total_spent / len(monthly_data), 2) if monthly_data else 0

    # ── Tips ───────────────────────────────────────────────────
    health_tips = generate_health_tips(category_totals, total_spent)
    saving_tips = generate_saving_tips(category_totals, monthly_data, avg_ticket, top_products)

    # ── Tickets recientes (últimos 10) ─────────────────────────
    recent_tickets = [
        {
            "date":        t["date"],
            "store":       t.get("store", "Consum"),
            "total":       t.get("total"),
            "items_count": len(t.get("items", [])),
            "payment":     t.get("payment_method", ""),
        }
        for t in reversed(tickets[-10:])
    ]

    return {
        "generated_at":     datetime.now().isoformat(),
        "total_tickets":    len(tickets),
        "total_spent":      total_spent,
        "avg_ticket":       avg_ticket,
        "avg_monthly_spent": avg_monthly,
        "monthly_data":     monthly_data,
        "categories":       categories,
        "top_products":     top_products,
        "recent_tickets":   recent_tickets,
        "health_tips":      health_tips,
        "saving_tips":      saving_tips,
    }


def generate_health_tips(category_totals: dict, total_spent: float) -> list[dict]:
    tips = []
    if total_spent == 0:
        return [{"icon":"","type":"success","title":"Sin datos aún","text":"Ejecuta el scraper para ver recomendaciones."}]

    fv_pct        = (category_totals.get("Frutas y Verduras", 0) / total_spent) * 100
    snack_pct     = (category_totals.get("Snacks y Dulces", 0) / total_spent) * 100
    processed_pct = (category_totals.get("Congelados y Preparados", 0) / total_spent) * 100
    beverages_pct = (category_totals.get("Bebidas", 0) / total_spent) * 100

    if fv_pct < 10:
        tips.append({"icon":"","type":"warning","title":"Poca fruta y verdura",
            "text":f"Solo el {fv_pct:.1f}% de tu gasto es en frutas/verduras. Los expertos recomiendan al menos 5 raciones al día."})
    elif fv_pct >= 20:
        tips.append({"icon":"","type":"success","title":"¡Genial consumo de vegetales!",
            "text":f"El {fv_pct:.1f}% de tu gasto va a frutas y verduras. ¡Sigue así!"})

    if snack_pct > 15:
        tips.append({"icon":"","type":"warning","title":"Alto consumo de snacks",
            "text":f"El {snack_pct:.1f}% de tu gasto son snacks y dulces. Intenta sustituirlos por fruta o frutos secos."})

    if processed_pct > 20:
        tips.append({"icon":"","type":"info","title":"Muchos alimentos procesados",
            "text":f"El {processed_pct:.1f}% es en congelados/preparados. Cocinar más en casa suele ser más sano y económico."})

    if beverages_pct > 10:
        tips.append({"icon":"","type":"info","title":"Gasto elevado en bebidas",
            "text":f"El {beverages_pct:.1f}% en bebidas. El agua del grifo filtrada puede ahorrarte dinero."})

    if not tips:
        tips.append({"icon":"","type":"success","title":"Cesta equilibrada",
            "text":"Tu distribución de compra parece variada y equilibrada."})

    return tips


def generate_saving_tips(category_totals, monthly_data, avg_ticket, top_products) -> list[dict]:
    tips = []

    if len(monthly_data) >= 3:
        last3 = monthly_data[-3:]
        if last3[0]["total"] > 0 and last3[-1]["total"] > last3[0]["total"] * 1.15:
            pct = ((last3[-1]["total"] / last3[0]["total"]) - 1) * 100
            tips.append({"icon":"","type":"warning","title":"Gasto creciente",
                "text":f"Tu gasto ha subido un {pct:.0f}% en los últimos 3 meses."})

    consum_brand = sum(1 for p in top_products
        if "CONSUM" in p["name"].upper() or "HACENDADO" in p["name"].upper())
    if consum_brand < 3 and len(top_products) > 5:
        tips.append({"icon":"","type":"info","title":"Prueba la marca propia",
            "text":"Los productos de marca Consum suelen ser un 20-30% más baratos con calidad similar."})

    if monthly_data:
        last = monthly_data[-1]
        if last["visits"] >= 8:
            tips.append({"icon":"","type":"info","title":"Muchas visitas al súper",
                "text":f"Fuiste {last['visits']} veces el último mes. Comprar menos veces y con lista suele reducir el gasto impulsivo."})

    if category_totals.get("Bebidas", 0) > 20:
        tips.append({"icon":"","type":"info","title":"Ahorra en agua embotellada",
            "text":"Un filtro de grifo cuesta ~15€ y se amortiza en pocas semanas si compras agua regularmente."})

    if not tips:
        tips.append({"icon":"","type":"success","title":"Buen control del gasto",
            "text":"No se detectan patrones de gasto excesivo. ¡Sigue con tu lista!"})

    return tips


if __name__ == "__main__":
    log.info("Cargando tickets...")
    tickets = load_all_tickets()
    log.info("%d tickets cargados", len(tickets))

    if not tickets:
        log.warning("No hay tickets. Ejecuta primero: python scripts/scraper.py")
    else:
        log.info("Generando summary.json...")
        summary = build_summary(tickets)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("summary.json generado:")
        log.info("  · %d tickets", summary["total_tickets"])
        log.info("  · %s€ gasto total", summary["total_spent"])
        log.info("  · %s€ ticket medio", summary["avg_ticket"])
        log.info("  · %s€ media mensual", summary["avg_monthly_spent"])
        log.info("  · %d categorías", len(summary["categories"]))
