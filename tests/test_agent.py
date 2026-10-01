import json
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.agent import (
    AgentEvent,
    AgentReport,
    MAX_ATTEMPTS,
    _matches_target,
    _record_event,
    _run_with_retries,
    build_checklist_tool,
    evaluate_profile_against_schemes,
    evaluate_tool,
    extract_rules_tool,
    find_gaps_tool,
    find_relevant_schemes_tool,
    load_profile_tool,
    make_plan_tool,
    run_agent,
)
from src.extract import extract_rules
from src.ingest import PDFPage, load_profiles, load_schemes
from src.models import Citation, Profile, Rule, RuleResult, Scheme
from src.plan import ChecklistItem, PlanStep, RequirementGap

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SAMPLE_PROFILES = load_profiles(DATA_DIR / "profiles")
TEST_CITATION = Citation(
    document="scheme.pdf",
    page=1,
    exact_clause="Startups in agri-tech are eligible.",
)
def _write_text_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    content = DecodedStreamObject()
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped_text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    with path.open("wb") as pdf_file:
        writer.write(pdf_file)


def test_record_event_logs_and_appends_structured_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[AgentEvent] = []

    with caplog.at_level(logging.INFO, logger="src.agent"):
        _record_event(events, "load_profile", "completed", "Loaded.")

    assert events[0].step == "load_profile"
    assert events[0].status == "completed"
    assert "agent step=load_profile" in caplog.text


@pytest.mark.parametrize("max_attempts", [0, 4])
def test_run_with_retries_rejects_attempts_over_cap(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _run_with_retries("test", lambda: "ok", [], max_attempts)


def test_run_with_retries_retries_then_returns_result() -> None:
    events: list[AgentEvent] = []
    operation = Mock(side_effect=[RuntimeError("transient"), "ok"])

    result = _run_with_retries("test", operation, events, max_attempts=3)

    assert result == "ok"
    assert operation.call_count == 2
    assert [event.status for event in events] == [
        "started",
        "retrying",
        "started",
        "completed",
    ]


def test_run_with_retries_stops_after_three_attempts() -> None:
    events: list[AgentEvent] = []
    operation = Mock(side_effect=RuntimeError("unavailable"))

    with pytest.raises(RuntimeError, match="unavailable"):
        _run_with_retries("test", operation, events, max_attempts=3)

    assert operation.call_count == 3
    assert events[-1].status == "failed"
    assert events[-1].attempt == 3


def test_load_profile_tool_loads_sample_profile(tmp_path: Path) -> None:
    profile = SAMPLE_PROFILES[0]
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(profile.model_dump_json(), encoding="utf-8")
    events: list[AgentEvent] = []

    loaded = load_profile_tool(profile.id, tmp_path, events=events)

    assert loaded == profile
    assert events[-1].step == "load_profile"


def test_load_profile_tool_rejects_unknown_profile(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="missing"):
        load_profile_tool("missing", tmp_path)


@pytest.mark.parametrize(
    ("profile_value", "targets", "expected"),
    [
        ("Agri-Tech", ["agri-tech"], True),
        ("Karnataka", ["Delhi"], False),
        (None, ["early"], False),
        (None, [], True),
    ],
)
def test_matches_target(
    profile_value: str | None, targets: list[str], expected: bool
) -> None:
    assert _matches_target(profile_value, targets) is expected


def test_find_relevant_schemes_tool_filters_sector_stage_and_state() -> None:
    profile = SAMPLE_PROFILES[0]
    schemes = [
        Scheme(
            id="match",
            name="Match",
            sectors=["AGRI-TECH"],
            stages=["growth"],
            states=["karnataka"],
        ),
        Scheme(id="wrong-sector", name="Wrong sector", sectors=["fintech"]),
        Scheme(id="wrong-stage", name="Wrong stage", stages=["early"]),
        Scheme(id="wrong-state", name="Wrong state", states=["Delhi"]),
        Scheme(id="open", name="Open"),
    ]
    events: list[AgentEvent] = []

    relevant = find_relevant_schemes_tool(profile, schemes, events=events)

    assert [scheme.id for scheme in relevant] == ["match", "open"]
    assert sum(event.status == "filtered" for event in events) == 3


def test_find_relevant_schemes_tool_excludes_targeted_schemes_if_profile_data_missing() -> None:
    profile = SAMPLE_PROFILES[2].model_copy(update={"stage": None})
    scheme = Scheme(id="stage-target", name="Targeted", stages=["early"])

    assert find_relevant_schemes_tool(profile, [scheme]) == []


def test_extract_rules_tool_reads_verified_rules_from_cache(tmp_path: Path) -> None:
    scheme = Scheme(id="cached", name="Cached")
    pdf_path = tmp_path / "cached.pdf"
    _write_text_pdf(pdf_path, TEST_CITATION.exact_clause)
    pages = [PDFPage(page_number=1, text=TEST_CITATION.exact_clause)]
    rule = Rule(
        id="sector",
        field="sector",
        operator="equals",
        value="agri-tech",
        citations=[
            Citation(
                document=pdf_path.name,
                page=1,
                exact_clause=TEST_CITATION.exact_clause,
            )
        ],
    )
    extract_rules(
        pages,
        "cached",
        pdf_path.name,
        cache_dir=tmp_path,
        llm_call=Mock(return_value=json.dumps({"rules": [rule.model_dump()], "manual_review": []})),
    )
    events: list[AgentEvent] = []

    extracted = extract_rules_tool(scheme, tmp_path, events=events)

    assert extracted.rules == [rule]
    assert events[-1].step == "extract_rules"


def test_evaluate_tool_returns_rule_results() -> None:
    profile = SAMPLE_PROFILES[0]
    scheme = Scheme(
        id="rule",
        name="Rule",
        rules=[
            Rule(
                id="sector",
                field="sector",
                operator="equals",
                value="agri-tech",
                citations=[TEST_CITATION],
            )
        ],
    )
    events: list[AgentEvent] = []

    results = evaluate_tool(profile, scheme, events=events)

    assert results[0].status == "met"
    assert events[-1].step == "evaluate"


def test_find_gaps_tool_returns_prioritized_gaps() -> None:
    results = [
        RuleResult(
            rule_id="missing",
            status="unknown",
            reason="Missing profile data.",
            citations=[TEST_CITATION],
        )
    ]
    events: list[AgentEvent] = []

    gaps = find_gaps_tool(results, events=events)

    assert isinstance(gaps[0], RequirementGap)
    assert events[-1].step == "find_gaps"


def test_build_checklist_tool_returns_documents() -> None:
    scheme = Scheme(
        id="checklist",
        name="Checklist",
        rules=[
            Rule(
                id="sector",
                field="sector",
                operator="equals",
                value="agri-tech",
                citations=[TEST_CITATION],
            )
        ],
    )
    events: list[AgentEvent] = []

    checklist = build_checklist_tool(scheme, [], events=events)

    assert isinstance(checklist[0], ChecklistItem)
    assert events[-1].step == "build_checklist"


def test_make_plan_tool_creates_dated_citation_linked_steps() -> None:
    profile = SAMPLE_PROFILES[0]
    scheme = Scheme(
        id="plan",
        name="Plan",
        rules=[
            Rule(
                id="sector",
                field="sector",
                operator="equals",
                value="fintech",
                citations=[TEST_CITATION],
            )
        ],
    )
    results = [
        RuleResult(
            rule_id="sector",
            status="not_met",
            reason="Not met.",
            action_if_unmet="Review eligibility.",
            citations=[TEST_CITATION],
        )
    ]
    events: list[AgentEvent] = []

    plan = make_plan_tool(profile, scheme, results, events=events)

    assert isinstance(plan[0], PlanStep)
    assert "Rule sector" in plan[0].action
    assert events[-1].step == "make_plan"


def test_compatibility_evaluator_returns_scheme_summaries() -> None:
    profile = SAMPLE_PROFILES[0]
    scheme = Scheme(
        id="compat",
        name="Compatibility",
        rules=[
            Rule(
                id="sector",
                field="sector",
                operator="equals",
                value="agri-tech",
                citations=[TEST_CITATION],
            )
        ],
    )

    result = evaluate_profile_against_schemes(profile, [scheme])

    assert result[0].status == "eligible"


def test_run_agent_end_to_end_uses_cached_scheme_rules(tmp_path: Path) -> None:
    profiles_dir = tmp_path / "profiles"
    schemes_dir = tmp_path / "schemes"
    profiles_dir.mkdir()
    schemes_dir.mkdir()
    profile = SAMPLE_PROFILES[0]
    (profiles_dir / "sample.json").write_text(
        profile.model_dump_json(), encoding="utf-8"
    )
    scheme = Scheme(
        id="demo-scheme",
        name="Demo Scheme",
        sectors=["agri-tech"],
        stages=["growth"],
        states=["Karnataka"],
    )
    (schemes_dir / "demo-scheme.json").write_text(
        scheme.model_dump_json(), encoding="utf-8"
    )
    pdf_path = schemes_dir / "demo-scheme.pdf"
    pdf_text = (
        "Startups in agri-tech are eligible. "
        "Applicants must hold DPIIT recognition."
    )
    _write_text_pdf(pdf_path, pdf_text)
    pages = [PDFPage(page_number=1, text=pdf_text)]
    rules = [
        Rule(
            id="sector",
            field="sector",
            operator="equals",
            value="agri-tech",
            citations=[
                Citation(
                    document=pdf_path.name,
                    page=1,
                    exact_clause=TEST_CITATION.exact_clause,
                )
            ],
        ),
        Rule(
            id="dpiit",
            field="dpiit_recognized",
            operator="equals",
            value=True,
            citations=[
                Citation(
                    document=pdf_path.name,
                    page=1,
                    exact_clause="Applicants must hold DPIIT recognition.",
                )
            ],
        ),
    ]
    extract_rules(
        pages,
        scheme.id,
        pdf_path.name,
        cache_dir=schemes_dir,
        llm_call=Mock(
            return_value=json.dumps(
                {"rules": [rule.model_dump() for rule in rules], "manual_review": []}
            )
        ),
    )

    report = run_agent(
        profile.id,
        profiles_dir=profiles_dir,
        schemes_dir=schemes_dir,
        llm_call=Mock(return_value="Rephrased action."),
    )

    assert isinstance(report, AgentReport)
    assert report.status == "completed"
    assert report.profile_name == profile.name
    assert len(report.schemes) == 1
    scheme_report = report.schemes[0]
    assert scheme_report.verdict == "needs more info"
    assert [result.status for result in scheme_report.rule_results] == [
        "met",
        "unknown",
    ]
    assert [gap.rule_id for gap in scheme_report.gaps] == ["dpiit"]
    assert "DPIIT recognition certificate" in [
        item.document for item in scheme_report.checklist
    ]
    assert len(scheme_report.plan) == 1
    assert "Rule dpiit" in scheme_report.plan[0].action
    assert all(
        event.status != "failed"
        for event in report.events
    )
    assert {
        "load_profile",
        "load_schemes",
        "find_relevant_schemes",
        "extract_rules",
        "evaluate",
        "find_gaps",
        "build_checklist",
        "make_plan",
    }.issubset({event.step for event in report.events})


def test_run_agent_reports_profile_failure_after_capped_retries(
    tmp_path: Path,
) -> None:
    report = run_agent(
        "missing",
        profiles_dir=tmp_path,
        schemes_dir=tmp_path,
        max_attempts=MAX_ATTEMPTS,
    )

    assert report.status == "failed"
    assert len(report.errors) == 1
    assert sum(event.status == "started" for event in report.events) == 3
    assert report.events[-1].status == "failed"
    assert report.events[-1].attempt == 3


def test_load_schemes_ignores_extraction_cache_files(tmp_path: Path) -> None:
    scheme = Scheme(id="scheme", name="Scheme")
    (tmp_path / "scheme.json").write_text(scheme.model_dump_json(), encoding="utf-8")
    (tmp_path / "scheme.rules.json").write_text("{}", encoding="utf-8")

    assert load_schemes(tmp_path) == [scheme]
