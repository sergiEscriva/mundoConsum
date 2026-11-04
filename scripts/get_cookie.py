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
import re
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
        "--sync-only",
        action="store_true",
        help="Usar el cookies.json ya guardado localmente sin abrir el navegador",
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
    if gh:
        return gh
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI/gh.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/GitHub CLI/gh.exe",
        ]
        for candidate in candidates:
            if str(candidate) and candidate.is_file():
                return str(candidate)
    raise CookieSyncError(
        "No se encontró GitHub CLI ('gh'). Instálalo desde https://cli.github.com/ "
        "y autentícate con 'gh auth login'."
    )


def infer_github_repo(root: Path = ROOT) -> str | None:
    """Detecta OWNER/REPO a partir del remote 'origin' si está disponible."""
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    url = (proc.stdout or "").strip()
    match = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$", url)
    return match.group(1) if match else None


def _sanitize_gh_stderr(stderr: str | None) -> str:
    if not stderr:
        return ""
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", stderr)
    cleaned = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b", "[REDACTED]", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned[:240]


def _format_gh_failure(action_msg: str, stderr: str | None) -> str:
    detail = _sanitize_gh_stderr(stderr)
    lower = detail.lower()
    if any(tok in lower for tok in ("not logged in", "auth login", "401", "authentication", "bad credentials")):
        hint = "GitHub CLI ('gh') no tiene sesión válida. Ejecuta: gh auth login -h github.com -p https -w"
    elif any(tok in lower for tok in ("403", "404", "resource not accessible", "must have admin", "scope", "insufficient")):
        hint = (
            "Tu token de 'gh' no tiene permisos sobre Secrets/Actions en este repositorio. "
            "Ejecuta: gh auth refresh -h github.com -s repo,workflow (o usa login web con 'gh auth login -w')."
        )
    else:
        hint = "Verifica 'gh auth status' y el acceso al repositorio (--repo OWNER/REPO)."
    return f"{action_msg} {hint}" + (f" [Detalle de gh: {detail}]" if detail else "")


def push_cookies_secret(cookies_file: Path, *, repo: str | None = None) -> None:
    """Sube el contenido de cookies_file a CONSUM_COOKIES pasando el JSON por stdin (nunca en argv)."""
    gh = _require_gh()
    if not cookies_file.is_file():
        raise CookieSyncError(f"No existe el archivo de cookies '{cookies_file}'.")
    payload = cookies_file.read_text(encoding="utf-8-sig")
    effective_repo = repo or infer_github_repo(ROOT)
    cmd = [gh, "secret", "set", "CONSUM_COOKIES"]
    if effective_repo:
        cmd.extend(["--repo", effective_repo])
    result = subprocess.run(
        cmd,
        input=payload,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise CookieSyncError(
            _format_gh_failure("No se pudo actualizar el secreto CONSUM_COOKIES en GitHub.", result.stderr)
        )
    log.info("Secreto CONSUM_COOKIES actualizado en GitHub Actions%s.", f" ({effective_repo})" if effective_repo else "")


def trigger_daily_workflow(*, repo: str | None = None, workflow: str = "daily.yml") -> None:
    """Dispara el workflow diario en GitHub Actions mediante gh workflow run."""
    gh = _require_gh()
    effective_repo = repo or infer_github_repo(ROOT)
    cmd = [gh, "workflow", "run", workflow]
    if effective_repo:
        cmd.extend(["--repo", effective_repo])
    result = subprocess.run(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise CookieSyncError(
            _format_gh_failure(f"No se pudo lanzar el workflow '{workflow}' en GitHub Actions.", result.stderr)
        )
    log.info("Workflow '%s' iniciado en GitHub Actions%s.", workflow, f" ({effective_repo})" if effective_repo else "")


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
    if args.sync_only:
        if not OUTPUT_FILE.is_file():
            log.error("No existe '%s'. Ejecuta primero 'python run.py login' para capturar la sesión.", OUTPUT_FILE.name)
            return 1
        try:
            push_cookies_secret(OUTPUT_FILE, repo=args.repo)
            if args.trigger_workflow:
                trigger_daily_workflow(repo=args.repo)
        except CookieSyncError as exc:
            log.error("%s", exc)
            return 2
        return 0

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
        log.info(
            "Tus cookies YA están guardadas en '%s'. Cuando corrijas el acceso de 'gh', "
            "sube las cookies sin repetir el login con: python run.py sync%s",
            OUTPUT_FILE.name,
            " --trigger-workflow" if args.trigger_workflow else "",
        )
        return 2
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
