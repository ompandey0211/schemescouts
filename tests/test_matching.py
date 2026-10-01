from pathlib import Path

import pytest

from src.ingest import load_profiles
from src.matching import (
    FIT_SCORE_WEIGHTS,
    incubator_fit_reasons,
    incubator_fit_score,
    rank_incubators,
)
from src.models import Citation, Profile, RuleResult, Scheme

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SAMPLE_PROFILES = load_profiles(DATA_DIR / "profiles")
CITATION = Citation(
    document="test-programme.pdf",
    page=1,
    exact_clause="Synthetic test eligibility clause.",
)


def _sample_result(rule_id: str, status: str) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        status=status,
        reason=f"Synthetic {status} result.",
        citations=[CITATION],
    )


@pytest.mark.parametrize("profile", SAMPLE_PROFILES, ids=lambda profile: profile.id)
def test_rank_incubators_is_deterministic_for_all_sample_profiles(
    profile: Profile,
) -> None:
    matching = Scheme(
        id="matching",
        name="Matching programme",
        kind="incubator",
        sectors=[profile.sector],
        stages=[profile.stage],
        states=[profile.state],
    )
    mismatching = Scheme(
        id="mismatching",
        name="Mismatching programme",
        kind="incubator",
        sectors=["not-" + (profile.sector or "known")],
        stages=["non-matching-stage"],
        states=["non-matching-state"],
    )
    matches = [
        (mismatching, [_sample_result("sector", "not_met")]),
        (matching, [_sample_result("sector", "met")]),
    ]

    first = rank_incubators(profile, matches)
    second = rank_incubators(profile, matches)

    assert [(incubator.id, score) for incubator, _, score in first] == [
        ("matching", 100),
        ("mismatching", 0),
    ]
    assert [(incubator.id, score) for incubator, _, score in first] == [
        (incubator.id, score) for incubator, _, score in second
    ]


def test_fit_score_uses_configured_weights_and_counts_unknown_rules() -> None:
    profile = SAMPLE_PROFILES[0]
    incubator = Scheme(
        id="partial",
        name="Partial match",
        kind="incubator",
        sectors=[profile.sector],
        stages=[profile.stage],
        states=["Delhi"],
    )

    score = incubator_fit_score(
        profile,
        incubator,
        [
            _sample_result("met", "met"),
            _sample_result("not-met", "not_met"),
            _sample_result("unknown", "unknown"),
        ],
    )

    assert FIT_SCORE_WEIGHTS == {
        "sector": 20,
        "stage": 20,
        "location": 20,
        "eligibility": 40,
    }
    assert sum(FIT_SCORE_WEIGHTS.values()) == 100
    assert score == 53


@pytest.mark.parametrize("profile", SAMPLE_PROFILES, ids=lambda profile: profile.id)
def test_fit_score_with_no_rules_has_no_eligibility_credit(profile: Profile) -> None:
    incubator = Scheme(
        id="unverified",
        name="No extracted rules",
        kind="incubator",
    )

    assert incubator_fit_score(profile, incubator, []) == 60


@pytest.mark.parametrize("profile", SAMPLE_PROFILES, ids=lambda profile: profile.id)
def test_fit_reasons_explain_targets_and_rule_statuses(profile: Profile) -> None:
    incubator = Scheme(
        id="reasons",
        name="Reasons programme",
        kind="incubator",
        sectors=[profile.sector],
        stages=["non-matching-stage"],
        states=[],
    )

    reasons = incubator_fit_reasons(
        profile, incubator, [_sample_result("rule-a", "unknown")]
    )

    assert any(f"Sector: {profile.sector} matches" in reason for reason in reasons)
    assert any("does not match" in reason for reason in reasons)
    assert "Location: no programme restriction." in reasons
    assert "Eligibility: 0 of 1 extracted rules met." in reasons
    assert any("Rule rule-a (unknown)" in reason for reason in reasons)


def test_scheme_kind_defaults_for_existing_definitions() -> None:
    assert Scheme(id="legacy", name="Legacy scheme").kind == "scheme"


def test_rank_incubators_rejects_scheme_entries() -> None:
    with pytest.raises(ValueError, match="Only incubators"):
        rank_incubators(
            SAMPLE_PROFILES[0],
            [(Scheme(id="scheme", name="Not an incubator"), [])],
        )
