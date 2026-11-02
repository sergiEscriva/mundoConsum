#!/usr/bin/env python3
"""Lanzador local: python run.py [start|update|login|setup|build|test|categorize]."""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import venv
import webbrowser

ROOT = Path(__file__).resolve().parent


class LocalError(RuntimeError):
    pass


def execute(args, *, env=None):
    """Usar siempre la raíz del proyecto, aunque se invoque desde otra carpeta."""
    return subprocess.run([str(arg) for arg in args], cwd=ROOT, env=env).returncode


def prepare_python():
    """Crear/reutilizar .venv e instalar solo cuando cambien los requisitos."""
    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Preparando el entorno local .venv…", flush=True)
        try:
            venv.EnvBuilder(with_pip=True).create(environment)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LocalError(
                "No se pudo crear .venv. En Debian/Ubuntu instala python3-venv "
                "y vuelve a ejecutar el lanzador."
            ) from exc
    requirements = ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = environment / ".consum-requirements.sha256"
    recorded = stamp.read_text().strip() if stamp.exists() else ""
    probe = subprocess.run(
        [str(python), "-c", "import playwright, pytest; from zoneinfo import ZoneInfo; ZoneInfo('Europe/Madrid')"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if recorded != fingerprint or probe.returncode:
        print("Instalando/verificando dependencias…", flush=True)
        if execute([python, "-m", "pip", "install", "-r", requirements]):
            raise LocalError("No se pudieron instalar las dependencias. Revisa la conexión y vuelve a intentarlo.")
        stamp.write_text(fingerprint, encoding="utf-8")
    return python


def prepare_browser(python, with_deps=False):
    print("Preparando Chromium para iniciar sesión y descargar tickets…", flush=True)
    args = [python, "-m", "playwright", "install", "chromium"]
    if with_deps:
        args.append("--with-deps")
    if execute(args):
        raise LocalError(
            "No se pudo preparar Chromium. Revisa la conexión. Si faltan bibliotecas en Linux, "
            "ejecuta: python run.py setup --with-deps"
        )


def build(python, *, require_gemini=False, refresh=False):
    args = [python, ROOT / "scripts/build_data.py"]
    if require_gemini:
        args.append("--require-gemini")
    if refresh:
        args.append("--refresh-categories")
    if execute(args):
        raise LocalError("No se pudieron generar los datos. Revisa los archivos indicados en los mensajes anteriores.")


def login(python):
    if execute([python, ROOT / "scripts/get_cookie.py"]):
        raise LocalError("No se completó el inicio de sesión. Vuelve a ejecutar: python run.py login")


def update(python):
    """Guiar el login inicial o la renovación y tratar el código 2 como no-op."""
    environment = os.environ.copy()
    has_cookies = bool(environment.get("CONSUM_COOKIES")) or (ROOT / "cookies.json").is_file()
    if not has_cookies:
        if not sys.stdin.isatty():
            raise LocalError("Faltan cookies. Ejecuta python run.py login en una terminal interactiva.")
        print("Primero inicia sesión en la ventana de Consum y completa el 2FA.", flush=True)
        login(python)
    result = execute([python, ROOT / "scripts/scraper.py"], env=environment.copy())
    if result == 1 and sys.stdin.isatty():
        print("La sesión no es válida. Vamos a renovarla en el navegador.", flush=True)
        login(python)
        # Las cookies recién capturadas deben prevalecer frente a una variable antigua.
        environment.pop("CONSUM_COOKIES", None)
        result = execute([python, ROOT / "scripts/scraper.py"], env=environment.copy())
    if result not in (0, 2, 4):
        raise LocalError(f"La descarga no se completó (código {result}). Revisa los mensajes anteriores y reintenta.")
    build(python)
    if result == 4:
        print("Dashboard actualizado con avisos: hay tickets pendientes de revisión. "
              "Consulta sus diferencias en el dashboard; las categorías pueden estar incompletas.", flush=True)
    else:
        print("Datos actualizados. Recarga el dashboard si ya lo tienes abierto.", flush=True)


class DashboardHandler(SimpleHTTPRequestHandler):
    """Servir únicamente el dashboard y sus JSON, nunca cookies ni el checkout."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_head(self):
        path = unquote(urlsplit(self.path).path)
        allowed = {"/", "/index.html", "/dashboard/", "/dashboard/index.html",
                   "/data/summary.json", "/data/budgets.json"}
        if path not in allowed:
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args):
        # La consola queda reservada a instrucciones y errores del lanzador.
        pass


def create_server(port):
    try:
        return ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError:
        if port == 0:
            raise
        print(f"No se pudo usar el puerto {port}; buscando otro disponible…", flush=True)
        return ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)


def serve(port, open_browser=True):
    with create_server(port) as server:
        url = f"http://127.0.0.1:{server.server_port}/dashboard/"
        print(f"\nDashboard: {url}\nDéjalo abierto. Para detenerlo, pulsa Ctrl+C.\n", flush=True)
        if open_browser:
            try:
                if not webbrowser.open(url):
                    print("Abre la dirección anterior en tu navegador.", flush=True)
            except webbrowser.Error:
                print("Abre la dirección anterior en tu navegador.", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            print("\nDashboard detenido.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Consum Tracker local: un comando para abrir el dashboard.")
    parser.add_argument("command", nargs="?", default="start",
                        choices=["start", "update", "login", "setup", "build", "test", "categorize"],
                        help="start: abrir; update: descargar; login: renovar sesión; setup: preparar; build: regenerar; test: probar; categorize: clasificar con Gemini")
    parser.add_argument("--port", type=int, default=8000, help="puerto preferido del dashboard (8000)")
    parser.add_argument("--no-browser", action="store_true", help="no abrir automáticamente el navegador")
    parser.add_argument("--with-deps", action="store_true", help="instalar también bibliotecas de Chromium (Linux)")
    parser.add_argument("--refresh-categories", action="store_true", help="volver a consultar a Gemini incluso para productos en caché")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("el puerto debe estar entre 0 y 65535")
    if args.refresh_categories and args.command not in {"categorize", "build"}:
        parser.error("--refresh-categories se usa con categorize o build")
    if sys.version_info < (3, 10):
        parser.error("se necesita Python 3.10 o superior; se recomienda 3.12")
    try:
        python = prepare_python()
        if args.command in {"setup", "login", "update"}:
            prepare_browser(python, args.with_deps)
        if args.command == "setup":
            print("Entorno preparado. Para abrir el dashboard: python run.py", flush=True)
        elif args.command == "login":
            login(python)
            print("Sesión guardada. Para descargar tus tickets: python run.py update", flush=True)
        elif args.command == "update":
            update(python)
        elif args.command == "build":
            if args.refresh_categories:
                build(python, refresh=True)
            else:
                build(python)
        elif args.command == "categorize":
            if execute([python, ROOT / "scripts/gemini_categories.py", "--configure"]):
                raise LocalError("No se pudo configurar Gemini. Revisa los mensajes anteriores.")
            build(python, require_gemini=True, refresh=args.refresh_categories)
            print("Catálogo clasificado con Gemini. Recarga el dashboard o ábrelo con python run.py.", flush=True)
        elif args.command == "test":
            return execute([python, "-m", "pytest", ROOT / "tests", "-v"])
        else:
            build(python)
            serve(args.port, not args.no_browser)
        return 0
    except KeyboardInterrupt:
        print("\nOperación cancelada.", flush=True)
        return 130
    except (LocalError, OSError) as exc:
        print(f"\nError: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
