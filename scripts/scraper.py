"""
scraper.py — Lista y detalles de tickets en la misma pestaña de la aplicación.
Se conservan el sessionStorage y los parámetros del iframe durante la extracción.
"""
import argparse, asyncio, json, logging, os, re, sys, html as html_module
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote, urlencode, parse_qs, parse_qsl, urlsplit, urlunsplit

from ticket_data import extraction_complete, normalize_ticket, number, reconcile_ticket

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

ROOT        = Path(__file__).resolve().parent.parent
MAIN_URL    = "https://www.consum.es/mundoconsum/mi-ahorro/"
API_BASE    = "https://nomastickets.consum.es/api"
TICKETS_DIR = ROOT / "data/tickets"
COOKIES_FILE = ROOT / "cookies.json"
PAGE_SIZE   = 10

CATEGORIES_FILE = ROOT / "data/categories.json"

def load_categories() -> dict[str, list[str]]:
    cat_file = CATEGORIES_FILE if CATEGORIES_FILE.exists() else Path("data/categories.json")
    if cat_file.exists():
        return json.loads(cat_file.read_text(encoding="utf-8"))
    log.warning("No se encontró %s, usando categorías por defecto", CATEGORIES_FILE)
    return {}

CATEGORIES = load_categories()
_uncategorized: set[str] = set()

SKIP = {"FACTURA","IMPORTE","CAMBIO","TOTAL","BASE","IVA","CUOTA",
        "SILLA","SOCIO","TARJ","BBVA","VISA","OPERACI","AUTORI",
        "ATENDIDO","ABONAR","CLIENTE","COOP.V","NIF","ALGINET",
        "ARQUITECTO","S.COOP","F46078986"}

_SKIP_WORDS = {"IVA", "CAMBIO", "SILLA", "SOCIO", "BBVA", "VISA", "NIF", "ALGINET", "ARQUITECTO"}
_SKIP_PREFIXES = ("FACTURA", "IMPORTE", "TOTAL", "CUOTA", "TARJ", "OPERACI", "AUTORI",
                  "ATENDIDO", "ABONAR", "CLIENTE", "COOP.V", "S.COOP", "F46078986")

_DISCOUNT_PREFIX_RE = re.compile(
    r"^(?:CHEQUE\s*AHORRO|MI\s+AHORRO|DTO(?:\b|\.)|DESCUENTO\b|DESC(?:\b|\.)|"
    r"VALE(?:\b|\.)|CUP[OÓ]N(?:\b|\.)|BONO(?:\b|\.)|PROMO(?:CI[OÓ]N)?(?:\b|\.))",
    re.IGNORECASE,
)
_INFO_LOYALTY_RE = re.compile(
    r"\b(?:GENERAD|ACUMULAD|PR[OÓ]XIM|DISPONIBLE|SALDO|PENDIENTE)",
    re.IGNORECASE,
)
_PAYMENT_FOOTER_WORDS = {"IVA", "CAMBIO", "SILLA", "BBVA", "VISA", "NIF", "ALGINET", "ARQUITECTO", "EFECTIVO", "ENTREGADO"}
_PAYMENT_FOOTER_PREFIXES = ("FACTURA", "IMPORTE", "TOTAL", "CUOTA", "TARJ", "OPERACI", "AUTORI",
                            "ATENDIDO", "COOP.V", "S.COOP", "F46078986")


def _is_discount_name(name: str) -> bool:
    """Detectar líneas de Cheque Ahorro, cupones o descuentos antes de filtrar cabeceras."""
    cleaned = name.strip()
    if len(cleaned) < 3:
        return False
    return bool(_DISCOUNT_PREFIX_RE.search(cleaned)) and not bool(_INFO_LOYALTY_RE.search(cleaned))


def _is_payment_or_tax_footer(name: str) -> bool:
    """Descartar líneas de pago/cambio/IVA sin bloquear descuentos de socio o cliente."""
    upper = name.upper().strip()
    if upper == "BASE" or re.search(r"\bBASE\s+(?:IMP|IVA|\d)", upper):
        return True
    tokens = re.findall(r"[A-ZÁÉÍÓÚÜÑ0-9.]+", upper)
    for tok in tokens:
        if tok in _PAYMENT_FOOTER_WORDS or tok.startswith(_PAYMENT_FOOTER_PREFIXES):
            return True
    return False


def _is_invoice_line(name: str) -> bool:
    """Descartar pies/cabeceras de factura sin eliminar artículos reales como OLIVA o RECAMBIO."""
    upper = name.upper().strip()
    if upper == "BASE" or re.search(r"\bBASE\s+(?:IMP|IVA|\d)", upper):
        return True
    tokens = re.findall(r"[A-ZÁÉÍÓÚÜÑ0-9.]+", upper)
    for tok in tokens:
        if tok in _SKIP_WORDS or tok.startswith(_SKIP_PREFIXES):
            return True
    return False


def _is_sold_by_kg(qty_s: str, unit_s: str = "") -> bool:
    """True solo cuando la cantidad tiene parte decimal/fraccionaria o la línea indica KG/€/KG."""
    spec_upper = f"{qty_s} {unit_s}".upper()
    if re.search(r"(?:€\s*/\s*KG|EUR\s*/\s*KG|\bKG\b)", spec_upper):
        return True
    qty_clean = re.sub(r"(?i)\s*KG\b", "", qty_s).strip()
    if "," in qty_clean or "." in qty_clean:
        try:
            val = abs(number(qty_clean))
        except ValueError:
            return False
        if val != int(val):
            return True
        # Básculas que imprimen 3 decimales exactos (ej. 1,000)
        parts = re.split(r"[,\.]", qty_clean)
        if len(parts) == 2 and len(parts[1]) == 3:
            return True
    return False


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


def parse_ticket_html(pre_text: str) -> dict:
    """Extrae artículos y descuentos (Cheque Ahorro, cupones, vales) desde #ticketpre."""
    if not pre_text:
        return {"items": [], "discounts": []}
    text = html_module.unescape(pre_text)
    item_pat = re.compile(
        r'<normal-pequenya>\s*([+-]?\d+(?:[,\.]\d+)?(?:\s*KG)?|\s*)\s*</normal-pequenya>'
        r'([A-ZÁÉÍÓÚÜÑ0-9][^\n<]{2,40}?)\s*'
        r'<normal-pequenya>\s*([^<\n]*?)\s*</normal-pequenya>'
        r'\s*([+-]?\d+(?:\.\d{3})*[,\.]\d+|[+-]?\d+(?:[,\.]\d+)+)(?=\s|<|$)',
        re.UNICODE | re.IGNORECASE,
    )
    items = []
    discounts = []
    consumed_spans = []

    for m in item_pat.finditer(text):
        consumed_spans.append(m.span())
        qty_s, name, unit_s, total_s = m.groups()
        name = name.strip()
        qty_s = (qty_s or "").strip()
        unit_s = (unit_s or "").strip()
        if len(name) < 3:
            continue
        # Capturar descuentos ANTES de _is_invoice_line (que descarta SOCIO/CLIENTE).
        if _is_discount_name(name) or (not qty_s and total_s.strip().startswith("-") and not _is_payment_or_tax_footer(name)):
            try:
                amt = -abs(number(total_s))
                discounts.append({"name": name, "amount": amt})
            except ValueError:
                pass
            continue
        if not qty_s or _is_invoice_line(name):
            continue
        qty_num_s = re.sub(r"(?i)\s*KG\b", "", qty_s).strip()
        unit_num_m = re.search(r"[+-]?\d+(?:\.\d{3})*[,\.]\d+|[+-]?\d+(?:\.\d+)?", unit_s)
        try:
            qty = number(qty_num_s)
            total = number(total_s)
            unit = number(unit_num_m.group(0)) if unit_num_m else round(total / qty, 4)
            items.append({
                "name": name,
                "quantity": qty,
                "unit_price": unit,
                "total": total,
                "category": categorize(name),
                "sold_by_kg": _is_sold_by_kg(qty_s, unit_s),
            })
        except (ValueError, ZeroDivisionError):
            continue

    # Analizar líneas restantes fuera de los bloques item_pat (descuentos en texto plano o líneas multiplicadoras).
    if consumed_spans:
        chunks = []
        last_idx = 0
        for start, end in consumed_spans:
            chunks.append(text[last_idx:start])
            chunks.append("\n")
            last_idx = end
        chunks.append(text[last_idx:])
        remainder = "".join(chunks)
    else:
        remainder = text

    mult_only_pat = re.compile(
        r"^\s*([+-]?\d+(?:[,\.]\d+)?)\s*(KG)?\s*[xX*]\s*"
        r"([+-]?\d+(?:\.\d{3})*[,\.]\d+)\s*(€\s*/\s*KG|EUR\s*/\s*KG)?\s+"
        r"([+-]?\d+(?:\.\d{3})*[,\.]\d+)\s*€?\s*$",
        re.IGNORECASE,
    )
    inline_mult_pat = re.compile(
        r"^\s*([A-ZÁÉÍÓÚÜÑ0-9].{2,38}?)\s+"
        r"([+-]?\d+(?:[,\.]\d+)?)\s*(KG)?\s*[xX*]\s*"
        r"([+-]?\d+(?:\.\d{3})*[,\.]\d+)\s*(€\s*/\s*KG|EUR\s*/\s*KG)?\s+"
        r"([+-]?\d+(?:\.\d{3})*[,\.]\d+)\s*€?\s*$",
        re.IGNORECASE,
    )
    trailing_amount_pat = re.compile(
        r"^\s*(.+?)\s+([+-]?\d+(?:\.\d{3})*[,\.]\d+)\s*(-)?\s*€?\s*$"
    )

    clean_lines = [re.sub(r"<[^>]+>", " ", ln).strip() for ln in remainder.splitlines()]
    clean_lines = [re.sub(r"\s+", " ", ln).strip() for ln in clean_lines if ln.strip()]

    pending_name = None
    for ln in clean_lines:
        m_mult = mult_only_pat.match(ln)
        if m_mult and pending_name and not _is_discount_name(pending_name) and not _is_invoice_line(pending_name):
            qty_s, kg_tok, unit_s, eur_kg_tok, total_s = m_mult.groups()
            try:
                qty = number(qty_s)
                unit = number(unit_s)
                total = number(total_s)
                items.append({
                    "name": pending_name,
                    "quantity": qty,
                    "unit_price": unit,
                    "total": total,
                    "category": categorize(pending_name),
                    "sold_by_kg": _is_sold_by_kg(f"{qty_s} {kg_tok or ''}", f"{unit_s} {eur_kg_tok or ''}"),
                })
                pending_name = None
                continue
            except ValueError:
                pass

        m_inline = inline_mult_pat.match(ln)
        if m_inline:
            p_name, qty_s, kg_tok, unit_s, eur_kg_tok, total_s = m_inline.groups()
            p_name = p_name.strip()
            if not _is_discount_name(p_name) and not _is_invoice_line(p_name):
                try:
                    qty = number(qty_s)
                    unit = number(unit_s)
                    total = number(total_s)
                    items.append({
                        "name": p_name,
                        "quantity": qty,
                        "unit_price": unit,
                        "total": total,
                        "category": categorize(p_name),
                        "sold_by_kg": _is_sold_by_kg(f"{qty_s} {kg_tok or ''}", f"{unit_s} {eur_kg_tok or ''}"),
                    })
                    pending_name = None
                    continue
                except ValueError:
                    pass

        m_amt = trailing_amount_pat.match(ln)
        if m_amt:
            pending_name = None
            raw_label, amt_s, trailing_minus = m_amt.groups()
            raw_label = raw_label.rstrip(":- ").strip()
            is_negative = amt_s.startswith("-") or bool(trailing_minus)
            if _is_discount_name(raw_label):
                try:
                    amt = -abs(number(amt_s))
                    discounts.append({"name": raw_label, "amount": amt})
                except ValueError:
                    pass
                continue
            if is_negative and len(raw_label) >= 3 and not _is_payment_or_tax_footer(raw_label):
                if not _INFO_LOYALTY_RE.search(raw_label):
                    try:
                        amt = -abs(number(amt_s))
                        discounts.append({"name": raw_label, "amount": amt})
                    except ValueError:
                        pass
                continue
            continue

        if re.match(r"^[A-ZÁÉÍÓÚÜÑ0-9][A-ZÁÉÍÓÚÜÑ0-9\s.,/%'-]{2,39}$", ln, re.IGNORECASE):
            pending_name = ln
        else:
            pending_name = None

    return {"items": items, "discounts": discounts}


def parse_items_from_pre(pre_text: str) -> list:
    return parse_ticket_html(pre_text)["items"]


def load_cookies():
    raw = os.environ.get("CONSUM_COOKIES", "")
    if not raw:
        local = COOKIES_FILE if COOKIES_FILE.exists() else Path("cookies.json")
        if local.exists():
            try:
                raw = local.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError as exc:
                raise SessionError("cookies.json no tiene codificación UTF-8 válida") from exc
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


def already_with_items(retry_review: bool = False):
    """Devuelve IDs ya procesados. Por defecto omite también needs_review con detalle DOM ya extraído."""
    done = set()
    for f in TICKETS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
            report = reconcile_ticket(data)
            if report["status"] == "complete":
                done.add(f.stem)
            elif (
                not retry_review
                and report["status"] == "needs_review"
                and data.get("source") == "dom_extracted"
                and data.get("extraction_status") != "incomplete"
                and bool(data.get("items"))
            ):
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
    payment = (t.get("PaymentMethod") or "").strip() or "Tarjeta"
    return {
        "id": str(t["Id"]),
        "ticket_code": t.get("TicketCode",""),
        "store": t.get("Store","Consum"),
        "store_address": t.get("StoreAddress",""),
        "date": date_str,
        "datetime_iso": dt.isoformat() if dt else None,
        "total": number(t.get("TotalPrice")),
        "payment_method": payment,
        "items": [], "tax_breakdown": [], "source": "api_only",
    }


def _is_login_url(url: str) -> bool:
    """Detectar redirecciones a SSO/login o salida de /mundoconsum/mi-ahorro sin mirar la query."""
    if not url:
        return False
    location = urlsplit(url)
    host = (location.hostname or "").lower()
    path = location.path.lower()
    if host == "iam.consum.es":
        return True
    if any(seg in path for seg in ("/acceso", "/login", "/oauth2", "/saml", "/authenticationendpoint")):
        return True
    if host in ("www.consum.es", "consum.es") and not path.rstrip("/").endswith("/mi-ahorro"):
        return True
    return False


def _has_unauthenticated_markers(html: str) -> bool:
    lower = html.lower()
    return any(marker in lower for marker in (
        "iam.consum.es",
        "/mundoconsum/acceso",
        "iniciar sesión",
        "iniciar sesion",
        "identifícate",
        "identificate",
    ))


async def get_iframe_info(context: BrowserContext, *, timeout: float = 15.0) -> tuple[str, str, str]:
    """Devuelve (iframe_url, pass_token, version) o lanza SessionError si la sesión expiró."""
    iframe_re = re.compile(r"""src=["'](https://nomastickets\.consum\.es/app/index\.html[^"']+)["']""")
    page = await context.new_page()
    try:
        log.info("Cargando página principal...")
        response = await page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=30000)
        if response is not None:
            if response.status in (401, 403):
                raise SessionError(f"Acceso rechazado en página principal (HTTP {response.status})")
            if response.status == 429:
                raise ScrapeError("Consum limita las peticiones (HTTP 429). Espera antes de reintentar")
            if response.status >= 400:
                raise ScrapeError(f"El servidor devolvió HTTP {response.status} al cargar Mi ahorro")

        deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
        html = ""
        m = None
        while True:
            if _is_login_url(page.url):
                raise SessionError("Sesión expirada")
            html = await page.content()
            m = iframe_re.search(html)
            if m or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(min(0.25, max(0.0, deadline - asyncio.get_running_loop().time())))

        if not m:
            if _is_login_url(page.url) or _has_unauthenticated_markers(html):
                raise SessionError("Sesión expirada: Mi ahorro requiere iniciar sesión de nuevo")
            raise ScrapeError("No se encontró el iframe de tickets")
    finally:
        if hasattr(page, "is_closed") and callable(page.is_closed):
            closed = page.is_closed()
            if asyncio.iscoroutine(closed):
                closed = await closed
        else:
            closed = False
        if not closed:
            await page.close()

    iframe_url = html_module.unescape(m.group(1))
    params = parse_qs(urlsplit(iframe_url).query)
    pass_token = params.get("pass", [""])[0]
    if not pass_token:
        raise SessionError("El iframe de tickets no contiene token de sesión activa")
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Descargar tickets de Consum")
    parser.add_argument(
        "--retry-review",
        action="store_true",
        help="Volver a descargar también los tickets marcados como needs_review",
    )
    return parser.parse_args(argv)


async def main(*, retry_review: bool = False):
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    cookies = load_cookies()
    existing = already_with_items(retry_review=retry_review)
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
            needs_review = 0
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
                parsed = parse_ticket_html(pre_html) if pre_html else {"items": [], "discounts": []}
                base["items"] = parsed["items"]
                if parsed["discounts"]:
                    base["discounts"] = parsed["discounts"]
                base["source"] = "dom_extracted" if pre_html else "api_only"
                fresh_detail = bool(pre_html)
                fresh_items = bool(base["items"])
                complete = extraction_complete(base)
                # Conservar líneas anteriores si un reintento devuelve menos detalle.
                if jf.exists():
                    try:
                        previous = normalize_ticket(json.loads(jf.read_text(encoding="utf-8-sig")))
                    except (ValueError, OSError):
                        previous = None
                    if not complete and previous and len(previous["items"]) > len(base["items"]):
                        base["items"] = previous["items"]
                        if "discounts" in previous:
                            base["discounts"] = previous["discounts"]
                        base["source"] = previous["source"]
                report = reconcile_ticket(base)
                base["reconciliation"] = report
                base["extraction_status"] = report["status"] if fresh_items else "incomplete"
                temporary = jf.with_suffix(".tmp")
                temporary.write_text(json.dumps(base, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                temporary.replace(jf)
                if complete:
                    success += 1
                elif fresh_detail:
                    needs_review += 1
                    if fresh_items:
                        log.warning("Ticket %s requiere revisión: total %.2f€, líneas %.2f€, diferencia %+.2f€. "
                                    "Puede haber descuentos o líneas pendientes de extraer",
                                    tid, report["ticket_total"], report["items_total"], report["difference"])
                    else:
                        log.warning("Ticket %s: detalle descargado, pero su formato no produjo líneas reconocidas. "
                                    "Total original %.2f€; requiere revisión del parser", tid, report["ticket_total"])
                else:
                    failures += 1
                    log.warning("Ticket %s sin líneas extraídas; se reintentará", tid)

        finally:
            await browser.close()

    log.info("=" * 50)
    log.info("%d/%d detalles descargados: %d conciliados y %d pendientes de revisión",
             success + needs_review, len(to_process), success, needs_review)
    if _uncategorized:
        log.info("Productos sin categoría (%d): %s", len(_uncategorized), ", ".join(sorted(_uncategorized)))
    if failures:
        raise ScrapeError(f"{failures} tickets incompletos sin líneas extraídas")
    if needs_review:
        log.warning("Descarga terminada con %d tickets pendientes de revisión. "
                    "Los totales originales se conservan; las categorías pueden estar incompletas", needs_review)
        raise SystemExit(4)


if __name__ == "__main__":
    cli_args = parse_args()
    try:
        asyncio.run(main(retry_review=cli_args.retry_review))
    except SessionError as exc:
        log.error("%s", exc)
        sys.exit(1)
    except (ScrapeError, ValueError, OSError) as exc:
        log.error("%s", exc)
        sys.exit(3)
    except Exception:
        log.error("Fallo inesperado de navegador o red; no se completó la descarga")
        sys.exit(3)
