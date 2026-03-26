# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-03-26

### Added

- Automated daily scraper (`scripts/scraper.py`) using Playwright to authenticate
  against Consum and download ticket data via the `nomastickets.consum.es` API.
- Data aggregation script (`scripts/build_data.py`) that consolidates all ticket
  JSON files into a single `data/summary.json` for the dashboard.
- Interactive cookie-capture helper (`scripts/get_cookie.py`) for the initial
  authentication and 2FA flow.
- Static dashboard (`dashboard/index.html`) built with Chart.js to visualize
  spending by category, monthly totals, top products, and saving tips.
- GitHub Actions workflow (`.github/workflows/daily.yml`) that runs every day at
  08:00 Spain time (06:00 UTC), scrapes new tickets, rebuilds the dashboard data,
  and commits the results automatically.
- GitHub Pages deployment of the dashboard at `dashboard/index.html`.
- Product category classifier with keyword matching for Spanish grocery items.
- Fallback encoding handling (UTF-8 / latin-1) when reading ticket JSON files.
- Exit-code contract for the scraper: `0` = new tickets processed, `1` = auth
  failure, `2` = no new tickets (treated as a no-op by the workflow).

### Changed

- Removed PDF-based ticket parser (`parse_pdf.py` + `pdfplumber` dependency) in
  favour of direct HTML parsing from `ticketdetail.html#ticketpre`.

[1.0.0]: https://github.com/sergiEscriva/mundoConsum/releases/tag/v1.0.0
