"""
notify_telegram.py — Envía un resumen proactivo en español a Telegram cuando entran
tickets nuevos (stdlib pura, sin exponer TELEGRAM_BOT_TOKEN en argumentos ni logs).
"""

import argparse
import html
import json
import logging
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gemini_categories import canonical_product_name, product_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("notify_telegram")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUMMARY = Path("data/summary.json")
DEFAULT_BUDGETS = Path("data/budgets.json")
DEFAULT_TICKETS_DIR = Path("data/tickets")


class TelegramNotifyError(RuntimeError):
    """Error seguro al enviar mensaje a Telegram (nunca contiene el token)."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fmt_date(iso_date: str | None) -> str:
    if not iso_date or "-" not in str(iso_date):
        return "—"
    parts = str(iso_date).split("-")
    if len(parts) == 3:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return str(iso_date)


def _latest_ticket_product_keys(latest_date: str, tickets_dir: Path | None) -> set[str]:
    if not tickets_dir or not tickets_dir.exists():
        return set()
    matched_tickets = []
    for f in sorted(tickets_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            if data.get("date") == latest_date:
                matched_tickets.append(data)
        except (OSError, ValueError):
            continue
    if not matched_tickets:
        return set()
    matched_tickets.sort(key=lambda t: (t.get("datetime_iso") or "", str(t.get("id") or "")))
    latest = matched_tickets[-1]
    keys = set()
    for it in latest.get("items", []):
        if isinstance(it, dict) and isinstance(it.get("name"), str):
            keys.add(product_key(it["name"]))
            keys.add(canonical_product_name(it["name"]))
    return keys


def build_digest(
    summary: dict,
    budgets: dict | None = None,
    *,
    tickets_dir: Path | None = None,
) -> str:
    """Construye el mensaje HTML en español con el resumen de la última compra y presupuestos."""
    recent = summary.get("recent_tickets") or []
    lines = ["🛒 <b>Consum Tracker — Resumen de compra</b>"]

    latest_date = None
    if recent:
        latest = recent[0]
        latest_date = latest.get("date")
        store = html.escape(str(latest.get("store") or "Consum"))
        total = float(latest.get("total") or 0.0)
        items_count = int(latest.get("items_count") or 0)
        review_tag = " ⚠️ <i>(pendiente de revisión)</i>" if latest.get("needs_review") else ""
        lines.append(
            f"🧾 <b>Último ticket</b>: {_fmt_date(latest_date)} · {store} · "
            f"<b>{total:.2f} €</b> ({items_count} art.){review_tag}"
        )
    else:
        lines.append("🧾 Sin compras registradas todavía.")

    bsum = summary.get("budget_summary")
    if bsum and isinstance(bsum, dict):
        overall = bsum.get("overall") or {}
        m_label = html.escape(str(bsum.get("label") or bsum.get("month") or "mes actual"))
        spent = float(overall.get("spent") or 0.0)
        limit = float(overall.get("limit") or 0.0)
        pct = float(overall.get("pct") or 0.0)
        burn = float(overall.get("burn_rate_daily") or 0.0)
        proj = float(overall.get("projected_month_end") or 0.0)
        if limit > 0:
            lines.append(f"📊 <b>Gasto {m_label}</b>: {spent:.2f} € / {limit:.2f} € ({pct:.1f}%)")
        else:
            lines.append(f"📊 <b>Gasto {m_label}</b>: {spent:.2f} €")
        lines.append(f"⏱️ <b>Ritmo diario</b>: {burn:.2f} €/día · <b>Proyección fin de mes</b>: {proj:.2f} €")

        warn_cats = [
            c for c in (bsum.get("categories") or [])
            if isinstance(c, dict) and float(c.get("pct") or 0.0) >= 80.0
        ]
        if warn_cats:
            warn_parts = []
            for c in warn_cats:
                icon = "🚨" if c.get("status") == "over" or float(c.get("pct", 0)) >= 100.0 else "⚠️"
                cat_name = html.escape(str(c.get("category", "")))
                warn_parts.append(
                    f"{icon} {cat_name}: {float(c.get('spent', 0)):.2f} € / {float(c.get('limit', 0)):.2f} € ({float(c.get('pct', 0)):.0f}%)"
                )
            lines.append("🎯 <b>Categorías ≥ 80% del presupuesto</b>:\n  • " + "\n  • ".join(warn_parts))
    elif budgets and isinstance(budgets, dict) and summary.get("monthly_data"):
        last_m = summary["monthly_data"][-1]
        m_label = html.escape(str(last_m.get("label") or last_m.get("month") or ""))
        spent = float(last_m.get("total") or 0.0)
        limit = float(budgets.get("alert_monthly_total") or 0.0)
        if limit > 0:
            pct = (spent / limit) * 100.0
            lines.append(f"📊 <b>Gasto {m_label}</b>: {spent:.2f} € / {limit:.2f} € ({pct:.1f}%)")

    # Subidas de precio en artículos del último ticket
    inflation = summary.get("inflation") or {}
    inf_items = inflation.get("items") or []
    hikes = []
    if latest_date and inf_items:
        latest_keys = _latest_ticket_product_keys(latest_date, tickets_dir)
        for it in inf_items:
            if float(it.get("change_pct") or 0.0) <= 0:
                continue
            it_key = product_key(str(it.get("name") or ""))
            it_canon = canonical_product_name(str(it.get("canonical_name") or it.get("name") or ""))
            if (latest_keys and (it_key in latest_keys or it_canon in latest_keys)) or (
                not latest_keys and it.get("last_date") == latest_date
            ):
                hikes.append(it)
    if hikes:
        hike_parts = [
            f"{html.escape(str(h.get('name')))}: {float(h['first_unit_price']):.2f} € → "
            f"{float(h['last_unit_price']):.2f} € (+{float(h['change_pct']):.1f}%)"
            for h in hikes[:5]
        ]
        lines.append("📈 <b>Subidas de precio en esta compra</b>:\n  • " + "\n  • ".join(hike_parts))

    if inflation.get("tracked_products"):
        ipc = float(inflation.get("personal_ipc_pct") or 0.0)
        sign = "+" if ipc > 0 else ""
        lines.append(f"📌 <b>Tu IPC personal</b>: {sign}{ipc:.2f}% ({inflation['tracked_products']} productos recurrentes)")

    return "\n".join(lines)


def send_telegram_message(text: str, *, bot_token: str, chat_id: str, timeout: float = 20.0) -> None:
    """Envía el mensaje a la API de Telegram ocultando siempre el token ante cualquier fallo."""
    token = (bot_token or "").strip()
    target_chat = (chat_id or "").strip()
    if not token or not target_chat:
        raise TelegramNotifyError("Faltan credenciales de Telegram")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode({
        "chat_id": target_chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    req = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
    )
    opener = build_opener(_NoRedirects())
    try:
        with opener.open(req, timeout=timeout) as resp:
            payload = json.loads(resp.read(65536).decode("utf-8", errors="replace"))
            if not payload.get("ok"):
                raise TelegramNotifyError("Telegram rechazó el envío del resumen")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise TelegramNotifyError(f"Telegram devolvió HTTP {code} al enviar el resumen") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise TelegramNotifyError("No se pudo conectar con la API de Telegram") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Enviar resumen de compra a Telegram")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY, help="Ruta a summary.json")
    parser.add_argument("--budgets", type=Path, default=DEFAULT_BUDGETS, help="Ruta a budgets.json")
    parser.add_argument("--tickets-dir", type=Path, default=DEFAULT_TICKETS_DIR, help="Directorio de tickets")
    parser.add_argument("--dry-run", action="store_true", help="Imprimir el resumen sin enviarlo por red")
    args = parser.parse_args(argv)

    summary_path = args.summary if args.summary.exists() else (ROOT / args.summary)
    if not summary_path.exists():
        log.error("No existe %s; ejecuta primero build_data.py", args.summary)
        return 1

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        log.error("No se pudo leer %s", summary_path)
        return 1

    budgets_path = args.budgets if args.budgets.exists() else (ROOT / args.budgets)
    budgets = None
    if budgets_path.exists():
        try:
            budgets = json.loads(budgets_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            budgets = None

    tickets_dir = args.tickets_dir if args.tickets_dir.exists() else (ROOT / args.tickets_dir)
    digest = build_digest(summary, budgets, tickets_dir=tickets_dir)

    if args.dry_run:
        print(digest)
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no definidos; se omite la notificación.")
        return 0

    try:
        send_telegram_message(digest, bot_token=token, chat_id=chat_id)
        log.info("Resumen de compra enviado a Telegram.")
        return 0
    except TelegramNotifyError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
