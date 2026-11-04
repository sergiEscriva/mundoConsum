"""Tests para scripts/export_public_site.py y limpieza de privacidad en GitHub Pages."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import export_public_site


def test_export_public_site_copies_only_safe_files_and_anonymizes_summary(tmp_path):
    fake_root = tmp_path / "repo"
    (fake_root / "dashboard").mkdir(parents=True)
    (fake_root / "data/tickets").mkdir(parents=True)

    (fake_root / "index.html").write_text((ROOT / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_root / "dashboard/index.html").write_text((ROOT / "dashboard/index.html").read_text(encoding="utf-8"), encoding="utf-8")
    (fake_root / "cookies.json").write_text('[{"name": "session", "value": "PRIVATE_COOKIE_999"}]', encoding="utf-8")
    (fake_root / ".env").write_text("GEMINI_API_KEY=PRIVATE_GEMINI_KEY_999\n", encoding="utf-8")
    (fake_root / "data/budgets.json").write_text(
        json.dumps({"monthly": {"Bebidas": 30}, "alert_monthly_total": 250}),
        encoding="utf-8",
    )
    (fake_root / "data/tickets/secret_ticket_12345.json").write_text(
        json.dumps({
            "id": "secret_ticket_12345",
            "ticket_code": "PRIVATE-CODE-777",
            "store": "Consum Calle Privada 88",
            "store_address": "Calle Privada 88, 46000 Valencia",
            "date": "2026-03-15",
            "total": 8.00,
            "source": "dom_extracted",
            "payment_method": "Tarjeta",
            "items": [
                {"name": "AGUA CONSUM 1,5L", "quantity": 2, "total": 10.00, "category": "Bebidas"}
            ],
        }),
        encoding="utf-8",
    )

    out_dir = tmp_path / "dist_site"
    assert export_public_site.main(["--root", str(fake_root), "--out", str(out_dir)]) == 0

    exported_files = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file())
    assert exported_files == [
        "dashboard/index.html",
        "data/budgets.json",
        "data/summary.json",
        "index.html",
    ]

    summary_raw = (out_dir / "data/summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_raw)
    assert summary["anonymized"] is True
    assert summary["recent_tickets"][0]["store"] == "Consum #1"
    assert "id" not in summary["data_quality"]["issues"][0]

    for forbidden in (
        "secret_ticket_12345",
        "PRIVATE-CODE-777",
        "Calle Privada 88",
        "PRIVATE_COOKIE_999",
        "PRIVATE_GEMINI_KEY_999",
    ):
        assert forbidden not in summary_raw


def test_docs_index_redirects_to_dashboard_and_stale_docs_summary_is_removed():
    docs_html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "../dashboard/" in docs_html
    assert not (ROOT / "docs/data/summary.json").exists()
    assert not (ROOT / ".claude/settings.local.json").exists()
