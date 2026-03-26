"""
scraper.py — Versión final.
Descubrimiento clave: ticketdetail.html acepta parámetros directos:
  ?ticketId=ID&pass=TOKEN&dni=&v=20251201
No hace falta mytickets.html, scroll ni clics.
Simplemente navegar a esa URL carga el ticket y el <pre> está en el DOM.
"""
import asyncio, json, os, re, sys, html as html_module
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote

try:
    from playwright.async_api import async_playwright, BrowserContext
except ImportError:
    print("❌ playwright no instalado"); sys.exit(1)

MAIN_URL    = "https://www.consum.es/mundoconsum/mi-ahorro/"
API_BASE    = "https://nomastickets.consum.es/api"
TICKETS_DIR = Path("data/tickets")
TICKETS_DIR.mkdir(parents=True, exist_ok=True)
PAGE_SIZE   = 10

CATEGORIES = {
    "Lácteos y Huevos":        ["MOZZAR","QUESO","LECHE","YOGUR","MANTEQUIL","NATA","HUEVO","KEFIR","BATIDO"],
    "Bebidas":                 ["FONTVELLA","AGUA","ZUMO","REFRESC","CERVEZA","VINO","COCA","FANTA","NESTEA","HORCHATA","CHUFI"],
    "Frutas y Verduras":       ["MANZANA","NARANJA","PLATANO","TOMATE","LECHUGA","ZANAHORIA","CEBOLLA","AJO",
                                "PIMIENTO","PEPINO","LIMON","MELON","FRESA","KIWI","PATATA","BROCOLI"],
    "Carne y Pescado":         ["POLLO","CERDO","TERNERA","SALMON","ATUN","JAMON","CHORIZO","BACON",
                                "LONCHA","FILETE","PECHUGA","MERLUZA","GAMBA","MEJILLON"],
    "Pan y Bollería":          ["PAN","BARRA","BAGUET","BOLLO","CROISSANT","GALLETA","BIZCOCHO","CEREALES","BROWNIE"],
    "Congelados y Preparados": ["PIZZA","LASAÑA","CANELONES","NUGGET","CROQUETA","CONGELAD","PRECOCID"],
    "Pasta, Arroz y Legumbres":["PASTA","ESPAGUETI","MACARRON","ARROZ","LENTEJA","GARBANZO","QUINOA"],
    "Conservas y Salsas":      ["TOMATE FRITO","KETCHUP","MAYONESA","ACEITE","VINAGRE","SALSA"],
    "Snacks y Dulces":         ["CHIPS","CHOCOLATE","CARAMELO","FRUTOS SECOS","ALMENDRA","PIPAS"],
    "Higiene y Limpieza":      ["CHAMPU","GEL","JABON","COLONIA","DESODORANTE","PAPEL","BAYETA","LAVAVAJIL"],
}

SKIP = {"FACTURA","IMPORTE","CAMBIO","TOTAL","BASE","IVA","CUOTA",
        "SILLA","SOCIO","TARJ","BBVA","VISA","OPERACI","AUTORI",
        "ATENDIDO","ABONAR","CLIENTE","COOP.V","NIF","ALGINET",
        "ARQUITECTO","S.COOP","F46078986"}

def categorize(name):
    n = name.upper()
    for cat, kws in CATEGORIES.items():
        if any(kw in n for kw in kws):
            return cat
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
            raw = local.read_text(); print("ℹ️  Usando cookies.json local")
        else:
            print("❌ No se encontró CONSUM_COOKIES ni cookies.json"); sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("❌ CONSUM_COOKIES no es JSON válido"); sys.exit(1)

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
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%SZ") + timedelta(hours=1)
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
    print("🌐 Cargando página principal...")
    await page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)
    if "acceso" in page.url or "login" in page.url.lower():
        print("❌ Sesión expirada."); await page.close(); sys.exit(1)
    html = await page.content()
    await page.close()
    m = re.search(r'src="(https://nomastickets\.consum\.es/app/index\.html[^"]+)"', html)
    if not m:
        print("❌ No se encontró el iframe"); sys.exit(1)
    iframe_url = m.group(1).replace("&amp;", "&")
    pass_match = re.search(r'pass=([^&]+)', iframe_url)
    pass_token = pass_match.group(1) if pass_match else ""
    ver_match  = re.search(r'[?&]v=(\d+)', iframe_url)
    version    = ver_match.group(1) if ver_match else "20251201"
    print("✅ Sesión activa")
    return iframe_url, pass_token, version

async def fetch_tickets_list(context, iframe_url, pass_token) -> list:
    """Descarga la lista completa de tickets paginando la API."""
    all_tickets, seen_ids = [], set()
    print("\n📋 Descargando lista de tickets...")

    # Usar una sola página para toda la paginación
    page = await context.new_page()
    await page.goto(iframe_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    page_num = 0
    while True:
        print(f"  📄 Página {page_num}...", end=" ", flush=True)
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
            print("+0"); break
        new = [t for t in pts if str(t["Id"]) not in seen_ids]
        for t in new: seen_ids.add(str(t["Id"]))
        all_tickets.extend(new)
        print(f"+{len(new)} (total: {len(all_tickets)})")
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
        print(f"    ⚠️  Error: {e}")
        return None
    finally:
        await page.close()

async def main():
    cookies = load_cookies()
    existing = already_with_items()
    print(f"ℹ️  Tickets ya con artículos: {len(existing)}")

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

        print(f"\n✅ Total tickets: {len(all_tickets)}")
        to_process = [t for t in all_tickets if str(t["Id"]) not in existing]
        print(f"🆕 A procesar: {len(to_process)}")

        if not to_process:
            print("ℹ️  Todos los tickets ya tienen artículos.")
            await browser.close(); sys.exit(2)

        success = 0
        for i, api_t in enumerate(to_process):
            tid   = str(api_t["Id"])
            store = api_t.get("Store","")
            date  = api_t.get("SaleTimestamp","")[:10]
            total = api_t.get("TotalPrice","?")
            print(f"\n  [{i+1}/{len(to_process)}] {store} — {date} — {total}€")

            pre_html = await fetch_ticket_detail(context, tid, pass_token, version)

            jf = TICKETS_DIR / f"{tid}.json"
            base = json.loads(jf.read_text()) if jf.exists() else build_ticket_from_api(api_t)

            if pre_html:
                items = parse_items_from_pre(pre_html)
                base["items"] = items
                base["source"] = "dom_extracted"
                print(f"    ✅ {len(items)} artículos")
            else:
                print(f"    ⚠️  Sin pre, guardando datos básicos")
                base["source"] = "api_only"

            jf.write_text(json.dumps(base, indent=2, ensure_ascii=False))
            success += 1

        await browser.close()

    print(f"\n{'='*50}")
    print(f"✅ {success}/{len(to_process)} tickets procesados")
    if success == 0: sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())