# AGENTS Guide

## Big picture
- This repo tracks Consum grocery purchases and builds a static dashboard from local JSON artifacts.
- Primary pipeline is script-driven: `scripts/scraper.py` -> `data/tickets/*.json` -> `scripts/build_data.py` -> `data/summary.json` -> `dashboard/index.html`.
- There is also a legacy docs pipeline: `docs/index.html` reads `docs/data/summary.json` (different schema from `data/summary.json`).

## Components and boundaries
- `scripts/get_cookie.py`: interactive login + 2FA capture; writes `cookies.json` for later automation.
- `scripts/scraper.py`: Playwright-based fetcher that combines API list retrieval with DOM extraction of line items.
- `scripts/parse_pdf.py`: alternate parser for local PDF tickets in `data/tickets`; only processes PDFs without sibling JSON.
- `scripts/build_data.py`: aggregation layer that computes KPIs, categories, top products, and recommendations.
- `dashboard/index.html`: current UI contract for `data/summary.json`.
- `docs/index.html`: older UI with a different JSON contract (`last_updated`, `stats`, `recommendations`).

## Critical workflows (from repo behavior)
- Install deps: `pip install -r requirements.txt` then `playwright install chromium`.
- Capture/refresh session cookies: `python scripts/get_cookie.py`.
- Fetch new tickets from Consum: `python scripts/scraper.py`.
- Rebuild dashboard data: `python scripts/build_data.py`.
- Optional PDF-only path: `python scripts/parse_pdf.py` or `python scripts/parse_pdf.py <ticket.pdf>`.

## Project-specific conventions
- JSON is written with `ensure_ascii=False` to preserve Spanish text/emojis (`scripts/scraper.py`, `scripts/build_data.py`, `scripts/parse_pdf.py`).
- Ticket ids are stored as filename stems (`data/tickets/<Id>.json`) and reused as canonical identifiers.
- `scripts/scraper.py` marks provenance via `source`: `dom_extracted` (preferred) or `api_only` fallback.
- `scripts/build_data.py` ignores malformed tickets missing both `date` and `items`.
- Dates are normalized to `YYYY-MM-DD`; monthly buckets use `YYYY-MM` slices.
- Category keyword maps are duplicated in `scripts/scraper.py` and `scripts/parse_pdf.py`; update both if taxonomy changes.

## Integration points
- Consum web + API endpoints are hardcoded in `scripts/scraper.py` (`https://www.consum.es/...` and `https://nomastickets.consum.es/api`).
- Auth uses `CONSUM_COOKIES` env var first, then local `cookies.json` fallback (`load_cookies` in `scripts/scraper.py`).
- Frontend charts use Chart.js CDN in `dashboard/index.html` and `docs/index.html`.

## Debugging and gotchas
- `scripts/scraper.py` exits with code `2` when all tickets already have items; treat as no-op, not fatal.
- `scripts/probe_api.py` is a reverse-engineering helper; it writes `debug_api_response.json` and `debug_apirest.js`.
- `dashboard/index.html` chooses JSON path by runtime: localhost/file mode uses `../data/summary.json`, hosted mode uses `./data/summary.json`.
- Keep `data/summary.json` and `docs/data/summary.json` contracts separate unless you intentionally migrate one dashboard to the other.

