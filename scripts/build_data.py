"""
build_data.py — Agrega todos los JSON de tickets en un único summary.json
que leerá el dashboard.
"""

import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, date

TICKETS_DIR = Path("data/tickets")
OUTPUT_FILE = Path("data/summary.json")


def load_all_tickets() -> list[dict]:
    tickets = []
    for f in sorted(TICKETS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Ignorar los que no tienen fecha (no se parsearon bien)
            if data.get("date") and data.get("items"):
                tickets.append(data)
        except Exception as e:
            print(f"  ⚠️  Error leyendo {f.name}: {e}")
    return tickets


def build_summary(tickets: list[dict]) -> dict:
    # Ordenar por fecha
    tickets.sort(key=lambda t: t["date"])

    # ── Gasto mensual ──────────────────────────────────────────
    monthly_spending = defaultdict(float)
    monthly_visits = defaultdict(int)

    for t in tickets:
        month_key = t["date"][:7]  # "2026-03"
        monthly_spending[month_key] += t.get("total") or 0
        monthly_visits[month_key] += 1

    monthly_data = [
        {
            "month": k,
            "label": datetime.strptime(k, "%Y-%m").strftime("%b %Y"),
            "total": round(v, 2),
            "visits": monthly_visits[k],
        }
        for k, v in sorted(monthly_spending.items())
    ]

    # ── Categorías (global y por mes) ─────────────────────────
    category_totals = defaultdict(float)
    category_counts = defaultdict(int)

    for t in tickets:
        for item in t.get("items", []):
            cat = item.get("category", "Otros")
            category_totals[cat] += item.get("total", 0)
            category_counts[cat] += 1

    categories = [
        {
            "name": k,
            "total": round(v, 2),
            "count": category_counts[k],
        }
        for k, v in sorted(category_totals.items(), key=lambda x: -x[1])
    ]

    # ── Top productos ──────────────────────────────────────────
    product_freq = defaultdict(int)
    product_total = defaultdict(float)

    for t in tickets:
        for item in t.get("items", []):
            name = item["name"]
            product_freq[name] += 1
            product_total[name] += item.get("total", 0)

    top_products = sorted(
        [
            {"name": k, "times": v, "total_spent": round(product_total[k], 2)}
            for k, v in product_freq.items()
        ],
        key=lambda x: -x["times"],
    )[:20]

    # ── Estadísticas generales ────────────────────────────────
    all_totals = [t["total"] for t in tickets if t.get("total")]
    total_spent = round(sum(all_totals), 2)
    avg_ticket = round(total_spent / len(all_totals), 2) if all_totals else 0
    avg_monthly = (
        round(total_spent / len(monthly_data), 2) if monthly_data else 0
    )

    # ── Recomendaciones de salud ──────────────────────────────
    health_tips = generate_health_tips(category_totals, total_spent)

    # ── Recomendaciones de ahorro ─────────────────────────────
    saving_tips = generate_saving_tips(
        category_totals, monthly_data, avg_ticket, top_products
    )

    # ── Tickets recientes (últimos 10) ────────────────────────
    recent_tickets = []
    for t in reversed(tickets[-10:]):
        recent_tickets.append({
            "date": t["date"],
            "store": t.get("store", "Consum"),
            "total": t.get("total"),
            "items_count": len(t.get("items", [])),
            "payment": t.get("payment_method", ""),
        })

    return {
        "generated_at": datetime.now().isoformat(),
        "total_tickets": len(tickets),
        "total_spent": total_spent,
        "avg_ticket": avg_ticket,
        "avg_monthly_spent": avg_monthly,
        "monthly_data": monthly_data,
        "categories": categories,
        "top_products": top_products,
        "recent_tickets": recent_tickets,
        "health_tips": health_tips,
        "saving_tips": saving_tips,
    }


def generate_health_tips(category_totals: dict, total_spent: float) -> list[dict]:
    tips = []
    if total_spent == 0:
        return tips

    fv_pct = (category_totals.get("Frutas y Verduras", 0) / total_spent) * 100
    snack_pct = (category_totals.get("Snacks y Dulces", 0) / total_spent) * 100
    processed_pct = (category_totals.get("Congelados y Preparados", 0) / total_spent) * 100
    beverages_pct = (category_totals.get("Bebidas", 0) / total_spent) * 100

    if fv_pct < 10:
        tips.append({
            "icon": "🥦",
            "type": "warning",
            "title": "Poca fruta y verdura",
            "text": f"Solo el {fv_pct:.1f}% de tu gasto es en frutas/verduras. "
                    f"Los expertos recomiendan al menos 5 raciones al día.",
        })
    elif fv_pct >= 20:
        tips.append({
            "icon": "🥗",
            "type": "success",
            "title": "¡Genial consumo de vegetales!",
            "text": f"El {fv_pct:.1f}% de tu gasto va a frutas y verduras. ¡Sigue así!",
        })

    if snack_pct > 15:
        tips.append({
            "icon": "🍬",
            "type": "warning",
            "title": "Alto consumo de snacks",
            "text": f"El {snack_pct:.1f}% de tu gasto son snacks y dulces. "
                    f"Intenta sustituirlos por fruta o frutos secos.",
        })

    if processed_pct > 20:
        tips.append({
            "icon": "🍕",
            "type": "info",
            "title": "Muchos alimentos procesados",
            "text": f"El {processed_pct:.1f}% es en congelados/preparados. "
                    f"Cocinar más en casa suele ser más sano y económico.",
        })

    if beverages_pct > 10:
        tips.append({
            "icon": "🥤",
            "type": "info",
            "title": "Gasto elevado en bebidas",
            "text": f"El {beverages_pct:.1f}% en bebidas. "
                    f"El agua del grifo filtrada puede ahorrarte dinero.",
        })

    if not tips:
        tips.append({
            "icon": "✅",
            "type": "success",
            "title": "Cesta equilibrada",
            "text": "Tu distribución de compra parece variada y equilibrada.",
        })

    return tips


def generate_saving_tips(
    category_totals: dict,
    monthly_data: list,
    avg_ticket: float,
    top_products: list,
) -> list[dict]:
    tips = []

    # Gasto creciente mes a mes
    if len(monthly_data) >= 3:
        last_three = monthly_data[-3:]
        if last_three[-1]["total"] > last_three[0]["total"] * 1.15:
            tips.append({
                "icon": "📈",
                "type": "warning",
                "title": "Gasto creciente",
                "text": f"Tu gasto ha subido un "
                        f"{((last_three[-1]['total']/last_three[0]['total'])-1)*100:.0f}% "
                        f"en los últimos 3 meses.",
            })

    # Marca blanca
    consum_brand = sum(
        1 for p in top_products
        if "CONSUM" in p["name"].upper() or "HACENDADO" in p["name"].upper()
    )
    if consum_brand < 3 and len(top_products) > 5:
        tips.append({
            "icon": "🏷️",
            "type": "info",
            "title": "Prueba la marca propia",
            "text": "Los productos de marca Consum suelen ser un 20-30% más baratos "
                    "con calidad similar.",
        })

    # Muchas visitas
    if monthly_data:
        last_month = monthly_data[-1]
        if last_month["visits"] >= 8:
            tips.append({
                "icon": "🛒",
                "type": "info",
                "title": "Muchas visitas al súper",
                "text": f"Fuiste {last_month['visits']} veces el último mes. "
                        f"Comprar menos veces y con lista suele reducir el gasto impulsivo.",
            })

    # Bebidas envasadas
    if category_totals.get("Bebidas", 0) > 20:
        tips.append({
            "icon": "💧",
            "type": "info",
            "title": "Ahorra en agua embotellada",
            "text": "Un filtro de grifo cuesta ~15€ y se amortiza en pocas semanas "
                    "si compras agua regularmente.",
        })

    if not tips:
        tips.append({
            "icon": "💰",
            "type": "success",
            "title": "Buen control del gasto",
            "text": "No se detectan patrones de gasto excesivo. ¡Sigue con tu lista!",
        })

    return tips


if __name__ == "__main__":
    print("📂 Cargando tickets...")
    tickets = load_all_tickets()
    print(f"  ✅ {len(tickets)} tickets cargados")

    if not tickets:
        print("⚠️  No hay tickets. Asegúrate de haber ejecutado parse_pdf.py primero.")
    else:
        print("🔧 Generando summary.json...")
        summary = build_summary(tickets)
        OUTPUT_FILE.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  ✅ summary.json generado:")
        print(f"     - {summary['total_tickets']} tickets")
        print(f"     - {summary['total_spent']}€ gasto total")
        print(f"     - {len(summary['categories'])} categorías")
        print(f"     - {len(summary['health_tips'])} consejos de salud")
        print(f"     - {len(summary['saving_tips'])} consejos de ahorro")
