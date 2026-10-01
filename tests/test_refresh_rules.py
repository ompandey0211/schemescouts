import json
from pathlib import Path
from unittest.mock import Mock

from src.extract import RuleExtraction
from src.models import Citation, Rule
from scripts.refresh_rules import (
    RuleChanges,
    _cache_path,
    _cached_extraction,
    compare_rules,
    refresh_all_rules,
)


def _rule(
    rule_id: str,
    *,
    value: str = "agri-tech",
    clause: str = "Startups in agri-tech are eligible.",
) -> Rule:
    return Rule(
        id=rule_id,
        field="sector",
        operator="equals",
        value=value,
        citations=[Citation(document="scheme.pdf", page=1, exact_clause=clause)],
    )


def test_compare_rules_ignores_rule_and_citation_order() -> None:
    first = _rule("first")
    second = _rule("second")
    reordered_second = second.model_copy(
        update={"citations": list(reversed(second.citations))}
    )

    assert compare_rules(
        RuleExtraction(rules=[first, second]),
        RuleExtraction(rules=[reordered_second, first]),
    ) == RuleChanges()


def test_compare_rules_reports_added_removed_and_modified_ids() -> None:
    previous = RuleExtraction(
        rules=[_rule("removed"), _rule("modified"), _rule("stable")]
    )
    refreshed = RuleExtraction(
        rules=[
            _rule("added"),
            _rule("modified", value="fintech"),
            _rule("stable"),
        ]
    )

    assert compare_rules(previous, refreshed) == RuleChanges(
        added=("added",),
        removed=("removed",),
        modified=("modified",),
    )


def test_cached_extraction_loads_prior_output_or_empty(tmp_path: Path) -> None:
    cache_path = _cache_path(tmp_path, "scheme")

    assert _cached_extraction(cache_path) == RuleExtraction()
    cache_path.write_text(
        json.dumps({"source_digest": "old", "extraction": {"rules": [_rule("a").model_dump(mode="json")]}}),
        encoding="utf-8",
    )

    assert _cached_extraction(cache_path).rules == [_rule("a")]


def test_compare_rules_reports_unchanged_outputs() -> None:
    assert compare_rules(
        RuleExtraction(rules=[_rule("stable")]),
        RuleExtraction(rules=[_rule("stable")]),
    ) == RuleChanges()


def test_refresh_all_rules_forces_extraction_and_reports_changes(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from src.models import Scheme

    catalog_scheme = Scheme(
        id="scheme",
        name="Scheme",
        file_path="scheme.pdf",
    )
    (tmp_path / "scheme.rules.json").write_text(
        json.dumps(
            {
                "source_digest": "old",
                "extraction": {"rules": [_rule("old").model_dump(mode="json")]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.refresh_rules.load_schemes", lambda _directory: [catalog_scheme])
    monkeypatch.setattr("scripts.refresh_rules.scheme_pdf_path", lambda _scheme, _directory: tmp_path / "scheme.pdf")
    monkeypatch.setattr("scripts.refresh_rules.load_pdf_pages", lambda _path: [])
    extract_call = Mock(return_value=RuleExtraction(rules=[_rule("new")]))
    monkeypatch.setattr("scripts.refresh_rules.extract_rules", extract_call)

    failure_count = refresh_all_rules(tmp_path)

    assert failure_count == 0
    assert extract_call.call_args.kwargs["force_refresh"] is True
    output = capsys.readouterr().out
    assert "scheme: rules changed" in output
    assert "added: new" in output


def test_refresh_all_rules_reports_unchanged_rules(tmp_path: Path, monkeypatch, capsys) -> None:
    from src.models import Scheme

    scheme = Scheme(id="scheme", name="Scheme", file_path="scheme.pdf")
    prior = RuleExtraction(rules=[_rule("stable")])
    (tmp_path / "scheme.rules.json").write_text(
        json.dumps(
            {
                "source_digest": "old",
                "extraction": prior.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.refresh_rules.load_schemes", lambda _directory: [scheme])
    monkeypatch.setattr(
        "scripts.refresh_rules.scheme_pdf_path",
        lambda _scheme, _directory: tmp_path / "scheme.pdf",
    )
    monkeypatch.setattr("scripts.refresh_rules.load_pdf_pages", lambda _path: [])
    monkeypatch.setattr("scripts.refresh_rules.extract_rules", lambda *args, **kwargs: prior)

    assert refresh_all_rules(tmp_path) == 0
    assert capsys.readouterr().out.strip() == "scheme: no rule changes"
