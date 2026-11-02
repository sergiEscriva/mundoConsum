"""Gemini con respuestas simuladas: lotes, validación, caché y privacidad."""

from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, Mock
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gemini_categories as gemini


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(gemini, "available_models", Mock(return_value=["gemini-2.5-flash"]))
    (tmp_path / "data").mkdir()
    (tmp_path / "data/categories.json").write_text(json.dumps({"Bebidas": ["AGUA"], "Hogar y Menaje": ["JARRA"]}))
    return tmp_path


def ticket(names):
    return {"id": "private-ticket", "date": "2026-10-01", "store": "private-store", "total": 10,
            "source": "dom_extracted", "items": [
                {"name": name, "quantity": 1, "total": 1, "category": "Otros"} for name in names
            ]}


def test_all_unique_products_are_submitted_even_if_already_categorized(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    original = [ticket(["AGUA", "  agua ", "JARRA"])]
    original[0]["items"][0]["category"] = "Hogar y Menaje"
    before = deepcopy(original)

    def classify(names, categories, key, model):
        assert names == ["AGUA", "JARRA"]
        assert key == "synthetic-key"
        return ["Bebidas", "Hogar y Menaje"]

    request = Mock(side_effect=classify)
    monkeypatch.setattr(gemini, "request_batch", request)
    enriched, report = gemini.classify_tickets(original, root=catalog, strict=True)
    assert [i["category"] for i in enriched[0]["items"]] == ["Bebidas", "Bebidas", "Hogar y Menaje"]
    assert original == before
    assert report["classified_products"] == 2 and report["pending_products"] == 0
    assert "synthetic-key" not in (catalog / "data/product_categories.json").read_text()
    gemini.classify_tickets(original, root=catalog, strict=True)
    assert request.call_count == 1
    monkeypatch.delenv("GEMINI_API_KEY")
    cached, report = gemini.classify_tickets(original, root=catalog)
    assert cached[0]["items"][0]["category"] == "Bebidas"
    assert report["pending_products"] == 0
    assert request.call_count == 1


def test_cache_progress_survives_quota_error_and_next_run_resumes(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "BATCH_SIZE", 2)
    request = Mock(side_effect=[["Bebidas", "Bebidas"], gemini.GeminiError("Cuota agotada")])
    monkeypatch.setattr(gemini, "request_batch", request)
    enriched, report = gemini.classify_tickets([ticket(["A", "B", "C"])], root=catalog)
    assert report["classified_products"] == 2 and report["pending_products"] == 1
    assert report["error"] == "Cuota agotada"
    assert enriched[0]["items"][2]["category"] == "Otros"
    request = Mock(return_value=["Hogar y Menaje"])
    monkeypatch.setattr(gemini, "request_batch", request)
    _, report = gemini.classify_tickets([ticket(["A", "B", "C"])], root=catalog, strict=True)
    assert request.call_args.args[0] == ["C"]
    assert report["pending_products"] == 0


def test_strict_mode_reports_failure_instead_of_claiming_complete_classification(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "request_batch", Mock(side_effect=gemini.GeminiError("Cuota agotada")))
    with pytest.raises(gemini.GeminiError, match="Cuota"):
        gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    assert not (catalog / "data/product_categories.json").exists()


def test_missing_key_keeps_local_categories_without_network(catalog, monkeypatch):
    request = Mock()
    monkeypatch.setattr(gemini, "request_batch", request)
    result, report = gemini.classify_tickets([ticket(["AGUA"])], root=catalog)
    assert result[0]["items"][0]["category"] == "Otros"
    assert not report["enabled"]
    assert report["provider"] == "rules"
    request.assert_not_called()
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY"):
        gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)


def test_explicit_refresh_requeries_previously_classified_products(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    request = Mock(return_value=["Bebidas"])
    monkeypatch.setattr(gemini, "request_batch", request)
    gemini.classify_tickets([ticket(["AGUA"])], root=catalog)
    gemini.classify_tickets([ticket(["AGUA"])], root=catalog, refresh=True, strict=True)
    assert request.call_count == 2


def test_summary_uses_gemini_categories_without_rewriting_ticket_files(catalog, monkeypatch):
    import build_data
    monkeypatch.chdir(catalog)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    (catalog / "data/tickets").mkdir()
    path = catalog / "data/tickets/synthetic.json"
    path.write_text(json.dumps(ticket(["AGUA", "JARRA"])))
    original = path.read_bytes()
    request = Mock(return_value=["Bebidas", "Hogar y Menaje"])
    monkeypatch.setattr(gemini, "request_batch", request)
    assert build_data.main(["--require-gemini"]) == 0
    summary = json.loads((catalog / "data/summary.json").read_text())
    assert {p["category"] for p in summary["products"]} == {"Bebidas", "Hogar y Menaje"}
    assert summary["classification"]["classified_products"] == 2
    assert path.read_bytes() == original
    monkeypatch.delenv("GEMINI_API_KEY")
    assert build_data.main([]) == 0
    assert request.call_count == 1


def test_failed_explicit_classification_preserves_existing_summary(catalog, monkeypatch):
    import build_data
    monkeypatch.chdir(catalog)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    (catalog / "data/tickets").mkdir()
    (catalog / "data/tickets/synthetic.json").write_text(json.dumps(ticket(["AGUA"])))
    summary = catalog / "data/summary.json"
    summary.write_text('{"previous": true}')
    monkeypatch.setattr(gemini, "request_batch", Mock(side_effect=gemini.GeminiError("Cuota agotada")))
    assert build_data.main(["--require-gemini"]) == 1
    assert summary.read_text() == '{"previous": true}'


def test_configuration_preserves_other_settings_and_never_prints_key(catalog, monkeypatch, capsys):
    (catalog / ".env").write_text('GEMINI_MODEL="gemini-2.5-flash"\nOTHER_SETTING=keep\n')
    monkeypatch.setattr(gemini.sys.stdin, "isatty", Mock(return_value=True))
    monkeypatch.setattr(gemini.getpass, "getpass", Mock(return_value="synthetic-secret"))
    gemini.configure(catalog)
    assert gemini.settings(catalog) == ("synthetic-secret", "gemini-2.5-flash")
    assert "OTHER_SETTING=keep" in (catalog / ".env").read_text()
    assert "synthetic-secret" not in capsys.readouterr().out
    assert not list(catalog.glob(".env.gemini-*.tmp"))


def response(results, finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {"parts": [
        {"text": json.dumps({"classifications": results})}
    ]}}]}


def mock_transport(monkeypatch, data):
    result = MagicMock()
    result.__enter__.return_value.read.return_value = json.dumps(data).encode()
    opener = Mock()
    opener.open.return_value = result
    monkeypatch.setattr(gemini, "build_opener", Mock(return_value=opener))
    return opener, result


def test_api_request_sends_only_product_names_and_key_in_header(monkeypatch):
    opener, _ = mock_transport(monkeypatch, response([{"id": 0, "category": "Bebidas"}]))
    assert gemini.request_batch(["AGUA"], ["Bebidas", "Otros"], "synthetic-secret", "gemini-2.5-flash") == ["Bebidas"]
    request = opener.open.call_args.args[0]
    assert request.get_header("X-goog-api-key") == "synthetic-secret"
    assert "synthetic-secret" not in request.full_url
    payload = json.loads(request.data)
    assert json.loads(payload["contents"][0]["parts"][0]["text"]) == {"products": [{"id": 0, "name": "AGUA"}]}
    assert "synthetic-secret" not in request.data.decode()
    assert payload["generationConfig"]["responseSchema"]["properties"]["classifications"]["items"]["properties"]["category"]["enum"] == ["Bebidas", "Otros"]


@pytest.mark.parametrize("results", [
    [], [{"id": 0, "category": "invented"}],
    [{"id": 0, "category": "Bebidas"}, {"id": 0, "category": "Bebidas"}],
    [{"id": True, "category": "Bebidas"}], [{"id": 99, "category": "Bebidas"}],
    [{"name": "AGUA", "category": "Bebidas"}],
])
def test_incomplete_duplicate_or_unknown_classifications_are_rejected(monkeypatch, results):
    mock_transport(monkeypatch, response(results))
    with pytest.raises(gemini.GeminiError, match="no válidas"):
        gemini.request_batch(["AGUA"], ["Bebidas"], "synthetic-secret", "gemini-2.5-flash")


def test_truncated_model_output_is_rejected(monkeypatch):
    mock_transport(monkeypatch, response([{"id": 0, "category": "Bebidas"}], finish="MAX_TOKENS"))
    with pytest.raises(gemini.GeminiError):
        gemini.request_batch(["AGUA"], ["Bebidas"], "synthetic-secret", "gemini-2.5-flash")


def test_quota_retry_is_bounded_and_does_not_expose_response_body(monkeypatch, caplog):
    opener, success = mock_transport(monkeypatch, response([{"id": 0, "category": "Bebidas"}]))
    failures = [HTTPError("https://example.invalid", 429, "quota", {}, io.BytesIO(b"synthetic-secret")) for _ in range(3)]
    opener.open.side_effect = failures
    pause = Mock()
    monkeypatch.setattr(gemini.time, "sleep", pause)
    with pytest.raises(gemini.GeminiError, match="Cuota") as error:
        gemini.request_batch(["AGUA"], ["Bebidas"], "synthetic-secret", "gemini-2.5-flash")
    assert opener.open.call_count == 3
    assert [call.args[0] for call in pause.call_args_list] == [2, 4]
    assert "synthetic-secret" not in str(error.value) + caplog.text


def test_unavailable_model_returns_actionable_error(monkeypatch):
    opener, _ = mock_transport(monkeypatch, {})
    opener.open.side_effect = HTTPError("https://example.invalid", 404, "missing", {}, io.BytesIO(b"synthetic-secret"))
    with pytest.raises(gemini.GeminiError, match="GEMINI_MODEL"):
        gemini.request_batch(["AGUA"], ["Bebidas"], "synthetic-secret", "not-a-model")


def test_default_model_is_detected_instead_of_pinned(catalog):
    assert gemini.settings(catalog) == ("", "auto")


def test_model_listing_follows_pages_and_excludes_non_generation_models(monkeypatch):
    payloads = [
        {"models": [{"name": "models/gemini-future-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]}],
         "nextPageToken": "next page"},
        {"models": [{"name": "models/gemini-future-pro", "supportedGenerationMethods": ["generateContent"]}]},
    ]
    requests = []

    def api(request, operation):
        requests.append(request)
        assert operation == "list"
        assert request.get_header("X-goog-api-key") == "synthetic-secret"
        assert "synthetic-secret" not in request.full_url
        return payloads.pop(0)

    monkeypatch.setattr(gemini, "api_json", api)
    assert gemini.available_models("synthetic-secret") == ["gemini-future-flash", "gemini-future-pro"]
    assert "pageToken=next+page" in requests[1].full_url


def test_model_selection_prefers_stable_text_flash_and_reuses_cache_model():
    models = ["gemini-9.0-pro", "gemini-8.0-flash-preview", "gemini-7.0-flash",
              "gemini-8.0-flash-image", "gemini-6.0-flash"]
    assert gemini.model_candidates(models)[0] == "gemini-7.0-flash"
    assert "gemini-8.0-flash-image" not in gemini.model_candidates(models)
    assert gemini.model_candidates(models, preferred="gemini-6.0-flash")[0] == "gemini-6.0-flash"


def test_missing_fixed_default_is_replaced_by_model_available_to_account(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "available_models", Mock(return_value=["gemini-future-flash"]))
    request = Mock(return_value=["Bebidas"])
    monkeypatch.setattr(gemini, "request_batch", request)
    _, report = gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    assert request.call_args.args[3] == "gemini-future-flash"
    assert report["model"] == "gemini-future-flash"
    assert json.loads((catalog / "data/product_categories.json").read_text())["model"] == "gemini-future-flash"


def test_auto_selection_tries_alternative_after_generation_returns_404(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "available_models", Mock(return_value=["gemini-8.0-flash", "gemini-7.0-flash"]))
    request = Mock(side_effect=[gemini.ModelUnavailable("unavailable"), ["Bebidas"]])
    monkeypatch.setattr(gemini, "request_batch", request)
    _, report = gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    assert [call.args[3] for call in request.call_args_list] == ["gemini-8.0-flash", "gemini-7.0-flash"]
    assert report["model"] == "gemini-7.0-flash"


def test_explicit_model_is_not_silently_replaced(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-private-model")
    request = Mock(side_effect=gemini.ModelUnavailable("Use GEMINI_MODEL=auto"))
    monkeypatch.setattr(gemini, "request_batch", request)
    with pytest.raises(gemini.ModelUnavailable):
        gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    gemini.available_models.assert_not_called()
    assert request.call_count == 1


def test_auto_cache_is_reused_offline_without_listing_models(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "request_batch", Mock(return_value=["Bebidas"]))
    gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    gemini.available_models.reset_mock()
    monkeypatch.delenv("GEMINI_API_KEY")
    _, report = gemini.classify_tickets([ticket(["AGUA"])], root=catalog)
    assert report["classified_products"] == 1
    assert report["model"] == "gemini-2.5-flash"
    gemini.available_models.assert_not_called()


def test_changing_auto_model_does_not_relabel_old_cache_entries(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "request_batch", Mock(return_value=["Bebidas"]))
    gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    monkeypatch.setattr(gemini, "available_models", Mock(return_value=["gemini-future-flash"]))
    request = Mock(return_value=["Bebidas", "Hogar y Menaje"])
    monkeypatch.setattr(gemini, "request_batch", request)
    _, report = gemini.classify_tickets([ticket(["AGUA", "JARRA"])], root=catalog, strict=True)
    assert request.call_args.args[0] == ["AGUA", "JARRA"]
    assert report["model"] == "gemini-future-flash"


def test_catalog_errors_are_not_reported_as_unknown_model(monkeypatch):
    opener, _ = mock_transport(monkeypatch, {})
    opener.open.side_effect = HTTPError("https://example.invalid", 404, "missing", {}, io.BytesIO(b"synthetic-secret"))
    with pytest.raises(gemini.GeminiError, match="catálogo") as error:
        gemini.available_models("synthetic-secret")
    assert not isinstance(error.value, gemini.ModelUnavailable)


def test_invalid_api_key_reason_is_reported_without_exposing_body(monkeypatch):
    opener, _ = mock_transport(monkeypatch, {})
    body = json.dumps({"error": {"message": "synthetic-secret", "details": [{"reason": "API_KEY_INVALID"}]}}).encode()
    opener.open.side_effect = HTTPError("https://example.invalid", 400, "bad key", {}, io.BytesIO(body))
    with pytest.raises(gemini.GeminiError, match="clave Gemini") as error:
        gemini.available_models("synthetic-secret")
    assert "synthetic-secret" not in str(error.value)


def test_service_503_uses_longer_bounded_backoff(monkeypatch):
    opener, _ = mock_transport(monkeypatch, {})
    opener.open.side_effect = [HTTPError("https://example.invalid", 503, "busy", {}, io.BytesIO()) for _ in range(3)]
    pause = Mock()
    monkeypatch.setattr(gemini.time, "sleep", pause)
    monkeypatch.setattr(gemini.random, "uniform", Mock(return_value=0))
    with pytest.raises(gemini.ServiceUnavailable, match="HTTP 503"):
        gemini.request_batch(["AGUA"], ["Bebidas"], "synthetic-secret", "gemini-test-flash")
    assert opener.open.call_count == 3
    assert [call.args[0] for call in pause.call_args_list] == [5, 10]


def test_retry_after_is_respected_before_retrying(monkeypatch):
    opener, success = mock_transport(monkeypatch, response([{"id": 0, "category": "Bebidas"}]))
    opener.open.side_effect = [HTTPError("https://example.invalid", 503, "busy", {"Retry-After": "12"}, io.BytesIO()), success]
    pause = Mock()
    monkeypatch.setattr(gemini.time, "sleep", pause)
    assert gemini.request_batch(["AGUA"], ["Bebidas"], "synthetic-secret", "gemini-test-flash") == ["Bebidas"]
    pause.assert_called_once_with(12)


def test_retry_after_http_date_and_invalid_values():
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    assert gemini.retry_after_seconds("Mon, 02 Nov 2026 12:00:20 GMT", now=now) == 20
    assert gemini.retry_after_seconds("invalid", now=now) is None
    assert gemini.retry_after_seconds("inf", now=now) is None


def test_long_retry_after_stops_instead_of_retrying_early(monkeypatch):
    opener, _ = mock_transport(monkeypatch, {})
    opener.open.side_effect = HTTPError("https://example.invalid", 503, "busy", {"Retry-After": "120"}, io.BytesIO())
    pause = Mock()
    monkeypatch.setattr(gemini.time, "sleep", pause)
    with pytest.raises(gemini.GeminiError, match="120 segundos") as error:
        gemini.request_batch(["AGUA"], ["Bebidas"], "synthetic-secret", "gemini-test-flash")
    assert not isinstance(error.value, gemini.ServiceUnavailable)
    assert opener.open.call_count == 1
    pause.assert_not_called()


def test_auto_tries_next_model_when_first_remains_overloaded(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "available_models", Mock(return_value=["gemini-8.0-flash", "gemini-7.0-flash"]))
    request = Mock(side_effect=[gemini.ServiceUnavailable("HTTP 503", retry_after=7), ["Bebidas"]])
    monkeypatch.setattr(gemini, "request_batch", request)
    pause = Mock()
    monkeypatch.setattr(gemini.time, "sleep", pause)
    _, report = gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    assert [call.args[3] for call in request.call_args_list] == ["gemini-8.0-flash", "gemini-7.0-flash"]
    assert report["model"] == "gemini-7.0-flash"
    pause.assert_called_once_with(7)


def test_later_service_failure_preserves_batches_and_does_not_mix_models(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "BATCH_SIZE", 1)
    monkeypatch.setattr(gemini, "available_models", Mock(return_value=["gemini-8.0-flash", "gemini-7.0-flash"]))
    request = Mock(side_effect=[["Bebidas"], gemini.ServiceUnavailable("HTTP 503")])
    monkeypatch.setattr(gemini, "request_batch", request)
    with pytest.raises(gemini.ServiceUnavailable):
        gemini.classify_tickets([ticket(["A", "B"])], root=catalog, strict=True)
    cache = json.loads((catalog / "data/product_categories.json").read_text())
    assert cache["model"] == "gemini-8.0-flash"
    assert cache["products"] == {"A": "Bebidas"}
    assert [call.args[3] for call in request.call_args_list] == ["gemini-8.0-flash"] * 2
    request = Mock(return_value=["Bebidas"])
    monkeypatch.setattr(gemini, "request_batch", request)
    _, report = gemini.classify_tickets([ticket(["A", "B"])], root=catalog, strict=True)
    assert request.call_args.args[0] == ["B"]
    assert report["classified_products"] == 2


def test_all_models_overloaded_remains_a_service_error(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(gemini, "available_models", Mock(return_value=["gemini-8.0-flash", "gemini-7.0-flash"]))
    request = Mock(side_effect=gemini.ServiceUnavailable("HTTP 503"))
    monkeypatch.setattr(gemini, "request_batch", request)
    with pytest.raises(gemini.ServiceUnavailable, match="503"):
        gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    assert request.call_count == 2
    assert not (catalog / "data/product_categories.json").exists()


def test_explicit_model_is_not_replaced_on_service_error(catalog, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-private-model")
    request = Mock(side_effect=gemini.ServiceUnavailable("HTTP 503"))
    monkeypatch.setattr(gemini, "request_batch", request)
    with pytest.raises(gemini.ServiceUnavailable):
        gemini.classify_tickets([ticket(["AGUA"])], root=catalog, strict=True)
    assert request.call_count == 1
