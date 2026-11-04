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
# Una sola vez: capturar cookies de sesión (abre navegador real para 2FA)
python scripts/get_cookie.py

# Descargar tickets nuevos desde la API de Consum
python scripts/scraper.py

# Regenerar los datos del dashboard
python scripts/build_data.py

```

## Arquitectura

**Pipeline principal:**
`scraper.py` → `data/tickets/*.json` → `build_data.py` → `data/summary.json` → `dashboard/index.html`

### Scripts principales

- **run.py**: Lanzador unificado (`start`, `update`, `login`, `setup`, `build`, `test`, `categorize`) con servidor HTTP local aislado en `127.0.0.1`.
- **scraper.py**: Se autentica con cookies, extrae el token `pass` de la URL del iframe en la página de Consum, pagina la API de tickets (`POST nomastickets.consum.es/api/ticket/v2`), luego obtiene la página de detalle de cada ticket y parsea los artículos desde `<pre id="ticketpre">`. Solo omite tickets ya conciliados. Escribe en `data/tickets/`.
- **build_data.py**: Agrega todos los JSON de tickets en `data/summary.json` con KPIs, totales mensuales, desglose por categorías (incluyendo clasificación opcional con Gemini vía `gemini_categories.py`), productos más comprados y consejos de ahorro/salud.
- **get_cookie.py**: Abre un navegador visible para el login interactivo con 2FA y guarda las cookies en `cookies.json` (raíz del repositorio).

### Autenticación

Las cookies de sesión se cargan desde la variable de entorno `CONSUM_COOKIES` (secreto de GitHub Actions) o, si no existe, desde `cookies.json` en la raíz del proyecto (desarrollo local). Las cookies caducan cada 2–4 semanas y hay que renovarlas con `python run.py login` o `python scripts/get_cookie.py`.

### Códigos de salida del scraper

- `0` = éxito, se han procesado y conciliado tickets nuevos
- `1` = fallo de autenticación (cookies ausentes o caducadas / redirección SSO)
- `2` = todos los tickets ya tienen artículos conciliados (sin cambios — no es un error)
- `3` = fallo de red, API o extracción incompleta
- `4` = tickets descargados pero con diferencias de conciliación pendientes de revisión

El workflow de GitHub Actions (`daily.yml`) omite `build_data.py` y el commit cuando el código es `2` para evitar commits vacíos.

### Formato de datos

Cada ticket se almacena como `data/tickets/<Id>.json` con los campos: `id`, `ticket_code`, `store`, `store_address`, `date` (`YYYY-MM-DD`), `datetime_iso`, `total`, `payment_method`, `items[]`, `tax_breakdown[]`, `source`, `reconciliation`, `extraction_status`.

`build_data.py` valida todos los tickets mediante `ticket_data.py` y aborta sin sobrescribir `data/summary.json` si encuentra archivos malformados. Los totales mensuales usan claves `YYYY-MM`.

### Dashboard

`dashboard/index.html` lee `data/summary.json` con la ruta `../data/summary.json` (relativa al directorio `dashboard/`). Usa Chart.js desde CDN. `docs/index.html` es una UI alternativa legacy con un esquema JSON diferente — mantener los dos contratos separados.

### Valores hardcodeados a vigilar

- Parámetro de versión de la API: `v=20251201` en scraper.py — actualizar si Consum cambia el frontend
- Tamaño de página de la API de tickets: 10, máximo 100 páginas
