"""
get_cookie.py — Ejecutar UNA VEZ localmente para capturar las cookies de sesión.

Instrucciones:
  1. pip install playwright
  2. playwright install chromium
  3. python scripts/get_cookie.py
  4. Inicia sesión en el navegador que se abre (con 2FA)
  5. Pulsa ENTER en la terminal cuando estés dentro
  6. Copia el contenido de cookies.json → GitHub Secret llamado CONSUM_COOKIES
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("get_cookie")

try:
    from playwright.async_api import async_playwright
except ImportError:
    log.error("Playwright no está instalado. Ejecuta: pip install playwright && playwright install chromium")
    sys.exit(1)

LOGIN_URL = "https://www.consum.es/mundoconsum/acceso/"
VERIFY_URL = "https://www.consum.es/mundoconsum/mi-ahorro/"
OUTPUT_FILE = Path("cookies.json")


async def main():
    log.info("=" * 55)
    log.info("CAPTURADOR DE COOKIES — CONSUM TRACKER")
    log.info("=" * 55)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        log.info("Paso 1: Abriendo la página de login de Consum...")
        await page.goto(LOGIN_URL, wait_until="networkidle")

        log.info("Paso 2: Inicia sesión manualmente en el navegador:")
        log.info("  → Introduce tu email y contraseña")
        log.info("  → Completa el 2FA (SMS o email)")
        log.info("  → Espera a que cargue el panel de usuario")

        input("\nPulsa ENTER cuando hayas iniciado sesión completamente: ")

        current_url = page.url
        if "acceso" in current_url or "login" in current_url.lower():
            log.warning("Parece que aún estás en la página de login.")
            resp = input("¿Seguro que completaste el proceso? (s/n): ").strip().lower()
            if resp != "s":
                log.info("Cancelado. Vuelve a ejecutar el script cuando estés listo.")
                await browser.close()
                return 1

        log.info("Verificando sesión navegando a mi-ahorro...")
        await page.goto(VERIFY_URL, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)
        if "acceso" in page.url or "login" in page.url.lower():
            log.error("La sesión no parece válida — redirigido al login.")
            await browser.close()
            return 1

        cookies = await context.cookies()

        if not cookies:
            log.error("No se encontraron cookies. Asegúrate de haber iniciado sesión.")
            await browser.close()
            return 1

        OUTPUT_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8")

        log.info("%d cookies guardadas en '%s'", len(cookies), OUTPUT_FILE)
        log.info("=" * 55)
        log.info("Sesión lista. Ejecuta: python run.py update")
        log.info("Para la automatización de GitHub, guarda cookies.json en el secreto CONSUM_COOKIES.")
        log.info("cookies.json es privado y nunca debe subirse al repositorio.")
        log.info("Repite python run.py login cuando la sesión caduque.")

        await browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
