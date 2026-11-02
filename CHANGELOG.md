# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Gemini categorization for all unique historical and newly downloaded products,
  with structured JSON validation, batched requests, resumable cache, coverage
  warnings and the `python run.py categorize` command. API keys stay in ignored
  local configuration or environment variables; requests only include names and
  batch identifiers. Household and pet categories extend the existing taxonomy.
- Cross-platform local launcher: `python run.py` prepares its virtual environment,
  builds data, starts the dashboard and opens the browser. Commands for login,
  updating tickets, setup, rebuilding and tests share the same environment.
- Double-click launchers for Windows/macOS, automatic port fallback, and a local
  HTTP server that serves dashboard files without exposing cookies or raw tickets.

### Fixed

- Downloaded tickets with unreconciled line totals now produce an explicit review
  outcome (exit 4), allowing the launcher and CI to rebuild the dashboard with
  visible warnings. Real download failures remain errors. Reconciliation reports
  retain original totals and expose line totals and differences for review.
- Ticket details reuse the initialized app tab and preserve the iframe parameters
  (including dni). Navigation retries wait for pending error-page redirects before
  reinitializing the app. Diagnostics show the failing stage and HTTP/browser
  error without exposing tokens; repeated failures stop the remaining download.
- Manual login keeps Playwright events running while waiting for terminal input.
  SSO redirects and logins in another tab are verified without navigating away
  from the user's page; unsuccessful checks can retry without losing cookies.
- Monthly budgets and current-month indicators now use the Europe/Madrid calendar
  and monthly category totals instead of lifetime spending.
- Product search includes the full catalog and applies the category filter.
- Chart loading failures no longer prevent tables, search and tips from rendering.
- Tickets are normalized and validated before aggregation; zero-value tickets
  count towards averages and invalid input cannot overwrite the existing summary.
- Scraping distinguishes authentication failures (exit 1), no-op (exit 2) and
  API/network/incomplete extraction failures (exit 3). Partial tickets are retried,
  negative amounts are parsed, and JSON updates use atomic replacement.
- Failure notifications run after failed scraping; Telegram configuration is
  checked without exposing secrets. Windows installs include timezone data.

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
