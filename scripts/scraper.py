"""
scraper.py — Descarga los PDFs de tickets nuevos desde Consum usando cookies guardadas.
Se ejecuta en GitHub Actions cada día.
"""

import asyncio
import json
import os
import sys
import hashlib
from pathlib import Path
from datetime import datetime

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ playwright no instalado")
    sys.exit(1)

TICKETS_URL = "https://www.consum.es/mundoconsum/mi-ahorro/"
PDF_DIR = Path("data/tickets")
PDF_DIR.mkdir(parents=True, exist_ok=True)


def load_cookies() -> list:
    """Carga las cookies desde la variable de entorno CONSUM_COOKIES."""
    raw = os.environ.get("CONSUM_COOKIES", "")
    if not raw:
        # Intento local: leer del archivo
        local = Path("cookies.json")
        if local.exists():
            raw = local.read_text()
            print("ℹ️  Usando cookies.json local")
        else:
            print("❌ No se encontró CONSUM_COOKIES ni cookies.json")
            sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("❌ CONSUM_COOKIES no es un JSON válido")
        sys.exit(1)


def already_downloaded() -> set:
    """Devuelve el conjunto de IDs de tickets ya procesados."""
    downloaded = set()
    for f in PDF_DIR.glob("*.json"):
        downloaded.add(f.stem)
    return downloaded


async def download_tickets():
    cookies = load_cookies()
    existing = already_downloaded()
    print(f"ℹ️  Tickets ya procesados: {len(existing)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # Inyectar cookies
        await context.add_cookies(cookies)

        page = await context.new_page()
        print(f"🌐 Navegando a {TICKETS_URL} ...")

        try:
            await page.goto(TICKETS_URL, wait_until="networkidle", timeout=30000)
        except Exception as e:
            print(f"❌ Error al cargar la página: {e}")
            await browser.close()
            sys.exit(1)

        # Verificar que la sesión sigue activa
        if "acceso" in page.url or "login" in page.url.lower():
            print("❌ La sesión ha expirado. Regenera las cookies con get_cookie.py")
            await browser.close()
            sys.exit(1)

        print("✅ Sesión activa")

        # Esperar a que cargue la sección de tickets
        try:
            await page.wait_for_selector(
                "[class*='ticket'], [class*='Ticket'], [href*='ticket'], [href*='.pdf']",
                timeout=15000
            )
        except Exception:
            print("⚠️  No se encontró el selector de tickets. Haciendo screenshot para debug.")
            await page.screenshot(path="debug_screenshot.png")
            # Intentar igualmente
            pass

        # Buscar todos los enlaces a PDFs o botones de descarga
        pdf_links = await page.evaluate("""
            () => {
                const results = [];
                // Buscar enlaces directos a PDF
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href;
                    if (href.includes('.pdf') || href.includes('ticket') || href.includes('factura')) {
                        results.push({type: 'link', url: href, text: a.innerText.trim()});
                    }
                });
                // Buscar botones de descarga
                document.querySelectorAll('button, [role="button"]').forEach(btn => {
                    const text = btn.innerText.toLowerCase();
                    if (text.includes('descargar') || text.includes('pdf') || text.includes('ticket')) {
                        results.push({type: 'button', text: btn.innerText.trim()});
                    }
                });
                return results;
            }
        """)

        print(f"🔍 Encontrados {len(pdf_links)} posibles tickets")

        new_count = 0
        for item in pdf_links:
            if item["type"] == "link" and item.get("url"):
                url = item["url"]
                # Generar ID único basado en la URL
                ticket_id = hashlib.md5(url.encode()).hexdigest()[:12]

                if ticket_id in existing:
                    print(f"  ⏭️  Ya procesado: {ticket_id}")
                    continue

                # Descargar el PDF
                pdf_path = PDF_DIR / f"{ticket_id}.pdf"
                try:
                    response = await page.goto(url)
                    if response and response.ok:
                        body = await response.body()
                        pdf_path.write_bytes(body)
                        print(f"  ✅ Descargado: {ticket_id}.pdf ({len(body)} bytes)")
                        new_count += 1
                    else:
                        print(f"  ⚠️  Error HTTP al descargar {url}")
                except Exception as e:
                    print(f"  ⚠️  Error descargando {url}: {e}")

                # Volver a la página principal
                await page.goto(TICKETS_URL, wait_until="networkidle", timeout=20000)

        print(f"\n📊 Resumen: {new_count} tickets nuevos descargados")
        await browser.close()

        if new_count == 0:
            print("ℹ️  Sin tickets nuevos hoy. No hay cambios que guardar.")
            # Salir con código especial para que el Action no haga commit
            sys.exit(2)


if __name__ == "__main__":
    asyncio.run(download_tickets())
