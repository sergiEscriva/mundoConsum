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
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("get_cookie")

try:
    from playwright.async_api import async_playwright, Error as PlaywrightError
except ImportError:
    log.error("Playwright no está instalado. Ejecuta: pip install playwright && playwright install chromium")
    sys.exit(1)

LOGIN_URL = "https://www.consum.es/mundoconsum/acceso/"
VERIFY_URL = "https://www.consum.es/mundoconsum/mi-ahorro/"
OUTPUT_FILE = Path("cookies.json")


async def prompt(message):
    # input() en el hilo principal bloquea los eventos de Playwright durante el 2FA.
    return await asyncio.to_thread(input, message)


async def has_ticket_access(page):
    """Confirmar acceso al iframe que necesita el scraper, no por el nombre de la URL."""
    if page.is_closed():
        return False
    try:
        return await page.evaluate("""() => [...document.querySelectorAll('iframe[src]')].some(frame => {
            try {
                const url = new URL(frame.getAttribute('src'), location.href);
                return url.protocol === 'https:' && url.hostname === 'nomastickets.consum.es'
                    && url.pathname === '/app/index.html' && !!url.searchParams.get('pass');
            } catch { return false; }
        })""")
    except PlaywrightError:
        # El contexto JS cambia durante las redirecciones de login/SSO.
        return False


async def verify_session(context, timeout=60):
    """Verificar en otra pestaña; conservar intacta la pestaña donde se hizo el login."""
    verification = await context.new_page()
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        try:
            await verification.goto(VERIFY_URL, wait_until="commit", timeout=max(1, min(30000, int(timeout * 1000))))
        except PlaywrightError:
            # Una navegación interrumpida puede ser una redirección SSO legítima.
            # Solo se acepta la sesión si después aparece el iframe autenticado.
            pass
        while True:
            # El usuario puede haber terminado el login en un popup u otra pestaña.
            for page in list(context.pages):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return False
                try:
                    if await asyncio.wait_for(has_ticket_access(page), timeout=min(3, remaining)):
                        return True
                except asyncio.TimeoutError:
                    continue
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(min(0.25, max(0, deadline - asyncio.get_running_loop().time())))
    finally:
        if not verification.is_closed():
            await verification.close()


async def capture_session(context, output_file=None):
    output_file = OUTPUT_FILE if output_file is None else output_file
    page = await context.new_page()
    log.info("Paso 1: Abriendo la página de login de Consum...")
    try:
        await page.goto(LOGIN_URL, wait_until="commit", timeout=30000)
    except PlaywrightError:
        log.warning("La página sigue redirigiendo o no terminó de cargar. Completa el acceso en el navegador.")

    log.info("Paso 2: Inicia sesión en la ventana abierta y completa el 2FA.")
    log.info("Espera a ver tu panel; puedes usar otra pestaña de esta misma ventana.")
    while True:
        answer = await prompt("\nPulsa ENTER cuando estés dentro de Consum (o escribe salir para cancelar): ")
        if answer.strip().lower() in {"salir", "cancelar", "exit"}:
            log.info("Inicio de sesión cancelado. No se han cambiado las cookies guardadas.")
            return 1
        log.info("Verificando el acceso a tus tickets; puede tardar hasta un minuto...")
        if not await verify_session(context):
            log.warning("Aún no se pudo confirmar el acceso a tus tickets.")
            log.info("La ventana sigue abierta: termina el login/2FA o abre Mi ahorro y pulsa ENTER de nuevo.")
            continue
        cookies = await context.cookies()
        if not cookies:
            log.warning("No hay cookies de sesión. Vuelve a completar el acceso en la ventana abierta.")
            continue
        temporary = output_file.with_name(".env.cookies.tmp")
        try:
            temporary.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(output_file)
        finally:
            temporary.unlink(missing_ok=True)
        log.info("%d cookies guardadas en '%s'", len(cookies), output_file.name)
        log.info("Sesión lista. Ejecuta: python run.py update")
        log.info("cookies.json es privado y nunca debe subirse al repositorio.")
        return 0


async def main():
    log.info("CAPTURADOR DE COOKIES — CONSUM TRACKER")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            return await capture_session(context)
        finally:
            await browser.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (KeyboardInterrupt, EOFError):
        log.info("Captura cancelada. Las cookies anteriores se conservan.")
        sys.exit(1)
    except (PlaywrightError, OSError):
        # Las excepciones de navegación pueden incluir tokens SSO en la URL.
        log.error("No se pudo completar la captura. Comprueba que Chromium siga abierto y vuelve a intentarlo.")
        sys.exit(1)
