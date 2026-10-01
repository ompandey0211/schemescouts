from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.evaluate import evaluate, evaluate_scheme
from src.ingest import load_profiles
from src.models import Citation, Profile, Rule, RuleResult, Scheme
from src.plan import (
    ChecklistItem,
    RequirementGap,
    _citations_for_rule,
    _phrase_action,
    _requirement_description,
    _step_priority,
    _unknown_next_step,
    build_action_plan,
    build_checklist,
    find_gaps,
    make_plan,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SAMPLE_PROFILES = load_profiles(DATA_DIR / "profiles")
TEST_CITATION = Citation(
    document="official scheme.pdf",
    page=2,
    exact_clause="Applicants must hold DPIIT recognition.",
)


def _rule(
    rule_id: str,
    field: str,
    operator: str = "equals",
    value: object = True,
) -> Rule:
    return Rule(
        id=rule_id,
        field=field,
        operator=operator,
        value=value,
        citations=[TEST_CITATION],
    )


def test_requirement_description_prefers_exact_cited_clause() -> None:
    result = RuleResult(
        rule_id="dpiit",
        status="not_met",
        reason="Rule not met.",
        citations=[TEST_CITATION],
    )

    assert _requirement_description(result) == TEST_CITATION.exact_clause


def test_requirement_description_falls_back_to_evaluation_reason() -> None:
    result = RuleResult(rule_id="unknown", status="unknown", reason="No citation.")

    assert _requirement_description(result) == "No citation."


def test_unknown_next_step_describes_missing_profile_data() -> None:
    result = RuleResult(
        rule_id="turnover",
        status="unknown",
        reason="Profile value for turnover is missing.",
    )

    assert "Greenfield Labs" in _unknown_next_step(result, "Greenfield Labs")
    assert "turnover" in _unknown_next_step(result, "Greenfield Labs")


def test_unknown_next_step_describes_other_uncertainty() -> None:
    result = RuleResult(
        rule_id="source",
        status="unknown",
        reason="Rule has no source citation.",
    )

    assert "official source" in _unknown_next_step(result, "Applicant")


def test_find_gaps_returns_unmet_and_unknown_sorted_by_blocking_priority() -> None:
    results = [
        RuleResult(
            rule_id="z-unknown",
            status="unknown",
            reason="Profile value is missing.",
            citations=[TEST_CITATION],
        ),
        RuleResult(
            rule_id="b-unmet",
            status="not_met",
            reason="Rule failed.",
            action_if_unmet="Apply for recognition.",
            citations=[TEST_CITATION],
        ),
        RuleResult(rule_id="a-met", status="met", reason="Passed."),
        RuleResult(
            rule_id="a-unknown",
            status="unknown",
            reason="No source citation.",
        ),
    ]

    gaps = find_gaps(results, "Greenfield Labs")

    assert [gap.rule_id for gap in gaps] == [
        "b-unmet",
        "a-unknown",
        "z-unknown",
    ]
    assert all(isinstance(gap, RequirementGap) for gap in gaps)
    assert gaps[0].blocking_priority == 1
    assert gaps[1].blocking_priority == 2
    assert "Greenfield Labs" in gaps[2].next_step


def test_citations_for_rule_prefers_result_and_deduplicates() -> None:
    fallback = Citation(document="fallback.pdf", page=1, exact_clause="fallback")
    results = {
        "rule": RuleResult(
            rule_id="rule",
            status="not_met",
            reason="Failed.",
            citations=[TEST_CITATION, TEST_CITATION],
        )
    }

    citations = _citations_for_rule(
        "rule", results, [fallback, fallback]
    )

    assert citations == [TEST_CITATION]


def test_citations_for_rule_uses_rule_citations_when_result_missing() -> None:
    assert _citations_for_rule("missing", {}, [TEST_CITATION]) == [TEST_CITATION]


@pytest.mark.parametrize(
    ("field", "expected_document", "expected_source"),
    [
        ("dpiit_recognized", "DPIIT recognition certificate", "Startup India portal"),
        (
            "entity_type",
            "Certificate of incorporation or registration",
            "Ministry of Corporate Affairs",
        ),
        ("annual_turnover_inr", "Audited financial statements", "chartered accountant"),
        ("funding_raised_inr", "Funding and investment records", "investors"),
        ("sector", "Pitch deck or product/service brief", "Prepare it"),
        ("women_led", "Shareholding and leadership records", "cap table"),
        ("state", "Registered-office or operations proof", "lease"),
    ],
)
def test_build_checklist_maps_profile_rules_to_documents(
    field: str, expected_document: str, expected_source: str
) -> None:
    scheme = Scheme(id="docs", name="Documents", rules=[_rule(field, field)])
    results = evaluate(SAMPLE_PROFILES[0], scheme)

    checklist = build_checklist(scheme, results)

    assert len(checklist) == 1
    assert isinstance(checklist[0], ChecklistItem)
    assert checklist[0].document == expected_document
    assert expected_source in checklist[0].where_to_obtain
    assert checklist[0].required_by == [field]
    assert checklist[0].citations == [TEST_CITATION]


def test_build_checklist_deduplicates_documents_and_merges_rules() -> None:
    scheme = Scheme(
        id="incorporation",
        name="Incorporation",
        rules=[
            _rule("type", "entity_type", value="private_limited"),
            _rule("year", "incorporation_year", operator="greater_than", value=2015),
        ],
    )
    results = evaluate(SAMPLE_PROFILES[0], scheme)

    checklist = build_checklist(scheme, results)

    assert len(checklist) == 1
    assert checklist[0].required_by == ["type", "year"]


def test_build_checklist_skips_uncited_or_unmapped_requirements() -> None:
    scheme = Scheme(
        id="incomplete",
        name="Incomplete",
        rules=[
            Rule(
                id="uncited",
                field="sector",
                operator="equals",
                value="agri-tech",
            ),
            _rule("not-mapped", "name"),
        ],
    )
    results = evaluate(SAMPLE_PROFILES[0], scheme)

    assert build_checklist(scheme, results) == []


def test_phrase_action_uses_deterministic_text_when_no_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHEMESCOUT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SCHEMESCOUT_LLM_MODEL", raising=False)

    assert _phrase_action("Review certificate.", None) == "Review certificate."


def test_phrase_action_uses_llm_only_to_rephrase() -> None:
    llm_call = Mock(return_value="Please review the certificate.")

    phrased = _phrase_action("Review certificate.", llm_call)

    assert phrased == "Please review the certificate."
    assert "Do not add facts" in llm_call.call_args.args[0]


def test_phrase_action_rejects_empty_llm_response() -> None:
    with pytest.raises(ValueError, match="empty action phrase"):
        _phrase_action("Review certificate.", Mock(return_value=" "))


@pytest.mark.parametrize(
    ("blocking_priority", "expected"),
    [(1, "high"), (2, "medium")],
)
def test_step_priority_maps_gap_blocking_level(
    blocking_priority: int, expected: str
) -> None:
    assert _step_priority(blocking_priority) == expected


@pytest.mark.parametrize("profile", SAMPLE_PROFILES)
def test_make_plan_dates_and_cites_each_step_for_sample_profiles(
    profile: Profile,
) -> None:
    scheme = Scheme(
        id=f"plan-{profile.id}",
        name="Application",
        rules=[
            _rule("dpiit", "dpiit_recognized", value=True),
            _rule("sector", "sector", value="different-sector"),
        ],
    )
    results = evaluate(profile, scheme)

    plan = make_plan(profile, scheme, results, start_date=date(2026, 10, 1))

    assert len(plan) == 2
    assert [step.date for step in plan] == [date(2026, 10, 1), date(2026, 10, 2)]
    assert [step.priority for step in plan] == ["high", "medium"]
    assert [step.rule_id for step in plan] == ["sector", "dpiit"]
    for step in plan:
        assert step.rule_id in step.action
        assert TEST_CITATION.exact_clause in step.action
        assert step.citations == [TEST_CITATION]
        assert profile.name in step.action


def test_make_plan_unknown_gap_uses_medium_priority() -> None:
    profile = SAMPLE_PROFILES[2]
    rule = _rule("turnover", "annual_turnover_inr", "greater_than", 0)
    scheme = Scheme(id="unknown", name="Unknown", rules=[rule])
    results = evaluate(profile, scheme)

    plan = make_plan(profile, scheme, results, start_date=date(2026, 10, 1))

    assert plan[0].priority == "medium"
    assert plan[0].rule_id == "turnover"
    assert TEST_CITATION.exact_clause in plan[0].action


def test_make_plan_calls_llm_to_phrase_but_keeps_rule_citation() -> None:
    profile = SAMPLE_PROFILES[0]
    scheme = Scheme(
        id="phrase",
        name="Phrase",
        rules=[_rule("dpiit", "dpiit_recognized", value=True)],
    )
    results = [
        RuleResult(
            rule_id="dpiit",
            status="not_met",
            reason="Recognition is missing.",
            action_if_unmet="Apply through Startup India.",
            citations=[TEST_CITATION],
        )
    ]
    llm_call = Mock(return_value="Start the recognition application.")

    plan = make_plan(
        profile,
        scheme,
        results,
        start_date=date(2026, 10, 1),
        llm_call=llm_call,
    )

    assert plan[0].action.startswith("Start the recognition application.")
    assert "[Rule dpiit;" in plan[0].action
    assert TEST_CITATION.exact_clause in plan[0].action
    llm_call.assert_called_once()


def test_make_plan_skips_results_for_rules_not_in_scheme(
    caplog: pytest.LogCaptureFixture,
) -> None:
    profile = SAMPLE_PROFILES[0]
    result = RuleResult(
        rule_id="orphan",
        status="unknown",
        reason="Missing information.",
        citations=[TEST_CITATION],
    )

    assert make_plan(profile, Scheme(id="empty", name="Empty"), [result]) == []
    assert "not present in scheme" in caplog.text


@pytest.mark.parametrize(
    ("status", "profile", "rules", "expected_action"),
    [
        (
            "eligible",
            SAMPLE_PROFILES[0],
            [_rule("pass", "sector", value="agri-tech")],
            "Review the source citations",
        ),
        (
            "ineligible",
            SAMPLE_PROFILES[0],
            [_rule("fail", "sector", value="fintech")],
            "Review the cited rules",
        ),
        (
            "unknown",
            SAMPLE_PROFILES[2],
            [_rule("unknown", "annual_turnover_inr", "greater_than", 0)],
            "Collect missing profile information",
        ),
    ],
)
def test_build_action_plan_matches_evaluation_status(
    status: str,
    profile: Profile,
    rules: list[Rule],
    expected_action: str,
) -> None:
    evaluation = evaluate_scheme(profile, Scheme(id=status, name=status, rules=rules))

    actions = build_action_plan(evaluation)

    assert evaluation.status == status
    assert expected_action in actions[0]
