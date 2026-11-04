# Guia de AGENTS

## Vision general
- Este repo registra compras de supermercado en Consum y construye un dashboard estatico a partir de artefactos JSON locales.
- El pipeline principal esta guiado por scripts: `scripts/scraper.py` -> `data/tickets/*.json` -> `scripts/build_data.py` -> `data/summary.json` -> `dashboard/index.html`.
- `docs/index.html` es ahora una redirección mínima hacia `../dashboard/` (sin contrato JSON duplicado), y `scripts/export_public_site.py` construye el bundle estático anonimizado para GitHub Pages (`.github/workflows/pages.yml`).

## Componentes y limites
- `scripts/get_cookie.py`: login interactivo + captura de 2FA; escribe `cookies.json` y soporta `--push-secret` (por `stdin` a `gh secret set CONSUM_COOKIES`), `--trigger-workflow` y `--repo`.
- `scripts/scraper.py`: fetcher con Playwright que obtiene listas paginadas de tickets via API y extrae artículos y descuentos (`discounts`) desde `ticketdetail.html` (`#ticketpre`). Omite por defecto tickets conciliados y `dom_extracted` `needs_review` ya extraídos (usa `--retry-review` para re-descargarlos), reintentando siempre los `incomplete`.
- `scripts/build_data.py`: capa de agregacion que calcula KPIs, `savings_summary`, `inflation` (IPC personal y alertas de reduflación), `budget_summary` (ritmo diario y proyección), `private_brand_share_pct`, `nova_distribution` y modo `--anonymize`.
- `scripts/gemini_categories.py`: cliente REST de Gemini con lotes, reintentos ante fallos transitorios HTTP 5xx / red (`URLError`, `TimeoutError`), caché intacta `data/product_categories.json` (`{KEY: category_str}`) y metadatos enriquecidos en `data/product_metadata.json`.
- `scripts/notify_telegram.py`: resumen proactivo en español en Telegram tras compras nuevas (`exit_code` `0` o `4`), usando solo stdlib y sin exponer jamás `TELEGRAM_BOT_TOKEN`.
- `scripts/export_public_site.py`: exportador estático que copia solo `index.html`, `dashboard/index.html`, `data/budgets.json` y `data/summary.json` anonimizado.
- `dashboard/index.html`: interfaz única para `data/summary.json`.

## Flujos criticos (segun el comportamiento del repo)
- Flujo local recomendado: `python run.py` crea/reutiliza `.venv`, instala dependencias si cambian, regenera datos y abre el dashboard. No requiere activar el entorno ni instalar Chromium para ver datos existentes.
- `python run.py update` prepara Chromium, guía el login si falta o caduca la sesión y descarga/regenera. `login`, `setup`, `build` y `test` están disponibles como comandos independientes.
- El lanzador fija el directorio del proyecto, escucha en loopback y sirve solo el dashboard y sus JSON. Los scripts directos siguientes siguen siendo válidos.
- `python run.py categorize` pide la clave mediante getpass si falta y la guarda en `.env` ignorado. Variables: `GEMINI_API_KEY`, `GEMINI_MODEL`. No imprimir ni versionar claves. `--refresh-categories` fuerza nuevas llamadas de todo el catálogo.
- El resumen aplica la caché `data/product_categories.json` sin reescribir los tickets originales. Los builds normales toleran fallos de API con avisos de cobertura; categorize exige completar los lotes. No aceptar respuestas parciales, categorías fuera de la taxonomía, ids duplicados ni salida truncada.
- `GEMINI_MODEL` usa `auto` por defecto: consultar ListModels con la misma clave, filtrar generateContent/texto y preferir Flash estable. Reutilizar el modelo real de la caché cuando siga disponible. No adivinar nombres nuevos, mezclar modelos dentro de una caché ni sustituir silenciosamente un modelo fijado explícitamente. Los fallos de catálogo/clave/cuota son distintos de un 404 del modelo.
- Reintentar HTTP 5xx con espera creciente y variación aleatoria; respetar Retry-After. Si pide más de 60 s, devolver un aviso de espera sin reintentar ni probar otro modelo antes. En auto, un 404 o 5xx persistente del primer lote permite probar hasta otras dos opciones. Tras un lote válido, conservar el progreso y fallar sin mezclar modelos. No alternar modelos por errores de clave o cuota.
- Mantener 15 s entre intentos generateContent por modelo en el proceso, contando errores. Respetar google.rpc.RetryInfo además de Retry-After; un 429 sin cuota diaria identificada espera al menos 60 s entre reintentos. Una QuotaFailure PerDay se devuelve sin reintentar ni alternar modelo. Preferir Flash Lite estable en auto, respetando el modelo de la caché. Este ritmo no controla otras aplicaciones del mismo proyecto.
- Instalar dependencias: `pip install -r requirements.txt` y despues `playwright install chromium`.
- Capturar/refrescar cookies de sesion: `python scripts/get_cookie.py`.
- Descargar tickets nuevos de Consum: `python scripts/scraper.py`.
- Regenerar datos del dashboard: `python scripts/build_data.py`.

## Convenciones especificas del proyecto
- Los JSON se escriben con `ensure_ascii=False` para preservar texto/emojis en espanol (`scripts/scraper.py`, `scripts/build_data.py`).
- Los ids de ticket se guardan como nombre base de archivo (`data/tickets/<Id>.json`) y se reutilizan como identificadores canonicos.
- `scripts/scraper.py` marca la procedencia mediante `source`: `dom_extracted` (items parseados desde `#ticketpre`) o fallback `api_only`.
- `scripts/ticket_data.py` normaliza números y valida fechas y líneas. El agregador rechaza archivos inválidos sin sustituir el resumen anterior ni reescribir tickets.
- Las fechas se normalizan a `YYYY-MM-DD`; los buckets mensuales usan cortes `YYYY-MM`.
- `scripts/scraper.py` omite solo tickets con líneas válidas que concilian con el total (tolerancia 0,02 €); los incompletos se reintentan.

## Puntos de integracion
- Los endpoints web + API de Consum estan hardcodeados en `scripts/scraper.py` (`https://www.consum.es/...` y `https://nomastickets.consum.es/api`).
- La autenticacion usa primero la variable de entorno `CONSUM_COOKIES`, y luego fallback al `cookies.json` local (`load_cookies` en `scripts/scraper.py`).
- La extraccion de detalle depende del token `pass` de la URL del iframe y construye `ticketdetail.html?ticketId=...&pass=...&v=...`.
- Conservar también `dni` y otros parámetros del iframe; no abrir una pestaña nueva por ticket. El reintento reinicializa la app tras asentar las navegaciones pendientes. Los logs de excepción deben ocultar URLs con tokens y omitir el call log de Playwright.
- La versión del detalle se obtiene del iframe; `20251201` es solo el fallback.
- Los graficos del frontend usan el CDN de Chart.js en `dashboard/index.html` y `docs/index.html`.

## Depuracion y gotchas
- `scripts/scraper.py` sale con codigo `2` cuando todos los tickets ya tienen items; tratalo como no-op, no como error fatal.
- `scripts/scraper.py` sale con código `1` para cookies ausentes/invalidas o sesión rechazada y `3` para errores de red/API, bootstrap o detalles inaccesibles. El código `4` indica que se descargaron los detalles pero hay diferencias de conciliación o formatos sin líneas reconocidas: el lanzador y CI regeneran el resumen con avisos, conservando los totales originales.
- `data_quality.issues` en el resumen muestra los tickets sin conciliar, su total original, suma de líneas y diferencia. No inventar artículos/descuentos ni aumentar la tolerancia para ocultar un descuadre.
- La lista se descarga por API paginada y los detalles por URL directa; no hay helpers de scroll ni fallback por clic.
- `dashboard/index.html` carga `../data/summary.json` mediante HTTP. El resumen incluye `products` (catálogo completo) y `monthly_categories` (totales por mes).
- Mantén separados los contratos de `data/summary.json` y `docs/data/summary.json`, salvo que migres intencionalmente un dashboard al otro.
