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
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print(" Playwright no está instalado.")
    print("   Ejecuta: pip install playwright && playwright install chromium")
    sys.exit(1)

LOGIN_URL = "https://www.consum.es/mundoconsum/acceso/"
TARGET_URL = "https://www.consum.es/mundoconsum/mi-ahorro/"
OUTPUT_FILE = Path("cookies.json")


async def main():
    print("\n" + "=" * 55)
    print("    CAPTURADOR DE COOKIES — CONSUM TRACKER")
    print("=" * 55)

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

        print("\n Paso 1: Abriendo la página de login de Consum...")
        await page.goto(LOGIN_URL, wait_until="networkidle")

        print("\n Paso 2: Inicia sesión manualmente en el navegador:")
        print("   → Introduce tu email y contraseña")
        print("   → Completa el 2FA (SMS o email)")
        print("   → Espera a que cargue el panel de usuario")
        print("\n   Cuando veas tu cuenta cargada, vuelve aquí.")

        input("\n Pulsa ENTER cuando hayas iniciado sesión completamente: ")

        # Verificar que realmente está logueado
        current_url = page.url
        if "acceso" in current_url or "login" in current_url.lower():
            print("\n  Parece que aún estás en la página de login.")
            print("   ¿Seguro que completaste el proceso? (s/n): ", end="")
            resp = input().strip().lower()
            if resp != "s":
                print("Cancelado. Vuelve a ejecutar el script cuando estés listo.")
                await browser.close()
                return

        cookies = await context.cookies()

        if not cookies:
            print("\n No se encontraron cookies. Asegúrate de haber iniciado sesión.")
            await browser.close()
            return

        OUTPUT_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False))

        print(f"\n ¡Listo! {len(cookies)} cookies guardadas en '{OUTPUT_FILE}'")
        print("\n" + "=" * 55)
        print("    SIGUIENTE PASO — GUARDAR EN GITHUB SECRETS")
        print("=" * 55)
        print("""
  1. Ve a tu repositorio en GitHub
  2. Settings → Secrets and variables → Actions
  3. Haz clic en "New repository secret"
  4. Name: CONSUM_COOKIES
  5. Value: copia y pega TODO el contenido de cookies.json
  6. Haz clic en "Add secret"

    IMPORTANTE:
     - El archivo cookies.json ya está en .gitignore
     - NUNCA lo subas a GitHub directamente
     - Las cookies duran ~15-30 días. Cuando el Action falle,
       repite este proceso y actualiza el Secret.
""")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
