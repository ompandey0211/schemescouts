from src.models import Citation, Profile, RuleResult, Scheme

NEEDS_INPUT = "[NEEDS INPUT]"
DRAFT_NOTE = (
    "Review before submitting. Generated guidance, not an official application."
)


def _display(value: object | None) -> str:
    if value is None or value == "":
        return NEEDS_INPUT
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _eligibility_line(
    rule_id: str, result: RuleResult | None, citations: list[Citation]
) -> str:
    status = result.status if result else "unknown"
    if result and result.citations:
        citation_text = "; ".join(
            f'{citation.document}, page {citation.page}: "{citation.exact_clause}"'
            for citation in result.citations
        )
    elif citations:
        citation_text = "; ".join(
            f'{citation.document}, page {citation.page}: "{citation.exact_clause}"'
            for citation in citations
        )
    else:
        citation_text = NEEDS_INPUT
    return f"- **{rule_id} — {status}:** {citation_text}"


def generate_draft(
    profile: Profile, scheme: Scheme, results: list[RuleResult]
) -> str:
    """Create a Markdown application draft using only explicitly provided data."""
    results_by_id = {result.rule_id: result for result in results}
    eligibility_lines = [
        _eligibility_line(
            rule.id,
            results_by_id.get(rule.id),
            rule.citations,
        )
        for rule in scheme.rules
    ]
    if not eligibility_lines:
        eligibility_lines = [f"- {NEEDS_INPUT}"]

    incorporation = profile.incorporation_date or profile.incorporation_year
    overview_parts = [
        f"{profile.name} is a startup operating in {_display(profile.sector)}.",
        f"It is at the {_display(profile.stage)} stage and is based in {_display(profile.state)}.",
        f"The entity type is {_display(profile.entity_type)}.",
    ]

    return "\n".join(
        [
            f"# Application draft: {scheme.name}",
            "",
            "## Applicant Details",
            f"- Applicant name: {_display(profile.name)}",
            f"- Startup name: {_display(profile.name)}",
            f"- State: {_display(profile.state)}",
            f"- Entity type: {_display(profile.entity_type)}",
            f"- Incorporation date: {_display(incorporation)}",
            "",
            "## Startup Overview",
            " ".join(overview_parts),
            f"- Sector: {_display(profile.sector)}",
            f"- Stage: {_display(profile.stage)}",
            f"- DPIIT recognized: {_display(profile.dpiit_recognized)}",
            f"- Annual turnover (INR): {_display(profile.annual_turnover_inr)}",
            "",
            "## Eligibility Summary",
            *eligibility_lines,
            "",
            "## Funding Requirement",
            f"- Funding raised to date (INR): {_display(profile.funding_raised_inr)}",
            f"- Funding requested (INR): {_display(profile.funding_requirement_inr)}",
            "",
            "## Use of Funds",
            _display(profile.use_of_funds),
            "",
            "## Team",
            f"- Number of founders: {_display(profile.num_founders)}",
            f"- Employee count: {_display(profile.employee_count)}",
            f"- Women-led: {_display(profile.women_led)}",
            "",
            "## Declaration",
            _display(profile.declaration),
            "",
            "---",
            f"*{DRAFT_NOTE}*",
            "",
        ]
    )
