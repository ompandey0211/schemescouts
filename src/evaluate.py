from typing import Literal

from src.models import Profile, Rule, RuleResult, Scheme, SchemeEvaluation

OverallVerdict = Literal["eligible", "not eligible", "needs more info"]


def evaluate_rule(profile: Profile, rule: Rule) -> RuleResult:
    """Evaluate one cited rule deterministically against the supplied profile."""
    if rule.field not in Profile.model_fields:
        return RuleResult(
            rule_id=rule.id,
            status="unknown",
            reason=f"Unsupported profile field: {rule.field}.",
            citations=rule.citations,
        )

    actual = getattr(profile, rule.field)
    if actual is None:
        return RuleResult(
            rule_id=rule.id,
            status="unknown",
            reason=f"Profile value for {rule.field} is missing.",
            citations=rule.citations,
        )

    if not rule.citations:
        return RuleResult(
            rule_id=rule.id,
            status="unknown",
            reason="Rule has no source citation.",
        )

    compatible_boolean = isinstance(rule.value, bool)
    if (
        isinstance(actual, bool)
        and rule.operator in {"in", "not_in"}
        and isinstance(rule.value, (list, tuple, set, frozenset))
    ):
        compatible_boolean = all(isinstance(value, bool) for value in rule.value)
    if (isinstance(actual, bool) and not compatible_boolean) or (
        not isinstance(actual, bool) and isinstance(rule.value, bool)
    ):
        return RuleResult(
            rule_id=rule.id,
            status="unknown",
            reason="Rule value is incompatible with its operator.",
            citations=rule.citations,
        )

    try:
        match rule.operator:
            case "equals":
                satisfied = actual == rule.value
            case "not_equals":
                satisfied = actual != rule.value
            case "greater_than":
                satisfied = actual > rule.value
            case "greater_than_or_equal":
                satisfied = actual >= rule.value
            case "less_than":
                satisfied = actual < rule.value
            case "less_than_or_equal":
                satisfied = actual <= rule.value
            case "in":
                satisfied = actual in rule.value
            case "not_in":
                satisfied = actual not in rule.value
            case "contains":
                satisfied = rule.value in actual
    except (TypeError, ValueError):
        return RuleResult(
            rule_id=rule.id,
            status="unknown",
            reason="Rule value is incompatible with its operator.",
            citations=rule.citations,
        )

    if satisfied:
        return RuleResult(
            rule_id=rule.id,
            status="met",
            reason="Cited eligibility condition is met.",
            citations=rule.citations,
        )
    return RuleResult(
        rule_id=rule.id,
        status="not_met",
        reason="Cited eligibility condition is not met.",
        action_if_unmet=_action_for_unmet_rule(rule),
        citations=rule.citations,
    )


def _action_for_unmet_rule(rule: Rule) -> str:
    if rule.field == "dpiit_recognized" and rule.operator == "equals" and rule.value is True:
        return "Obtain DPIIT recognition via the Startup India portal."
    if rule.field == "women_led":
        return "Review the scheme's women-led criteria and confirm the profile classification with supporting documents."
    if rule.field == "state":
        return f"Establish eligible operations in one of the cited locations: {rule.value}."
    if rule.field == "sector":
        return f"Document the startup's activities and confirm they align with the cited sector requirement: {rule.value}."
    if rule.field == "entity_type":
        return f"Consult a qualified advisor about whether restructuring to {rule.value} is appropriate for this scheme."
    if rule.field in {"annual_turnover_inr", "funding_raised_inr"}:
        return "Review the cited financial threshold and verify the figures against audited financial records."
    return (
        f"Review the cited requirement for {rule.field} and identify a concrete "
        "remediation with the scheme authority."
    )


def evaluate(profile: Profile, scheme: Scheme) -> list[RuleResult]:
    """Evaluate every rule in a scheme, preserving scheme rule order."""
    return [evaluate_rule(profile, rule) for rule in scheme.rules]


def overall_verdict(results: list[RuleResult]) -> OverallVerdict:
    """Summarize rule outcomes; any failed condition rules out eligibility."""
    if any(
        result.status == "not_met" and result.citations
        for result in results
    ):
        return "not eligible"
    if not results or any(
        result.status == "unknown" or not result.citations for result in results
    ):
        return "needs more info"
    if any(result.status == "not_met" for result in results):
        return "needs more info"
    return "eligible"


def evaluate_scheme(profile: Profile, scheme: Scheme) -> SchemeEvaluation:
    """Build the legacy scheme summary around the citation-backed rule results."""
    rule_results = evaluate(profile, scheme)
    verdict = overall_verdict(rule_results)
    status: Literal["eligible", "ineligible", "unknown"] = {
        "eligible": "eligible",
        "not eligible": "ineligible",
        "needs more info": "unknown",
    }[verdict]
    summary = {
        "eligible": "All available cited eligibility rules are satisfied.",
        "ineligible": "At least one cited eligibility rule is not satisfied.",
        "unknown": (
            "No cited eligibility rules are available."
            if not rule_results
            else "One or more eligibility rules cannot be evaluated from available data."
        ),
    }[status]
    return SchemeEvaluation(
        scheme_id=scheme.id,
        scheme_name=scheme.name,
        status=status,
        summary=summary,
        rule_results=rule_results,
        citations=[
            citation for result in rule_results for citation in result.citations
        ],
    )
