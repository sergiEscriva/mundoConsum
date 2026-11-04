"""Tests para scripts/notify_telegram.py: formato del resumen, omisión limpia y protección del token."""

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import notify_telegram


def _sample_summary():
    return {
        "recent_tickets": [
            {
                "date": "2026-04-12",
                "store": "Consum Avenida Valencia",
                "total": 42.50,
                "items_count": 14,
                "needs_review": False,
            }
        ],
        "budget_summary": {
            "month": "2026-04",
            "label": "abr 2026",
            "is_current_month": True,
            "categories": [
                {"category": "Carne y Pescado", "spent": 52.0, "limit": 60.0, "pct": 86.7, "status": "warn"},
                {"category": "Snacks y Dulces", "spent": 25.0, "limit": 20.0, "pct": 125.0, "status": "over"},
                {"category": "Bebidas", "spent": 10.0, "limit": 30.0, "pct": 33.3, "status": "ok"},
            ],
            "overall": {
                "spent": 120.0,
                "limit": 300.0,
                "pct": 40.0,
                "status": "ok",
                "burn_rate_daily": 10.0,
                "projected_month_end": 300.0,
                "days_elapsed": 12,
                "days_in_month": 30,
            },
        },
        "inflation": {
            "personal_ipc_pct": 2.45,
            "tracked_products": 8,
            "items": [
                {
                    "name": "ACEITE OLIVA CONSUM 1L",
                    "canonical_name": "Aceite Oliva Consum 1L",
                    "first_unit_price": 4.20,
                    "last_unit_price": 4.95,
                    "change_pct": 17.86,
                    "last_date": "2026-04-12",
                },
                {
                    "name": "LECHE ENTERA 1L",
                    "canonical_name": "Leche Entera 1L",
                    "first_unit_price": 1.00,
                    "last_unit_price": 0.90,
                    "change_pct": -10.0,
                    "last_date": "2026-04-12",
                },
            ],
        },
    }


def test_build_digest_includes_latest_ticket_budget_projection_warnings_and_price_hikes(tmp_path):
    tickets_dir = tmp_path / "tickets"
    tickets_dir.mkdir()
    (tickets_dir / "t1.json").write_text(json.dumps({
        "id": "1",
        "date": "2026-04-12",
        "items": [{"name": "ACEITE OLIVA CONSUM 1L", "quantity": 1, "total": 4.95}],
    }), encoding="utf-8")

    digest = notify_telegram.build_digest(_sample_summary(), tickets_dir=tickets_dir)
    assert "12/04/2026" in digest
    assert "Consum Avenida Valencia" in digest
    assert "42.50 €" in digest
    assert "120.00 € / 300.00 €" in digest
    assert "Proyección fin de mes" in digest
    assert "Carne y Pescado" in digest
    assert "Snacks y Dulces" in digest
    assert "Bebidas" not in digest  # < 80%
    assert "ACEITE OLIVA CONSUM 1L" in digest
    assert "+17.9%" in digest


def test_main_skips_gracefully_when_telegram_credentials_missing(monkeypatch, tmp_path):
    summary_file = tmp_path / "summary.json"
    summary_file.write_text(json.dumps(_sample_summary()), encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert notify_telegram.main(["--summary", str(summary_file)]) == 0


def test_send_telegram_message_never_leaks_bot_token_on_http_or_network_error(monkeypatch):
    secret_token = "999888777:SECRET_TELEGRAM_TOKEN_XYZ"

    opener_http = MagicMock()
    opener_http.open.side_effect = HTTPError(
        f"https://api.telegram.org/bot{secret_token}/sendMessage",
        401,
        "Unauthorized",
        hdrs=None,
        fp=io.BytesIO(b'{"ok":false}'),
    )
    monkeypatch.setattr(notify_telegram, "build_opener", Mock(return_value=opener_http))
    with pytest.raises(notify_telegram.TelegramNotifyError) as exc_info:
        notify_telegram.send_telegram_message("Hola", bot_token=secret_token, chat_id="12345")
    assert secret_token not in str(exc_info.value)
    assert "HTTP 401" in str(exc_info.value)

    opener_net = MagicMock()
    opener_net.open.side_effect = URLError(f"failed to reach https://api.telegram.org/bot{secret_token}/sendMessage")
    monkeypatch.setattr(notify_telegram, "build_opener", Mock(return_value=opener_net))
    with pytest.raises(notify_telegram.TelegramNotifyError) as net_exc:
        notify_telegram.send_telegram_message("Hola", bot_token=secret_token, chat_id="12345")
    assert secret_token not in str(net_exc.value)
