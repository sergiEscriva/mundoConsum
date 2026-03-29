"""
scraper.py — Versión final.
Descubrimiento clave: ticketdetail.html acepta parámetros directos:
  ?ticketId=ID&pass=TOKEN&dni=&v=20251201
No hace falta mytickets.html, scroll ni clics.
Simplemente navegar a esa URL carga el ticket y el <pre> está en el DOM.
"""
import asyncio, json, logging, os, re, sys, html as html_module
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

try:
    from playwright.async_api import async_playwright, BrowserContext
except ImportError:
    logging.error("Playwright no instalado")
    sys.exit(1)

MAIN_URL    = "https://www.consum.es/mundoconsum/mi-ahorro/"
API_BASE    = "https://nomastickets.consum.es/api"
TICKETS_DIR = Path("data/tickets")
TICKETS_DIR.mkdir(parents=True, exist_ok=True)
PAGE_SIZE   = 10

CATEGORIES_FILE = Path("data/categories.json")

def load_categories() -> dict[str, list[str]]:
    if CATEGORIES_FILE.exists():
        return json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
    log.warning("No se encontró %s, usando categorías por defecto", CATEGORIES_FILE)
    return {}

CATEGORIES = load_categories()
_uncategorized: set[str] = set()

SKIP = {"FACTURA","IMPORTE","CAMBIO","TOTAL","BASE","IVA","CUOTA",
        "SILLA","SOCIO","TARJ","BBVA","VISA","OPERACI","AUTORI",
        "ATENDIDO","ABONAR","CLIENTE","COOP.V","NIF","ALGINET",
        "ARQUITECTO","S.COOP","F46078986"}

def categorize(name):
    n = name.upper()
    for cat, kws in CATEGORIES.items():
        if any(kw in n for kw in kws):
            return cat
    if n not in _uncategorized:
        _uncategorized.add(n)
        log.debug("Producto sin categoría: %s", name)
    return "Otros"

def parse_items_from_pre(pre_text: str) -> list:
    text = html_module.unescape(pre_text)
    item_pat = re.compile(
        r'<normal-pequenya>\s*(\d+(?:,\d+)?)\s*</normal-pequenya>'
        r'([A-ZÁÉÍÓÚÜÑ][^\n<]{2,40}?)\s*'
        r'<normal-pequenya>\s*([\d,\.]*)\s*</normal-pequenya>'
        r'\s*([\d]+[,\.][\d]+)',
        re.UNICODE
    )
    items = []
    for m in item_pat.finditer(text):
        qty_s, name, unit_s, total_s = m.groups()
        name = name.strip()
        unit_s = unit_s.strip() if unit_s else ""
        if any(s in name.upper() for s in SKIP) or len(name) < 3:
            continue
        try:
            qty   = float(qty_s.replace(",", "."))
            total = float(total_s.replace(",", "."))
            unit  = float(unit_s.replace(",", ".")) if unit_s else round(total/qty, 4)
            items.append({
                "name": name, "quantity": qty,
                "unit_price": unit, "total": total,
                "category": categorize(name),
                "sold_by_kg": bool(unit_s),
            })
        except (ValueError, ZeroDivisionError):
            continue
    return items

def load_cookies():
    raw = os.environ.get("CONSUM_COOKIES", "")
    if not raw:
        local = Path("cookies.json")
        if local.exists():
            raw = local.read_text()
            log.info("Usando cookies.json local")
        else:
            log.error("No se encontró CONSUM_COOKIES ni cookies.json")
            sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.error("CONSUM_COOKIES no es JSON válido")
        sys.exit(1)

def already_with_items():
    done = set()
    for f in TICKETS_DIR.glob("*.json"):
        d = json.loads(f.read_text())
        if d.get("items"):
            done.add(f.stem)
    return done

def build_ticket_from_api(t):
    dt_str = t.get("SaleTimestamp", "")
    dt = date_str = None
    if dt_str:
        try:
            utc_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc)
            dt = utc_dt.astimezone(ZoneInfo("Europe/Madrid"))
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return {
        "id": str(t["Id"]),
        "ticket_code": t.get("TicketCode",""),
        "store": t.get("Store","Consum"),
        "store_address": t.get("StoreAddress",""),
        "date": date_str,
        "datetime_iso": dt.isoformat() if dt else None,
        "total": t.get("TotalPrice"),
        "payment_method": t.get("PaymentMethod") or "Tarjeta",
        "items": [], "tax_breakdown": [], "source": "api_only",
    }

async def get_iframe_info(context: BrowserContext) -> tuple[str, str, str]:
    """Devuelve (iframe_url, pass_token, version)."""
    page = await context.new_page()
    log.info("Cargando página principal...")
    await page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)
    if "acceso" in page.url or "login" in page.url.lower():
        log.error("Sesión expirada.")
        await page.close()
        sys.exit(1)
    html = await page.content()
    await page.close()
    m = re.search(r'src="(https://nomastickets\.consum\.es/app/index\.html[^"]+)"', html)
    if not m:
        log.error("No se encontró el iframe")
        sys.exit(1)
    iframe_url = m.group(1).replace("&amp;", "&")
    pass_match = re.search(r'pass=([^&]+)', iframe_url)
    pass_token = pass_match.group(1) if pass_match else ""
    ver_match  = re.search(r'[?&]v=(\d+)', iframe_url)
    version    = ver_match.group(1) if ver_match else "20251201"
    log.info("Sesión activa")
    return iframe_url, pass_token, version

async def fetch_tickets_list(context, iframe_url, pass_token) -> list:
    """Descarga la lista completa de tickets paginando la API."""
    all_tickets, seen_ids = [], set()
    log.info("Descargando lista de tickets...")

    # Usar una sola página para toda la paginación
    page = await context.new_page()
    await page.goto(iframe_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    page_num = 0
    while True:
        log.info("Página %d...", page_num)
        body = (f"email=null&password={pass_token}&isTicketOnline=false"
                f"&page={page_num}&elements={PAGE_SIZE}&latitude=0&longitude=0")
        await page.evaluate(f"window.__b = {json.dumps(body)};")
        pts = await page.evaluate(f"""
            async () => {{
                const r = await fetch('{API_BASE}/ticket/v2', {{
                    method:'POST', credentials:'include',
                    headers:{{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
                             'X-Requested-With':'XMLHttpRequest',
                             'Accept':'application/json, text/javascript, */*; q=0.01'}},
                    body: window.__b
                }});
                if (r.ok) return await r.json();
                return null;
            }}
        """)
        if not pts or not isinstance(pts, list):
            log.info("Sin más tickets"); break
        new = [t for t in pts if str(t["Id"]) not in seen_ids]
        for t in new: seen_ids.add(str(t["Id"]))
        all_tickets.extend(new)
        log.info("+%d (total: %d)", len(new), len(all_tickets))
        if len(pts) < PAGE_SIZE or not new: break
        page_num += 1
        if page_num > 100: break

    await page.close()
    return all_tickets

async def fetch_ticket_detail(context, ticket_id: str, pass_token: str, version: str) -> str | None:
    """
    Navega directamente a ticketdetail.html con ticketId y pass como params.
    Extrae el innerHTML del #ticketpre.
    """
    pass_q = quote(pass_token, safe="")
    url = (f"https://nomastickets.consum.es/app/ticketdetail.html"
           f"?ticketId={ticket_id}&pass={pass_q}&dni=&v={version}")
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2500)
        pre_html = await page.evaluate("""
            () => {
                const pre = document.querySelector('#ticketpre');
                return pre ? pre.innerHTML : null;
            }
        """)
        return pre_html
    except Exception as e:
        log.error("Error obteniendo detalle: %s", e)
        return None
    finally:
        await page.close()

async def main():
    cookies = load_cookies()
    existing = already_with_items()
    log.info("Tickets ya con artículos: %d", len(existing))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 430, "height": 800}
        )
        await context.add_cookies(cookies)

        iframe_url, pass_token, version = await get_iframe_info(context)
        all_tickets = await fetch_tickets_list(context, iframe_url, pass_token)

        log.info("Total tickets: %d", len(all_tickets))
        to_process = [t for t in all_tickets if str(t["Id"]) not in existing]
        log.info("A procesar: %d", len(to_process))

        if not to_process:
            log.info("Todos los tickets ya tienen artículos.")
            await browser.close(); sys.exit(2)

        success = 0
        for i, api_t in enumerate(to_process):
            tid   = str(api_t["Id"])
            store = api_t.get("Store","")
            date  = api_t.get("SaleTimestamp","")[:10]
            total = api_t.get("TotalPrice","?")
            log.info("[%d/%d] %s — %s — %s€", i+1, len(to_process), store, date, total)

            pre_html = await fetch_ticket_detail(context, tid, pass_token, version)

            jf = TICKETS_DIR / f"{tid}.json"
            base = json.loads(jf.read_text()) if jf.exists() else build_ticket_from_api(api_t)

            if pre_html:
                items = parse_items_from_pre(pre_html)
                base["items"] = items
                base["source"] = "dom_extracted"
                log.info("  %d artículos", len(items))
            else:
                log.warning("  Sin pre, guardando datos básicos")
                base["source"] = "api_only"

            jf.write_text(json.dumps(base, indent=2, ensure_ascii=False))
            success += 1

        await browser.close()

    log.info("=" * 50)
    log.info("%d/%d tickets procesados", success, len(to_process))
    if _uncategorized:
        log.info("Productos sin categoría (%d): %s", len(_uncategorized), ", ".join(sorted(_uncategorized)))
    if success == 0: sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
