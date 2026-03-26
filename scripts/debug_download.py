"""
debug_download.py — Debug visual para encontrar el botón de descarga de PDF.

Abre Chromium EN MODO VISIBLE sobre ticketdetail.html del primer ticket,
vuelca todos los botones/links/elementos clicables del DOM y luego
espera a que hagas click manualmente mientras intercepta la descarga.

Instrucciones:
  1. Asegúrate de tener cookies.json actualizado
  2. pip install playwright && playwright install chromium
  3. python debug_download.py
  4. El script te mostrará en terminal todos los botones encontrados
  5. Haz clic en "Descargar PDF" en la ventana del navegador
  6. El script capturará qué URL/evento se dispara
"""

import asyncio
import json
import re
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ playwright no instalado. Ejecuta: pip install playwright && playwright install chromium")
    sys.exit(1)

COOKIES_FILE  = Path("cookies.json")
MAIN_URL      = "https://www.consum.es/mundoconsum/mi-ahorro/"
OUTPUT_DIR    = Path("data/tickets")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────
#  UTILIDADES
# ──────────────────────────────────────────────────────────────

def load_cookies() -> list:
    if not COOKIES_FILE.exists():
        print("❌ No se encontró cookies.json")
        sys.exit(1)
    return json.loads(COOKIES_FILE.read_text(encoding="utf-8"))


async def get_iframe_url(context) -> str:
    """Extrae la URL del iframe nomastickets desde la página principal."""
    page = await context.new_page()
    print("🌐 Cargando página principal para obtener pass token...")
    await page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(3000)

    if "acceso" in page.url or "login" in page.url.lower():
        print("❌ Sesión expirada. Renueva cookies.json con get_cookie.py")
        await page.close()
        sys.exit(1)

    html = await page.content()
    await page.close()

    m = re.search(r'src="(https://nomastickets\.consum\.es/app/index\.html[^"]+)"', html)
    if not m:
        print("❌ No se encontró el iframe de nomastickets en la página")
        sys.exit(1)

    url = m.group(1).replace("&amp;", "&")
    print(f"✅ iframe URL encontrada")
    return url


async def get_first_ticket_id(context, iframe_url: str) -> str | None:
    """Llama a la API para obtener el ID del ticket más reciente."""
    pass_match = re.search(r"pass=([^&]+)", iframe_url)
    pass_token = pass_match.group(1) if pass_match else ""

    page = await context.new_page()
    await page.goto(iframe_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    data = await page.evaluate(f"""
        async () => {{
            const body = 'email=null&password={pass_token}&isTicketOnline=false&page=0&elements=5&latitude=0&longitude=0';
            const r = await fetch('https://nomastickets.consum.es/api/ticket/v2', {{
                method: 'POST',
                credentials: 'include',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'X-Requested-With': 'XMLHttpRequest'
                }},
                body
            }});
            if (r.ok) return await r.json();
            return null;
        }}
    """)
    await page.close()

    if data and isinstance(data, list) and data:
        tid = str(data[0]["Id"])
        store = data[0].get("Store", "?")
        date  = data[0].get("SaleTimestamp", "")[:10]
        print(f"🎫 Primer ticket: ID={tid}  {store}  {date}")
        return tid, pass_token

    print("⚠️  No se pudieron obtener tickets de la API")
    return None, pass_token


# ──────────────────────────────────────────────────────────────
#  BLOQUE PRINCIPAL DE DEBUG
# ──────────────────────────────────────────────────────────────

async def debug_ticket_detail(ticket_id: str, pass_token: str, iframe_url: str):
    """
    Abre ticketdetail.html en modo VISIBLE, vuelca todos los elementos
    clicables y espera interacción del usuario para capturar descargas.
    """
    import urllib.parse
    version_match = re.search(r"[?&]v=(\d+)", iframe_url)
    version = version_match.group(1) if version_match else "20251201"
    pass_q = urllib.parse.quote(pass_token, safe="")

    detail_url = (
        f"https://nomastickets.consum.es/app/ticketdetail.html"
        f"?ticketId={ticket_id}&pass={pass_q}&dni=&v={version}"
    )
    print(f"\n🔗 URL de detalle:\n   {detail_url}\n")

    async with async_playwright() as p:
        # ── MODO VISIBLE (headless=False) ──────────────────────────────
        browser = await p.chromium.launch(
            headless=False,
            args=["--window-size=480,900"],
            downloads_path=str(OUTPUT_DIR),
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 480, "height": 900},
            accept_downloads=True,
        )

        cookies = load_cookies()
        await context.add_cookies(cookies)

        # ── Interceptar todas las peticiones salientes ─────────────────
        intercepted_requests = []

        page = await context.new_page()

        async def on_request(request):
            url = request.url
            if any(kw in url.lower() for kw in ["pdf", "download", "ticket", "blob"]):
                intercepted_requests.append({
                    "url": url,
                    "method": request.method,
                    "type": request.resource_type,
                })
                print(f"\n  🌐 REQUEST  [{request.method}] {url}")

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "pdf" in ct or "pdf" in url.lower() or "download" in url.lower():
                print(f"\n  📥 RESPONSE [{response.status}] {url}")
                print(f"     Content-Type: {ct}")
                # Si es un PDF real, guardarlo
                if "pdf" in ct:
                    try:
                        body = await response.body()
                        out_path = OUTPUT_DIR / f"ticket_{ticket_id}.pdf"
                        out_path.write_bytes(body)
                        print(f"  💾 PDF guardado automáticamente: {out_path}")
                    except Exception as e:
                        print(f"  ⚠️  No se pudo guardar el PDF: {e}")

        page.on("request", on_request)
        page.on("response", on_response)

        # ── Capturar descargas ─────────────────────────────────────────
        download_future = asyncio.get_event_loop().create_future()

        async def on_download(download):
            suggested = download.suggested_filename
            out_path = OUTPUT_DIR / (suggested or f"ticket_{ticket_id}.pdf")
            print(f"\n  📥 DESCARGA INICIADA: {suggested}")
            await download.save_as(out_path)
            print(f"  💾 Guardado en: {out_path}")
            if not download_future.done():
                download_future.set_result(out_path)

        page.on("download", on_download)

        # ── Navegar a ticketdetail ─────────────────────────────────────
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)  # dejar que cargue el JS

        # ── Volcar TODOS los elementos clicables del DOM ───────────────
        print("\n" + "═" * 60)
        print("  🔍  ELEMENTOS CLICABLES ENCONTRADOS EN EL DOM")
        print("═" * 60)

        elements = await page.evaluate("""
            () => {
                const results = [];

                // Botones
                document.querySelectorAll('button, [role="button"]').forEach((el, i) => {
                    results.push({
                        type: 'button',
                        index: i,
                        text: el.innerText?.trim().substring(0, 80),
                        id: el.id || '',
                        class: el.className || '',
                        onclick: el.getAttribute('onclick') || '',
                        ngClick: el.getAttribute('ng-click') || '',
                    });
                });

                // Links
                document.querySelectorAll('a').forEach((el, i) => {
                    results.push({
                        type: 'link',
                        index: i,
                        text: el.innerText?.trim().substring(0, 80),
                        href: el.href || '',
                        id: el.id || '',
                        class: el.className || '',
                        onclick: el.getAttribute('onclick') || '',
                        ngClick: el.getAttribute('ng-click') || '',
                    });
                });

                // Divs/spans con ng-click o onclick (muy común en apps Angular)
                document.querySelectorAll('[ng-click], [onclick], [data-action]').forEach((el, i) => {
                    if (el.tagName === 'BUTTON' || el.tagName === 'A') return; // ya capturados
                    results.push({
                        type: el.tagName.toLowerCase(),
                        index: i,
                        text: el.innerText?.trim().substring(0, 80),
                        id: el.id || '',
                        class: el.className || '',
                        onclick: el.getAttribute('onclick') || '',
                        ngClick: el.getAttribute('ng-click') || '',
                        dataAction: el.getAttribute('data-action') || '',
                    });
                });

                // Iconos (fa-download, etc.)
                document.querySelectorAll('[class*="download"], [class*="pdf"], [class*="descarga"]').forEach((el, i) => {
                    results.push({
                        type: 'icon/' + el.tagName.toLowerCase(),
                        index: i,
                        text: el.innerText?.trim().substring(0, 80),
                        id: el.id || '',
                        class: el.className || '',
                        onclick: el.getAttribute('onclick') || '',
                        ngClick: el.getAttribute('ng-click') || '',
                    });
                });

                return results;
            }
        """)

        if elements:
            for el in elements:
                print(f"\n  [{el['type'].upper()}]")
                if el.get('text'):   print(f"    texto    : {el['text']}")
                if el.get('id'):     print(f"    id       : {el['id']}")
                if el.get('class'):  print(f"    class    : {el['class'][:100]}")
                if el.get('href'):   print(f"    href     : {el['href'][:120]}")
                if el.get('onclick'):print(f"    onclick  : {el['onclick'][:100]}")
                if el.get('ngClick'):print(f"    ng-click : {el['ngClick']}")
                if el.get('dataAction'): print(f"    data-act : {el['dataAction']}")
        else:
            print("  ⚠️  No se encontraron elementos clicables. La página puede no haber cargado.")

        # ── Volcar también el innerHTML completo para análisis offline ──
        inner_html = await page.evaluate("() => document.documentElement.outerHTML")
        debug_html = OUTPUT_DIR / f"debug_ticketdetail_{ticket_id}.html"
        debug_html.write_text(inner_html, encoding="utf-8")
        print(f"\n  💾 HTML completo guardado: {debug_html}")

        # ── Buscar específicamente funciones de descarga en el JS ──────
        print("\n" + "═" * 60)
        print("  🔍  BUSCANDO FUNCIONES DE DESCARGA EN ANGULAR SCOPE")
        print("═" * 60)

        scope_info = await page.evaluate("""
            () => {
                // Intentar acceder al scope de AngularJS si existe
                try {
                    const el = document.querySelector('[ng-controller]') ||
                               document.querySelector('[ng-app]') ||
                               document.body;
                    if (window.angular) {
                        const scope = window.angular.element(el).scope();
                        if (scope) {
                            return Object.keys(scope).filter(k =>
                                typeof scope[k] === 'function' &&
                                !k.startsWith('$')
                            );
                        }
                    }
                } catch(e) { return ['error: ' + e.message]; }
                return ['Angular no detectado o scope no accesible'];
            }
        """)
        print(f"  Funciones en scope: {scope_info}")

        # ── Instrucciones para el usuario ──────────────────────────────
        print("\n" + "═" * 60)
        print("  👆  ACCIÓN REQUERIDA")
        print("═" * 60)
        print("""
  El navegador está abierto mostrando el ticket.

  ► Busca el botón/icono de DESCARGAR PDF y haz clic en él.

  El script capturará automáticamente:
    - La URL exacta que se llama
    - El archivo PDF si se descarga
    - Cualquier petición HTTP relacionada

  Pulsa ENTER aquí cuando hayas hecho clic (o para salir):
""")
        input("  → ENTER para continuar: ")

        # ── Esperar descarga si se inició ──────────────────────────────
        if not download_future.done():
            print("  ⏳ Esperando 3 s por si la descarga está en curso...")
            try:
                await asyncio.wait_for(asyncio.shield(download_future), timeout=3.0)
            except asyncio.TimeoutError:
                print("  ℹ️  No se detectó descarga automática.")

        # ── Resumen de requests interceptadas ─────────────────────────
        if intercepted_requests:
            print("\n" + "═" * 60)
            print("  📡  REQUESTS INTERCEPTADAS DURANTE LA SESIÓN")
            print("═" * 60)
            for r in intercepted_requests:
                print(f"  [{r['method']}] ({r['type']}) {r['url']}")
        else:
            print("\n  ℹ️  No se interceptaron requests de PDF/descarga.")

        print("\n  🔍 Consejo: inspecciona también:")
        print(f"     {debug_html}")
        print("  Busca 'download', 'pdf', 'descargar' en ese archivo HTML.\n")

        await browser.close()


# ──────────────────────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────────────────────

async def main():
    print("\n" + "═" * 60)
    print("  🐛  DEBUG DESCARGA PDF — CONSUM TRACKER")
    print("═" * 60 + "\n")

    cookies = load_cookies()

    # Necesitamos el pass token: lo obtenemos con un contexto temporal
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        )
        await context.add_cookies(cookies)

        iframe_url = await get_iframe_url(context)
        ticket_id, pass_token = await get_first_ticket_id(context, iframe_url)
        await browser.close()

    if not ticket_id:
        print("❌ No se pudo obtener ningún ticket para debug")
        sys.exit(1)

    print(f"\n✅ Usando ticket ID: {ticket_id}")
    print(f"✅ pass token: {pass_token[:20]}...")

    # Ahora abrir en modo visible para debug interactivo
    await debug_ticket_detail(ticket_id, pass_token, iframe_url)


if __name__ == "__main__":
    asyncio.run(main())
