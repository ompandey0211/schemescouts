from pathlib import Path

import pytest

from src.evaluate import evaluate, evaluate_rule, evaluate_scheme, overall_verdict
from src.ingest import load_profiles
from src.models import Citation, Profile, Rule, RuleResult, Scheme

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SAMPLE_PROFILE = load_profiles(DATA_DIR / "profiles")[0]
TEST_CITATION = Citation(
    document="test source",
    page=1,
    exact_clause="test clause",
)


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        ("sector", "equals", "agri-tech"),
        ("sector", "not_equals", "health-tech"),
        ("annual_turnover_inr", "greater_than", 3_000_000),
        ("annual_turnover_inr", "greater_than_or_equal", 4_000_000),
        ("annual_turnover_inr", "less_than", 5_000_000),
        ("annual_turnover_inr", "less_than_or_equal", 4_000_000),
        ("state", "in", ["Karnataka", "Tamil Nadu"]),
        ("state", "not_in", ["Delhi"]),
        ("sector", "contains", "agri"),
    ],
)
def test_evaluate_rule_supported_operators(
    field: str,
    operator: str,
    value: object,
) -> None:
    rule = Rule(
        id="supported-rule",
        field=field,
        operator=operator,
        value=value,
        citations=[TEST_CITATION],
    )

    result = evaluate_rule(SAMPLE_PROFILE, rule)

    assert result.status == "met"
    assert result.citations == [TEST_CITATION]


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        ("sector", "equals", "fintech"),
        ("sector", "not_equals", "agri-tech"),
        ("annual_turnover_inr", "greater_than", 5_000_000),
        ("annual_turnover_inr", "greater_than_or_equal", 5_000_000),
        ("annual_turnover_inr", "less_than", 3_000_000),
        ("annual_turnover_inr", "less_than_or_equal", 3_000_000),
        ("state", "in", ["Delhi"]),
        ("state", "not_in", ["Karnataka"]),
        ("sector", "contains", "health"),
    ],
)
def test_evaluate_rule_marks_unsatisfied_operators_ineligible(
    field: str,
    operator: str,
    value: object,
) -> None:
    rule = Rule(
        id="unsatisfied-rule",
        field=field,
        operator=operator,
        value=value,
        citations=[TEST_CITATION],
    )

    result = evaluate_rule(SAMPLE_PROFILE, rule)

    assert result.status == "not_met"
    assert result.action_if_unmet


def test_evaluate_rule_returns_ineligible_for_a_cited_mismatch() -> None:
    rule = Rule(
        id="mismatch",
        field="sector",
        operator="equals",
        value="fintech",
        citations=[TEST_CITATION],
    )

    result = evaluate_rule(SAMPLE_PROFILE, rule)

    assert result.status == "not_met"
    assert result.citations == [TEST_CITATION]


def test_evaluate_rule_returns_unknown_for_missing_profile_data() -> None:
    profile = load_profiles(DATA_DIR / "profiles")[2]
    rule = Rule(
        id="missing-value",
        field="annual_turnover_inr",
        operator="greater_than",
        value=0,
        citations=[TEST_CITATION],
    )

    result = evaluate_rule(profile, rule)

    assert result.status == "unknown"
    assert "missing" in result.explanation


def test_evaluate_rule_returns_unknown_without_citation() -> None:
    rule = Rule(
        id="uncited",
        field="sector",
        operator="equals",
        value="agri-tech",
    )

    result = evaluate_rule(SAMPLE_PROFILE, rule)

    assert result.status == "unknown"
    assert result.citations == []


def test_evaluate_rule_returns_unknown_for_unsupported_profile_field() -> None:
    rule = Rule(
        id="unsupported",
        field="grant_amount",
        operator="equals",
        value=100,
        citations=[TEST_CITATION],
    )

    assert evaluate_rule(SAMPLE_PROFILE, rule).status == "unknown"


def test_evaluate_rule_returns_unknown_for_incompatible_values() -> None:
    rule = Rule(
        id="incompatible",
        field="annual_turnover_inr",
        operator="greater_than",
        value="not-a-number",
        citations=[TEST_CITATION],
    )

    assert evaluate_rule(SAMPLE_PROFILE, rule).status == "unknown"


def test_evaluate_rule_does_not_coerce_boolean_values() -> None:
    rule = Rule(
        id="boolean-number",
        field="women_led",
        operator="equals",
        value=1,
        citations=[TEST_CITATION],
    )

    assert evaluate_rule(SAMPLE_PROFILE, rule).status == "unknown"


def test_evaluate_rule_includes_women_led_remediation() -> None:
    profile = SAMPLE_PROFILE.model_copy(update={"women_led": False})
    rule = Rule(
        id="women-led",
        field="women_led",
        operator="equals",
        value=True,
        citations=[TEST_CITATION],
    )

    result = evaluate_rule(profile, rule)

    assert result.status == "not_met"
    assert "confirm the profile classification" in result.action_if_unmet


def test_evaluate_rule_includes_startup_india_remediation() -> None:
    rule = Rule(
        id="dpiit",
        field="dpiit_recognized",
        operator="equals",
        value=True,
        citations=[TEST_CITATION],
    )
    profile = SAMPLE_PROFILE.model_copy(update={"dpiit_recognized": False})

    result = evaluate_rule(profile, rule)

    assert result.status == "not_met"
    assert result.action_if_unmet == (
        "Obtain DPIIT recognition via the Startup India portal."
    )


def test_evaluate_rule_supports_boolean_membership() -> None:
    rule = Rule(
        id="women-led-option",
        field="women_led",
        operator="in",
        value=[False, True],
        citations=[TEST_CITATION],
    )

    assert evaluate_rule(SAMPLE_PROFILE, rule).status == "met"


@pytest.mark.parametrize(
    ("profile_index", "field", "operator", "value", "expected"),
    [
        (0, "sector", "equals", "agri-tech", "met"),
        (1, "sector", "equals", "agri-tech", "not_met"),
        (2, "annual_turnover_inr", "greater_than", 0, "unknown"),
    ],
)
def test_evaluate_covers_all_sample_profiles(
    profile_index: int,
    field: str,
    operator: str,
    value: object,
    expected: str,
) -> None:
    profiles = load_profiles(DATA_DIR / "profiles")
    scheme = Scheme(
        id="sample-test",
        name="Sample test",
        rules=[
            Rule(
                id="sample-rule",
                field=field,
                operator=operator,
                value=value,
                citations=[TEST_CITATION],
            )
        ],
    )

    results = evaluate(profiles[profile_index], scheme)

    assert len(results) == 1
    assert results[0].status == expected


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["met"], "eligible"),
        (["not_met"], "not eligible"),
        (["unknown"], "needs more info"),
        (["met", "unknown"], "needs more info"),
        (["not_met", "unknown"], "not eligible"),
        ([], "needs more info"),
    ],
)
def test_overall_verdict_summarizes_rule_results(
    statuses: list[str], expected: str
) -> None:
    results = [
        RuleResult(
            rule_id=str(index),
            status=status,
            reason="test",
            citations=[TEST_CITATION],
        )
        for index, status in enumerate(statuses)
    ]

    assert overall_verdict(results) == expected


def test_overall_verdict_requires_citations_for_conclusions() -> None:
    result = RuleResult(rule_id="uncited", status="met", reason="test")

    assert overall_verdict([result]) == "needs more info"


def test_evaluate_scheme_returns_unknown_when_there_are_no_rules() -> None:
    scheme = Scheme(id="empty", name="Empty scheme")

    result = evaluate_scheme(SAMPLE_PROFILE, scheme)

    assert result.status == "unknown"
    assert result.rule_results == []
    assert result.citations == []


def test_evaluate_scheme_returns_ineligible_when_a_cited_rule_fails() -> None:
    scheme = Scheme(
        id="failed",
        name="Failed scheme",
        rules=[
            Rule(
                id="failed-rule",
                field="sector",
                operator="equals",
                value="fintech",
                citations=[TEST_CITATION],
            )
        ],
    )

    result = evaluate_scheme(SAMPLE_PROFILE, scheme)

    assert result.status == "ineligible"
    assert result.citations == [TEST_CITATION]


def test_evaluate_scheme_returns_unknown_when_a_rule_needs_data() -> None:
    profile = load_profiles(DATA_DIR / "profiles")[2]
    scheme = Scheme(
        id="unknown",
        name="Unknown scheme",
        rules=[
            Rule(
                id="missing-rule",
                field="annual_turnover_inr",
                operator="greater_than",
                value=0,
                citations=[TEST_CITATION],
            )
        ],
    )

    assert evaluate_scheme(profile, scheme).status == "unknown"


def test_evaluate_scheme_returns_eligible_with_cited_satisfied_rules() -> None:
    scheme = Scheme(
        id="passed",
        name="Passed scheme",
        rules=[
            Rule(
                id="passed-rule",
                field="sector",
                operator="equals",
                value="agri-tech",
                citations=[TEST_CITATION],
            )
        ],
    )

    result = evaluate_scheme(SAMPLE_PROFILE, scheme)

    assert result.status == "eligible"
    assert result.rule_results[0].status == "met"
    assert result.citations == [TEST_CITATION]
