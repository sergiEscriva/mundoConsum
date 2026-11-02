"""
scraper.py — Lista y detalles de tickets en la misma pestaña de la aplicación.
Se conservan el sessionStorage y los parámetros del iframe durante la extracción.
"""
import asyncio, json, logging, os, re, sys, html as html_module
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlencode, parse_qs, parse_qsl, urlsplit, urlunsplit

from ticket_data import extraction_complete, normalize_ticket, number

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

try:
    from playwright.async_api import async_playwright, BrowserContext, Error as PlaywrightError
except ImportError:
    logging.error("Playwright no instalado")
    sys.exit(1)

MAIN_URL    = "https://www.consum.es/mundoconsum/mi-ahorro/"
API_BASE    = "https://nomastickets.consum.es/api"
TICKETS_DIR = Path("data/tickets")
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

class SessionError(RuntimeError):
    """Sesión ausente o caducada; código de salida 1."""


class ScrapeError(RuntimeError):
    """Fallo de red, API o extracción; código de salida 3."""


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
        r'<normal-pequenya>\s*([+-]?\d+(?:[,\.]\d+)?)\s*</normal-pequenya>'
        r'([A-ZÁÉÍÓÚÜÑ][^\n<]{2,40}?)\s*'
        r'<normal-pequenya>\s*([+-]?[\d,\.]*)\s*</normal-pequenya>'
        r'\s*([+-]?\d+(?:[,\.]\d+)+)(?=\s|<|$)',
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
            qty   = number(qty_s)
            total = number(total_s)
            unit  = number(unit_s) if unit_s else round(total/qty, 4)
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
            raise SessionError("No se encontró CONSUM_COOKIES ni cookies.json")
    try:
        cookies = json.loads(raw)
        if not isinstance(cookies, list) or not cookies or not all(isinstance(c, dict) for c in cookies):
            raise SessionError("Las cookies deben ser una lista no vacía de objetos")
        return cookies
    except json.JSONDecodeError as exc:
        raise SessionError("CONSUM_COOKIES no es JSON válido") from exc

def already_with_items():
    done = set()
    for f in TICKETS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            if extraction_complete(data):
                done.add(f.stem)
        except (ValueError, OSError):
            log.warning("Ticket %s ilegible; se intentará descargar de nuevo", f.name)
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
        "total": number(t.get("TotalPrice")),
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
        await page.close()
        raise SessionError("Sesión expirada")
    html = await page.content()
    await page.close()
    m = re.search(r'src="(https://nomastickets\.consum\.es/app/index\.html[^"]+)"', html)
    if not m:
        raise ScrapeError("No se encontró el iframe de tickets")
    iframe_url = m.group(1).replace("&amp;", "&")
    params = parse_qs(urlsplit(iframe_url).query)
    pass_token = params.get("pass", [""])[0]
    if not pass_token:
        raise ScrapeError("El iframe no contiene un token de sesión")
    version = params.get("v", ["20251201"])[0]
    log.info("Sesión activa")
    return iframe_url, pass_token, version

async def fetch_tickets_list(context, iframe_url, pass_token, *, page=None) -> list:
    """Descarga la lista completa de tickets paginando la API."""
    all_tickets, seen_ids = [], set()
    owns_page = page is None
    if owns_page:
        page = await context.new_page()
    try:
        await page.goto(iframe_url, wait_until="domcontentloaded", timeout=30000)
        for page_num in range(101):
            body = urlencode({"email": "null", "password": pass_token,
                              "isTicketOnline": "false", "page": page_num,
                              "elements": PAGE_SIZE, "latitude": 0, "longitude": 0})
            response = await page.evaluate("""async ({url, body}) => {
                const r = await fetch(url, {
                    method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                              'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
                    body
                });
                return {status: r.status, body: await r.text()};
            }""", {"url": f"{API_BASE}/ticket/v2", "body": body})
            status = response["status"]
            if status in (401, 403):
                raise SessionError(f"API: sesión rechazada (HTTP {status})")
            if not 200 <= status < 300:
                raise ScrapeError(f"API: error HTTP {status} en página {page_num}")
            try:
                pts = json.loads(response["body"])
            except (ValueError, TypeError) as exc:
                raise ScrapeError("La API no devolvió JSON válido") from exc
            if not isinstance(pts, list) or any(not isinstance(t, dict) or "Id" not in t for t in pts):
                raise ScrapeError("La API devolvió un esquema de tickets inesperado")
            if not pts:
                return all_tickets
            new = [t for t in pts if str(t["Id"]) not in seen_ids]
            if not new:
                raise ScrapeError("La API repite la página; no se puede garantizar la descarga completa")
            for t in new:
                seen_ids.add(str(t["Id"]))
            all_tickets.extend(new)
            if len(pts) < PAGE_SIZE:
                return all_tickets
        raise ScrapeError("Se alcanzó el límite de paginación; descarga incompleta")
    finally:
        if owns_page:
            await page.close()


def detail_url(ticket_id, pass_token, version, iframe_url=None):
    """Conservar dni y parámetros adicionales del iframe en la URL de detalle."""
    parts = urlsplit(iframe_url or "https://nomastickets.consum.es/app/index.html")
    if parts.scheme != "https" or parts.hostname != "nomastickets.consum.es":
        raise ScrapeError("El iframe no pertenece al dominio de tickets de Consum")
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params.update({"ticketId": str(ticket_id), "pass": pass_token, "v": version})
    params.setdefault("dni", "")
    return urlunsplit((parts.scheme, parts.netloc, "/app/ticketdetail.html", urlencode(params), ""))


def safe_navigation_error(exc, pass_token):
    """Dar una causa útil sin URLs con tokens, cookies ni el call log de Playwright."""
    message = str(exc).split("Call log:")[0].splitlines()
    message = message[0] if message else type(exc).__name__
    for value in (pass_token, quote(pass_token, safe=""), urlencode({"pass": pass_token})[5:]):
        if value:
            message = message.replace(value, "[oculto]")
    message = re.sub(r"https?://[^\s\"'<>]+", "[URL]", message)
    message = re.sub(r"(?i)(pass|token|password|dni|sessionDataKey)=[^\s&]+", r"\1=[oculto]", message)
    return f"{type(exc).__name__}: {message[:240]}"


async def read_ticket_pre(page, timeout=15000):
    """Esperar al contenido sin evaluar funciones en el contexto JS de la aplicación."""
    pre = page.locator("#ticketpre")
    await pre.wait_for(state="attached", timeout=timeout)
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while True:
        content = await pre.inner_html(timeout=timeout)
        if content and re.sub(r"<[^>]*>", "", html_module.unescape(content)).strip():
            return content
        if asyncio.get_running_loop().time() >= deadline:
            raise ScrapeError("El detalle contiene #ticketpre pero no ha cargado las líneas")
        await asyncio.sleep(0.25)


async def fetch_ticket_detail(context, ticket_id: str, pass_token: str, version: str,
                              *, page=None, iframe_url=None) -> str | None:
    """Reutilizar la aplicación autenticada y reintentar tras reinicializarla."""
    url = detail_url(ticket_id, pass_token, version, iframe_url)
    owns_page = page is None
    if owns_page:
        page = await context.new_page()
    try:
        for attempt in range(2):
            stage = "navegación"
            try:
                if attempt:
                    # Chromium puede seguir abriendo su página de error tras un fallo
                    # de red. Consumir esa navegación antes de reiniciar la aplicación.
                    stage = "reinicio tras fallo de navegación"
                    try:
                        await page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
                    except PlaywrightError:
                        try:
                            await page.wait_for_url("about:blank", wait_until="domcontentloaded", timeout=3000)
                        except PlaywrightError:
                            pass
                if iframe_url and (owns_page or attempt):
                    stage = "inicialización de la aplicación"
                    await page.goto(iframe_url, wait_until="domcontentloaded", timeout=30000)
                stage = "navegación al detalle"
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000,
                                           **({"referer": iframe_url} if iframe_url else {}))
                if response is not None:
                    if response.status in (401, 403):
                        raise SessionError(f"Acceso al detalle rechazado (HTTP {response.status})")
                    if response.status == 429:
                        raise ScrapeError("Consum limita las peticiones (HTTP 429). Espera antes de reintentar")
                    if response.status >= 400:
                        raise ScrapeError(f"El servidor devolvió HTTP {response.status} al abrir el detalle")
                location = urlsplit(page.url)
                if location.hostname == "iam.consum.es" or "acceso" in location.path or "login" in location.path.lower():
                    raise SessionError("La página de detalle redirigió al inicio de sesión")
                stage = "lectura de #ticketpre"
                return await read_ticket_pre(page)
            except SessionError:
                raise
            except (PlaywrightError, ScrapeError) as exc:
                log.warning("Detalle %s: fallo en %s (intento %d/2): %s",
                            ticket_id, stage, attempt + 1, safe_navigation_error(exc, pass_token))
                if isinstance(exc, ScrapeError):
                    # 404/429 o contenido vacío no se corrigen repitiendo la misma petición.
                    raise
                if attempt == 1:
                    return None
    finally:
        if owns_page:
            await page.close()


async def main():
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    cookies = load_cookies()
    existing = already_with_items()
    log.info("Tickets ya con artículos: %d", len(existing))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
                viewport={"width": 430, "height": 800}
            )
            try:
                await context.add_cookies(cookies)
            except Exception as exc:
                raise SessionError("Cookies de Playwright inválidas") from exc

            iframe_url, pass_token, version = await get_iframe_info(context)
            app_page = await context.new_page()
            all_tickets = await fetch_tickets_list(context, iframe_url, pass_token, page=app_page)

            log.info("Total tickets: %d", len(all_tickets))
            to_process = [t for t in all_tickets if str(t["Id"]) not in existing]
            log.info("A procesar: %d", len(to_process))

            if not to_process:
                log.info("Todos los tickets ya tienen artículos.")
                raise SystemExit(2)

            success = 0
            failures = 0
            consecutive_detail_failures = 0
            for i, api_t in enumerate(to_process):
                tid   = str(api_t["Id"])
                store = api_t.get("Store","")
                date  = api_t.get("SaleTimestamp","")[:10]
                total = api_t.get("TotalPrice","?")
                log.info("[%d/%d] %s — %s — %s€", i+1, len(to_process), store, date, total)

                pre_html = await fetch_ticket_detail(
                    context, tid, pass_token, version, page=app_page, iframe_url=iframe_url
                )
                consecutive_detail_failures = 0 if pre_html else consecutive_detail_failures + 1
                if consecutive_detail_failures >= 3:
                    raise ScrapeError(
                        "Tres detalles consecutivos no pudieron abrirse. Se detiene la descarga "
                        "para evitar repetir el fallo en todos los tickets; revisa la causa indicada arriba"
                    )

                jf = TICKETS_DIR / f"{tid}.json"
                base = build_ticket_from_api(api_t)
                base["items"] = parse_items_from_pre(pre_html) if pre_html else []
                complete = extraction_complete(base)
                base["source"] = "dom_extracted" if pre_html else "api_only"
                base["extraction_status"] = "complete" if complete else "incomplete"
                # Conservar líneas anteriores si un reintento devuelve menos detalle.
                if jf.exists():
                    try:
                        previous = normalize_ticket(json.loads(jf.read_text(encoding="utf-8-sig")))
                    except (ValueError, OSError):
                        previous = None
                    if not complete and previous and len(previous["items"]) > len(base["items"]):
                        base["items"] = previous["items"]
                        base["source"] = previous["source"]
                temporary = jf.with_suffix(".tmp")
                temporary.write_text(json.dumps(base, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                temporary.replace(jf)
                if complete:
                    success += 1
                else:
                    failures += 1
                    log.warning("Ticket %s incompleto o sin conciliar; se reintentará", tid)

        finally:
            await browser.close()

    log.info("=" * 50)
    log.info("%d/%d tickets procesados", success, len(to_process))
    if _uncategorized:
        log.info("Productos sin categoría (%d): %s", len(_uncategorized), ", ".join(sorted(_uncategorized)))
    if failures:
        raise ScrapeError(f"{failures} tickets incompletos; no se considera una descarga correcta")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SessionError as exc:
        log.error("%s", exc)
        sys.exit(1)
    except (ScrapeError, ValueError, OSError) as exc:
        log.error("%s", exc)
        sys.exit(3)
    except Exception:
        log.error("Fallo inesperado de navegador o red; no se completó la descarga")
        sys.exit(3)
