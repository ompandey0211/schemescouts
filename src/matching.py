from collections.abc import Sequence
from typing import Final

from src.models import Profile, RuleResult, Scheme

FIT_SCORE_WEIGHTS: Final[dict[str, int]] = {
    "sector": 20,
    "stage": 20,
    "location": 20,
    "eligibility": 40,
}


def _target_matches(profile_value: str | None, targets: list[str]) -> bool:
    if not targets:
        return True
    if profile_value is None:
        return False
    return profile_value.strip().casefold() in {
        target.strip().casefold() for target in targets
    }


def incubator_fit_score(
    profile: Profile,
    incubator: Scheme,
    rule_results: Sequence[RuleResult],
) -> int:
    """Return a deterministic 0–100 score from targets and cited rule outcomes."""
    if incubator.kind != "incubator":
        raise ValueError("Fit scores can only be calculated for incubators.")

    dimension_scores = {
        "sector": _target_matches(profile.sector, incubator.sectors),
        "stage": _target_matches(profile.stage, incubator.stages),
        "location": _target_matches(profile.state, incubator.states),
    }
    eligibility_share = (
        sum(result.status == "met" for result in rule_results) / len(rule_results)
        if rule_results
        else 0
    )
    weighted_score = sum(
        FIT_SCORE_WEIGHTS[dimension] * matched
        for dimension, matched in dimension_scores.items()
    ) + FIT_SCORE_WEIGHTS["eligibility"] * eligibility_share
    return round(weighted_score)


def incubator_fit_reasons(
    profile: Profile,
    incubator: Scheme,
    rule_results: Sequence[RuleResult],
) -> list[str]:
    """Explain each targeting and eligibility component of an incubator score."""
    if incubator.kind != "incubator":
        raise ValueError("Fit reasons can only be calculated for incubators.")

    dimensions = (
        ("Sector", profile.sector, incubator.sectors),
        ("Stage", profile.stage, incubator.stages),
        ("Location", profile.state, incubator.states),
    )
    reasons: list[str] = []
    for label, profile_value, targets in dimensions:
        if not targets:
            reasons.append(f"{label}: no programme restriction.")
        elif profile_value is None:
            reasons.append(
                f"{label}: unknown for this profile; programme targets "
                f"{', '.join(targets)}."
            )
        elif _target_matches(profile_value, targets):
            reasons.append(f"{label}: {profile_value} matches the programme.")
        else:
            reasons.append(
                f"{label}: {profile_value} does not match the programme targets "
                f"{', '.join(targets)}."
            )

    if not rule_results:
        reasons.append("Eligibility: no extracted rules are available yet.")
    else:
        met_count = sum(result.status == "met" for result in rule_results)
        reasons.append(
            f"Eligibility: {met_count} of {len(rule_results)} extracted rules met."
        )
        reasons.extend(
            f"Rule {result.rule_id} ({result.status.replace('_', ' ')}): "
            f"{result.reason}"
            for result in rule_results
        )
    return reasons


def rank_incubators(
    profile: Profile,
    matches: Sequence[tuple[Scheme, Sequence[RuleResult]]],
) -> list[tuple[Scheme, list[RuleResult], int]]:
    """Rank incubators by fit score, then by stable name and ID."""
    scored: list[tuple[Scheme, list[RuleResult], int]] = []
    for incubator, rule_results in matches:
        if incubator.kind != "incubator":
            raise ValueError("Only incubators can be ranked as incubator matches.")
        results = list(rule_results)
        scored.append(
            (incubator, results, incubator_fit_score(profile, incubator, results))
        )
    return sorted(
        scored,
        key=lambda match: (-match[2], match[0].name.casefold(), match[0].id),
    )
