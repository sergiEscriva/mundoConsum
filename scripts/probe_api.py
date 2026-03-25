"""
probe_api.py — Llama directamente a la API de nomastickets para ver
la estructura exacta de los datos y cómo descargar cada ticket.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ playwright no instalado")
    sys.exit(1)

MAIN_URL = "https://www.consum.es/mundoconsum/mi-ahorro/"
COOKIES_FILE = Path("cookies.json")


async def main():
    cookies = json.loads(COOKIES_FILE.read_text())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        )
        await context.add_cookies(cookies)

        # Paso 1: obtener el pass token de la página principal
        page = await context.new_page()
        await page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        html = await page.content()
        await page.close()

        match = re.search(r'src="(https://nomastickets\.consum\.es/app/index\.html[^"]+)"', html)
        if not match:
            print("❌ No se encontró el iframe")
            await browser.close()
            sys.exit(1)

        iframe_url = match.group(1).replace("&amp;", "&")
        pass_match = re.search(r'pass=([^&]+)', iframe_url)
        pass_token = pass_match.group(1) if pass_match else ""
        print(f"✅ pass token: {pass_token[:20]}...")

        # Paso 2: llamar a la API interceptando la respuesta completa
        api_page = await context.new_page()
        full_api_response = []

        async def capture(response):
            if "nomastickets.consum.es/api/ticket" in response.url:
                try:
                    data = await response.json()
                    full_api_response.extend(data if isinstance(data, list) else [data])
                    print(f"✅ API capturada: {response.url}")
                    print(f"   Total tickets: {len(data)}")
                except Exception as e:
                    print(f"⚠️ Error parseando: {e}")
                    text = await response.text()
                    print(f"   Raw: {text[:500]}")

        api_page.on("response", capture)
        await api_page.goto(iframe_url, wait_until="domcontentloaded", timeout=30000)
        await api_page.wait_for_timeout(4000)

        if full_api_response:
            # Guardar respuesta completa
            Path("debug_api_response.json").write_text(
                json.dumps(full_api_response, indent=2, ensure_ascii=False)
            )
            print(f"\n✅ Respuesta completa guardada en debug_api_response.json")
            print(f"\n=== PRIMER TICKET (estructura) ===")
            print(json.dumps(full_api_response[0], indent=2, ensure_ascii=False))

            if len(full_api_response) > 1:
                print(f"\n=== SEGUNDO TICKET ===")
                print(json.dumps(full_api_response[1], indent=2, ensure_ascii=False))
        else:
            print("❌ No se capturó la API")

        # Paso 3: analizar el JS de apirest.js para entender cómo descarga PDFs
        api_js_page = await context.new_page()
        await api_js_page.goto(
            "https://nomastickets.consum.es/app/js/apirest.js?v=20251201",
            wait_until="domcontentloaded", timeout=15000
        )
        js_content = await api_js_page.evaluate("() => document.body.innerText")
        Path("debug_apirest.js").write_text(js_content)
        print(f"\n✅ apirest.js guardado ({len(js_content)} chars)")

        # Buscar endpoints en el JS
        endpoints = re.findall(r'["\']([/a-zA-Z0-9_\-{}]+(?:pdf|download|ticket|detail)[/a-zA-Z0-9_\-{}]*)["\']', js_content, re.IGNORECASE)
        print("\n🔍 Endpoints encontrados en apirest.js:")
        for ep in set(endpoints):
            print(f"   {ep}")

        await api_js_page.close()
        await api_page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
