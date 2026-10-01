import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.extract import (
    DEFAULT_CACHE_DIR,
    ManualReviewItem,
    RuleExtraction,
    _build_prompt,
    _cache_path,
    _call_llm,
    _normalise_clause,
    _parse_extraction,
    _source_digest,
    extract_pdf_pages,
    extract_rules,
)
from src.ingest import PDFPage


def test_extract_pdf_pages_preserves_page_numbers_and_text(tmp_path: Path) -> None:
    first_page = Mock()
    first_page.extract_text.return_value = "First page text"
    second_page = Mock()
    second_page.extract_text.return_value = None
    pdf_reader = Mock(return_value=SimpleNamespace(pages=[first_page, second_page]))
    pdf_path = tmp_path / "source.pdf"

    with patch("pypdf.PdfReader", pdf_reader):
        pages = extract_pdf_pages(pdf_path)

    assert pages == [(1, "First page text"), (2, "")]
    pdf_reader.assert_called_once_with(str(pdf_path))


def test_normalise_clause_collapses_whitespace() -> None:
    assert _normalise_clause("  Exact\n quoted\tclause ") == "Exact quoted clause"


def test_source_digest_changes_when_source_or_document_changes() -> None:
    pages = [PDFPage(page_number=1, text="Eligibility clause.")]

    assert _source_digest(pages, "scheme.pdf") == _source_digest(pages, "scheme.pdf")
    assert _source_digest(pages, "scheme.pdf") != _source_digest(pages, "other.pdf")
    assert _source_digest(pages, "scheme.pdf") != _source_digest(
        [PDFPage(page_number=1, text="Updated clause.")], "scheme.pdf"
    )


def test_cache_path_uses_scheme_id_under_cache_directory(tmp_path: Path) -> None:
    assert _cache_path("scheme_01", tmp_path) == tmp_path / "scheme_01.rules.json"


def test_default_rule_cache_is_outside_the_repository() -> None:
    assert DEFAULT_CACHE_DIR == (
        Path(tempfile.gettempdir()) / "schemescout" / "rule-cache"
    )


def test_cache_path_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="scheme_id"):
        _cache_path("../outside", tmp_path)


def test_build_prompt_names_profile_fields_and_source_page() -> None:
    prompt = _build_prompt(
        [PDFPage(page_number=3, text="Eligible entities are startups.")],
        "scheme.pdf",
    )

    assert "incorporation_year" in prompt
    assert '"page_number": 3' in prompt
    assert "scheme.pdf" in prompt


def test_call_llm_sends_openai_compatible_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMESCOUT_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("SCHEMESCOUT_LLM_MODEL", "test-model")
    monkeypatch.setenv("SCHEMESCOUT_LLM_API_KEY", "test-token")
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(
        {"choices": [{"message": {"content": '{"rules": [], "manual_review": []}'}}]}
    ).encode()
    urlopen = Mock(return_value=response)

    with patch("src.extract.urllib.request.urlopen", urlopen):
        result = _call_llm("extract")

    request = urlopen.call_args.args[0]
    assert request.full_url == "https://llm.example/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert json.loads(request.data)["model"] == "test-model"
    assert result == '{"rules": [], "manual_review": []}'


def test_call_llm_requests_json_object_when_json_mode_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEMESCOUT_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("SCHEMESCOUT_LLM_MODEL", "test-model")
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(
        {"choices": [{"message": {"content": '{"fields": []}'}}]}
    ).encode()
    urlopen = Mock(return_value=response)

    with patch("src.extract.urllib.request.urlopen", urlopen):
        _call_llm("extract fields", json_mode=True)

    request_body = json.loads(urlopen.call_args.args[0].data)
    assert request_body["response_format"] == {"type": "json_object"}


def test_call_llm_requires_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHEMESCOUT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SCHEMESCOUT_LLM_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="SCHEMESCOUT_LLM_BASE_URL"):
        _call_llm("extract")


def test_parse_extraction_keeps_only_source_verified_rules_and_manual_items(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pages = [PDFPage(page_number=1, text="Eligible entities are registered startups.")]
    response = {
        "rules": [
            {
                "id": "eligible-startup",
                "field": "entity_type",
                "operator": "equals",
                "value": "startup",
                "citations": [
                    {
                        "document": "wrong-name.pdf",
                        "page": 1,
                        "exact_clause": "Eligible entities are registered startups.",
                    }
                ],
            },
            {
                "id": "hallucinated-rule",
                "field": "entity_type",
                "operator": "equals",
                "value": "company",
                "citations": [
                    {
                        "document": "scheme.pdf",
                        "page": 1,
                        "exact_clause": "Applicants must have raised 10 crore.",
                    }
                ],
            },
            {
                "id": "unmappable",
                "field": "audience_type",
                "operator": "equals",
                "value": "rural",
                "citations": [
                    {
                        "document": "scheme.pdf",
                        "page": 1,
                        "exact_clause": "Eligible entities are registered startups.",
                    }
                ],
            },
        ],
        "manual_review": [
            {
                "condition": "Applicants must submit a detailed project report.",
                "citation": {
                    "document": "scheme.pdf",
                    "page": 1,
                    "exact_clause": "Eligible entities are registered startups.",
                },
            },
            {
                "condition": "Fabricated manual item.",
                "citation": {
                    "document": "scheme.pdf",
                    "page": 1,
                    "exact_clause": "This clause is not on the page.",
                },
            },
        ],
    }

    with caplog.at_level(logging.WARNING, logger="src.extract"):
        result = _parse_extraction(json.dumps(response), pages, "scheme.pdf")

    assert len(result.rules) == 1
    assert result.rules[0].citations[0].document == "scheme.pdf"
    assert [item.condition for item in result.manual_review] == [
        "Applicants must submit a detailed project report.",
        "Unmappable condition for field 'audience_type'",
    ]
    assert "Dropping rule 'hallucinated-rule'" in caplog.text
    assert "Dropping manual-review condition" in caplog.text


def test_parse_extraction_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        _parse_extraction("[]", [], "scheme.pdf")


def test_extract_rules_calls_llm_once_and_caches_verified_result(
    tmp_path: Path,
) -> None:
    pages = [PDFPage(page_number=1, text="Eligible entities are startups.")]
    llm_call = Mock(
        return_value=json.dumps(
            {
                "rules": [
                    {
                        "id": "eligible-startup",
                        "field": "entity_type",
                        "operator": "equals",
                        "value": "startup",
                        "citations": [
                            {
                                "document": "scheme.pdf",
                                "page": 1,
                                "exact_clause": "Eligible entities are startups.",
                            }
                        ],
                    },
                    {
                        "id": "fake",
                        "field": "entity_type",
                        "operator": "equals",
                        "value": "company",
                        "citations": [
                            {
                                "document": "scheme.pdf",
                                "page": 1,
                                "exact_clause": "Fabricated eligibility clause.",
                            }
                        ],
                    },
                ],
                "manual_review": [],
            }
        )
    )

    first = extract_rules(
        pages, "test-scheme", "scheme.pdf", cache_dir=tmp_path, llm_call=llm_call
    )
    second = extract_rules(
        pages,
        "test-scheme",
        "scheme.pdf",
        cache_dir=tmp_path,
        llm_call=Mock(side_effect=AssertionError("cache should prevent LLM call")),
    )

    assert first == second
    assert isinstance(first, RuleExtraction)
    assert len(first.rules) == 1
    assert (tmp_path / "test-scheme.rules.json").is_file()
    llm_call.assert_called_once()


def test_extract_rules_reextracts_when_document_pages_change(tmp_path: Path) -> None:
    llm_call = Mock(return_value='{"rules": [], "manual_review": []}')
    extract_rules(
        [PDFPage(page_number=1, text="First source.")],
        "test-scheme",
        "scheme.pdf",
        cache_dir=tmp_path,
        llm_call=llm_call,
    )
    extract_rules(
        [PDFPage(page_number=1, text="Updated source.")],
        "test-scheme",
        "scheme.pdf",
        cache_dir=tmp_path,
        llm_call=llm_call,
    )

    assert llm_call.call_count == 2


def test_extract_rules_rejects_empty_document_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="document"):
        extract_rules([], "scheme", " ", cache_dir=tmp_path, llm_call=Mock())


def test_manual_review_item_requires_a_citation() -> None:
    item = ManualReviewItem(
        condition="Submit a project report.",
        citation={
            "document": "scheme.pdf",
            "page": 1,
            "exact_clause": "Submit a project report.",
        },
    )

    assert item.citation.page == 1
