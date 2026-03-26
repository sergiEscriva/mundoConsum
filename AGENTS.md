# Guia de AGENTS

## Vision general
- Este repo registra compras de supermercado en Consum y construye un dashboard estatico a partir de artefactos JSON locales.
- El pipeline principal esta guiado por scripts: `scripts/scraper.py` -> `data/tickets/*.json` -> `scripts/build_data.py` -> `data/summary.json` -> `dashboard/index.html`.
- Tambien existe un pipeline legacy para docs: `docs/index.html` lee `docs/data/summary.json` (esquema distinto de `data/summary.json`).

## Componentes y limites
- `scripts/get_cookie.py`: login interactivo + captura de 2FA; escribe `cookies.json` para automatizacion posterior.
- `scripts/scraper.py`: fetcher con Playwright que obtiene listas paginadas de tickets via API, y luego extrae lineas de articulo desde `ticketdetail.html` (`#ticketpre`) usando una URL directa por ticket, con fallback por click en DOM.
- `scripts/parse_pdf.py`: parser alternativo para tickets PDF locales en `data/tickets`; solo procesa PDFs sin JSON hermano.
- `scripts/build_data.py`: capa de agregacion que calcula KPIs, categorias, top productos y recomendaciones.
- `dashboard/index.html`: contrato de UI actual para `data/summary.json`.
- `docs/index.html`: UI antigua con un contrato JSON distinto (`last_updated`, `stats`, `recommendations`).

## Flujos criticos (segun el comportamiento del repo)
- Instalar dependencias: `pip install -r requirements.txt` y despues `playwright install chromium`.
- Capturar/refrescar cookies de sesion: `python scripts/get_cookie.py`.
- Descargar tickets nuevos de Consum: `python scripts/scraper.py`.
- Regenerar datos del dashboard: `python scripts/build_data.py`.
- Ruta opcional solo PDF: `python scripts/parse_pdf.py` o `python scripts/parse_pdf.py <ticket.pdf>`.

## Convenciones especificas del proyecto
- Los JSON se escriben con `ensure_ascii=False` para preservar texto/emojis en espanol (`scripts/scraper.py`, `scripts/build_data.py`, `scripts/parse_pdf.py`).
- Los ids de ticket se guardan como nombre base de archivo (`data/tickets/<Id>.json`) y se reutilizan como identificadores canonicos.
- `scripts/scraper.py` marca la procedencia mediante `source`: `dom_extracted` (items parseados desde `#ticketpre`) o fallback `api_only`.
- `scripts/build_data.py` ignora tickets malformados que no tienen ni `date` ni `items`.
- Las fechas se normalizan a `YYYY-MM-DD`; los buckets mensuales usan cortes `YYYY-MM`.
- Los mapas de palabras clave por categoria estan duplicados en `scripts/scraper.py` y `scripts/parse_pdf.py`; actualiza ambos si cambia la taxonomia.
- `scripts/scraper.py` solo procesa tickets que todavia no tengan `items` en `data/tickets/<Id>.json`.

## Puntos de integracion
- Los endpoints web + API de Consum estan hardcodeados en `scripts/scraper.py` (`https://www.consum.es/...` y `https://nomastickets.consum.es/api`).
- La autenticacion usa primero la variable de entorno `CONSUM_COOKIES`, y luego fallback al `cookies.json` local (`load_cookies` en `scripts/scraper.py`).
- La extraccion de detalle depende del token `pass` de la URL del iframe y construye `ticketdetail.html?ticketId=...&pass=...&v=...`.
- `scripts/scraper.py` actualmente fija `mytickets.html?v=20251201`; manten `v` sincronizado si Consum cambia versionado del frontend.
- Los graficos del frontend usan el CDN de Chart.js en `dashboard/index.html` y `docs/index.html`.

## Depuracion y gotchas
- `scripts/scraper.py` sale con codigo `2` cuando todos los tickets ya tienen items; tratalo como no-op, no como error fatal.
- `scripts/scraper.py` sale con codigo `1` en fallos de sesion/auth/bootstrap (cookies ausentes/invalidas, sesion expirada, iframe no encontrado) y cuando se procesan `0` tickets.
- El render de la lista de tickets esta virtualizado/infinite-scroll; el scraper usa helpers de scroll (`load_all_tickets_in_list`, `ensure_ticket_visible`) y puede requerir ajuste de timeouts si cambia la latencia de UI.
- `scripts/probe_api.py` es una utilidad de ingenieria inversa; escribe `debug_api_response.json` y `debug_apirest.js`.
- `dashboard/index.html` elige ruta JSON segun runtime: modo localhost/file usa `../data/summary.json`, modo hosted usa `./data/summary.json`.
- Mantén separados los contratos de `data/summary.json` y `docs/data/summary.json`, salvo que migres intencionalmente un dashboard al otro.

