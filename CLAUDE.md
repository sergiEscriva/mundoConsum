# CLAUDE.md

Este archivo proporciona orientación a Claude Code (claude.ai/code) al trabajar con el código de este repositorio.

## Descripción del proyecto

Dashboard personal para registrar compras en el supermercado Consum. Los datos se obtienen diariamente mediante GitHub Actions y se sirven como sitio estático vía GitHub Pages.

## Configuración inicial

```bash
pip install -r requirements.txt
playwright install chromium
```

## Comandos habituales

```bash
# Capturar cookies de sesión y opcionalmente subirlas a GitHub Secret + lanzar workflow
python run.py login --push-secret --trigger-workflow

# Descargar tickets nuevos desde la API de Consum (--retry-review reintenta también needs_review)
python scripts/scraper.py
python scripts/scraper.py --retry-review

# Regenerar los datos del dashboard (--anonymize oculta tiendas reales e IDs de ticket)
python scripts/build_data.py
python scripts/build_data.py --anonymize

# Exportar bundle público anonimizado para GitHub Pages
python scripts/export_public_site.py --out dist_site
```

## Arquitectura

**Pipeline principal:**
`scraper.py` → `data/tickets/*.json` → `build_data.py` → `data/summary.json` → `dashboard/index.html`

### Scripts principales

- **run.py**: Lanzador unificado (`start`, `update`, `login`, `setup`, `build`, `test`, `categorize`) con servidor HTTP local aislado en `127.0.0.1` y activación automática de `REQUIRE_BROWSER_TESTS=1` en `test` cuando Chromium está disponible.
- **scraper.py**: Se autentica con cookies, extrae el token `pass` del iframe en Consum, pagina la API de tickets (`POST nomastickets.consum.es/api/ticket/v2`) y parsea artículos + descuentos (`discounts`) desde `<pre id="ticketpre">`. Por defecto omite tickets conciliados y `dom_extracted` con `needs_review` ya extraídos (reintentables con `--retry-review`), pero sigue reintentando tickets `incomplete` sin líneas.
- **build_data.py**: Agrega todos los tickets en `data/summary.json` con KPIs, `savings_summary`, `inflation` (IPC personal ponderado y alertas de reduflación), `budget_summary` (ritmo diario y proyección mensual), `private_brand_share_pct`, `nova_distribution` (NOVA 1–4), desglose mensual completo y modo `--anonymize`.
- **gemini_categories.py**: Clasifica productos por lotes con Gemini manteniendo `data/product_categories.json` (`{KEY: category_str}`) y enriqueciendo `data/product_metadata.json` (`canonical_name`, `is_private_brand`, `brand_tier`, `nova_group`).
- **notify_telegram.py**: Envía un resumen en español a Telegram (stdlib pura) cuando entran compras nuevas (`exit_code` `0` o `4`), omitiendo el envío limpiamente si faltan `TELEGRAM_BOT_TOKEN` o `TELEGRAM_CHAT_ID`.
- **export_public_site.py**: Genera un bundle estático público y anonimizado (`index.html`, `dashboard/index.html`, `data/budgets.json`, `data/summary.json`) desplegado por `.github/workflows/pages.yml` sin incluir jamás `data/tickets/*.json` ni `cookies.json`.
- **get_cookie.py**: Login interactivo con 2FA; soporta `--push-secret` (envía `cookies.json` por `stdin` a `gh secret set CONSUM_COOKIES`), `--trigger-workflow` y `--repo`.

### Autenticación

Las cookies de sesión se cargan desde `CONSUM_COOKIES` (secreto de GitHub Actions) o desde `cookies.json` en la raíz del proyecto. Caducan cada 2–4 semanas y se renuevan con `python run.py login --push-secret --trigger-workflow`.

### Códigos de salida del scraper

- `0` = éxito, se han procesado y conciliado tickets nuevos
- `1` = fallo de autenticación (cookies ausentes o caducadas / redirección SSO)
- `2` = todos los tickets ya tienen artículos procesados (sin cambios — no es un error)
- `3` = fallo de red, API o extracción incompleta
- `4` = tickets descargados pero con diferencias de conciliación pendientes de revisión

El workflow de GitHub Actions (`daily.yml`) omite `build_data.py` y el commit cuando el código es `2`, y lanza `notify_telegram.py` cuando el código es `0` o `4`.

### Formato de datos

Cada ticket se almacena como `data/tickets/<Id>.json` con los campos: `id`, `ticket_code`, `store`, `store_address`, `date` (`YYYY-MM-DD`), `datetime_iso`, `total`, `payment_method`, `items[]`, `discounts[]` (opcional, importes `<= 0`), `tax_breakdown[]`, `source`, `reconciliation`, `extraction_status`.

`build_data.py` valida todos los tickets mediante `ticket_data.py` y aborta sin sobrescribir `data/summary.json` si encuentra archivos malformados.

### Dashboard

`dashboard/index.html` lee `../data/summary.json` y `../data/budgets.json`. `docs/index.html` redirige directamente a `../dashboard/` para mantener un único contrato de interfaz.

### Valores hardcodeados a vigilar

- Parámetro de versión de la API: `v=20251201` (fallback en `scraper.py`) — la versión activa se toma dinámicamente del iframe
- Tamaño de página de la API de tickets: 10, máximo 100 páginas
