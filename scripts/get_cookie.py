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

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
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

ROOT = Path(__file__).resolve().parent.parent
LOGIN_URL = "https://www.consum.es/mundoconsum/acceso/"
VERIFY_URL = "https://www.consum.es/mundoconsum/mi-ahorro/"
OUTPUT_FILE = ROOT / "cookies.json"


class CookieSyncError(RuntimeError):
    """Error seguro al sincronizar secretos o disparar workflows con GitHub CLI."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Capturar cookies de sesión de Mundo Consum")
    parser.add_argument(
        "--push-secret",
        action="store_true",
        help="Subir cookies.json al secreto CONSUM_COOKIES en GitHub mediante 'gh secret set' (por stdin)",
    )
    parser.add_argument(
        "--trigger-workflow",
        action="store_true",
        help="Lanzar 'gh workflow run daily.yml' tras guardar las cookies",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Repositorio OWNER/REPO opcional para GitHub CLI",
    )
    return parser.parse_args(argv)


def _require_gh() -> str:
    gh = shutil.which("gh")
    if not gh:
        raise CookieSyncError(
            "No se encontró GitHub CLI ('gh'). Instálalo desde https://cli.github.com/ "
            "y autentícate con 'gh auth login'."
        )
    return gh


def push_cookies_secret(cookies_file: Path, *, repo: str | None = None) -> None:
    """Sube el contenido de cookies_file a CONSUM_COOKIES pasando el JSON por stdin (nunca en argv)."""
    gh = _require_gh()
    if not cookies_file.is_file():
        raise CookieSyncError(f"No existe el archivo de cookies '{cookies_file}'.")
    payload = cookies_file.read_text(encoding="utf-8")
    cmd = [gh, "secret", "set", "CONSUM_COOKIES"]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, input=payload, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        stderr_lower = (result.stderr or "").lower()
        if any(tok in stderr_lower for tok in ("auth", "login", "401", "token", "logged in")):
            raise CookieSyncError(
                "GitHub CLI ('gh') no está autenticado o no tiene permisos. "
                "Ejecuta 'gh auth login' y vuelve a intentarlo."
            )
        raise CookieSyncError(
            "No se pudo actualizar el secreto CONSUM_COOKIES en GitHub. "
            "Verifica el repositorio (--repo OWNER/REPO) y 'gh auth status'."
        )
    log.info("Secreto CONSUM_COOKIES actualizado en GitHub Actions.")


def trigger_daily_workflow(*, repo: str | None = None, workflow: str = "daily.yml") -> None:
    """Dispara el workflow diario en GitHub Actions mediante gh workflow run."""
    gh = _require_gh()
    cmd = [gh, "workflow", "run", workflow]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        stderr_lower = (result.stderr or "").lower()
        if any(tok in stderr_lower for tok in ("auth", "login", "401", "token", "logged in")):
            raise CookieSyncError(
                "GitHub CLI ('gh') no está autenticado. Ejecuta 'gh auth login' antes de lanzar el workflow."
            )
        raise CookieSyncError(
            f"No se pudo lanzar el workflow '{workflow}' en GitHub Actions. "
            "Verifica el repositorio (--repo OWNER/REPO) y 'gh auth status'."
        )
    log.info("Workflow '%s' iniciado en GitHub Actions.", workflow)


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


async def main(argv=None):
    args = parse_args(argv)
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
            code = await capture_session(context, OUTPUT_FILE)
        finally:
            await browser.close()

    if code != 0:
        return code
    try:
        if args.push_secret:
            push_cookies_secret(OUTPUT_FILE, repo=args.repo)
        if args.trigger_workflow:
            trigger_daily_workflow(repo=args.repo)
    except CookieSyncError as exc:
        log.error("%s", exc)
        return 1
    return 0


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
