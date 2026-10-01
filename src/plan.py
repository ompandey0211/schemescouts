import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.config import get_llm_settings
from src.extract import _call_llm
from src.models import Citation, Profile, RuleResult, Scheme, SchemeEvaluation

logger = logging.getLogger(__name__)

PhraseCall = Callable[[str], str]


class RequirementGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    status: Literal["not_met", "unknown"]
    requirement: str
    blocking_priority: int = Field(ge=1, le=2)
    next_step: str
    citations: list[Citation] = Field(default_factory=list)


class ChecklistItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str
    where_to_obtain: str
    required_by: list[str]
    citations: list[Citation]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    priority: Literal["high", "medium", "low"]
    action: str
    rule_id: str
    citations: list[Citation] = Field(default_factory=list)


DOCUMENTS_BY_FIELD: dict[str, tuple[str, str]] = {
    "dpiit_recognized": (
        "DPIIT recognition certificate",
        "Download it from the Startup India portal after recognition is granted.",
    ),
    "incorporation_year": (
        "Certificate of incorporation or registration",
        "Obtain it from the Ministry of Corporate Affairs (MCA) portal or the relevant registering authority.",
    ),
    "entity_type": (
        "Certificate of incorporation or registration",
        "Obtain it from the Ministry of Corporate Affairs (MCA) portal or the relevant registering authority.",
    ),
    "annual_turnover_inr": (
        "Audited financial statements",
        "Request them from the startup's chartered accountant or finance team.",
    ),
    "funding_raised_inr": (
        "Funding and investment records",
        "Compile investment agreements and statements from the startup's finance team or investors.",
    ),
    "sector": (
        "Pitch deck or product/service brief",
        "Prepare it using current product, customer, and business materials.",
    ),
    "women_led": (
        "Shareholding and leadership records",
        "Obtain the latest cap table and relevant company filings from the company secretary or finance team.",
    ),
    "state": (
        "Registered-office or operations proof",
        "Obtain a current lease, utility bill, or relevant registration filing for the stated location.",
    ),
}


def _requirement_description(result: RuleResult) -> str:
    if result.citations:
        return result.citations[0].exact_clause
    return result.reason


def _unknown_next_step(result: RuleResult, profile_name: str) -> str:
    if "missing" in result.reason.lower():
        return (
            f"Confirm the missing profile information for {profile_name} "
            f"needed by rule {result.rule_id}."
        )
    return (
        f"Resolve the uncertainty for {profile_name} under rule {result.rule_id} "
        "using its official source."
    )


def find_gaps(
    results: list[RuleResult], profile_name: str | None = None
) -> list[RequirementGap]:
    """Return unmet and unknown requirements, most blocking gaps first."""
    applicant_name = profile_name or "the applicant"
    gaps: list[RequirementGap] = []
    for result in results:
        if result.status not in {"not_met", "unknown"}:
            continue
        is_unmet = result.status == "not_met"
        gaps.append(
            RequirementGap(
                rule_id=result.rule_id,
                status=result.status,
                requirement=_requirement_description(result),
                blocking_priority=1 if is_unmet else 2,
                next_step=(
                    result.action_if_unmet
                    if is_unmet and result.action_if_unmet
                    else _unknown_next_step(
                        result,
                        applicant_name,
                    )
                    if not is_unmet
                    else f"Address the unmet requirement in rule {result.rule_id}."
                ),
                citations=result.citations,
            )
        )
    return sorted(gaps, key=lambda gap: (gap.blocking_priority, gap.rule_id))


def _citations_for_rule(
    rule_id: str, results_by_id: dict[str, RuleResult], citations: list[Citation]
) -> list[Citation]:
    result = results_by_id.get(rule_id)
    selected = result.citations if result and result.citations else citations
    unique: dict[tuple[str, int, str], Citation] = {}
    for citation in selected:
        unique[(citation.document, citation.page, citation.exact_clause)] = citation
    return list(unique.values())


def build_checklist(scheme: Scheme, results: list[RuleResult]) -> list[ChecklistItem]:
    """Map cited profile-field rules to applicant documents and their sources."""
    results_by_id = {result.rule_id: result for result in results}
    items_by_document: dict[str, ChecklistItem] = {}
    for rule in scheme.rules:
        document_details = DOCUMENTS_BY_FIELD.get(rule.field)
        if not document_details:
            continue
        citations = _citations_for_rule(
            rule.id, results_by_id, rule.citations
        )
        if not citations:
            logger.warning(
                "Skipping checklist document for rule %s because it has no citation.",
                rule.id,
            )
            continue
        document, where_to_obtain = document_details
        if document not in items_by_document:
            items_by_document[document] = ChecklistItem(
                document=document,
                where_to_obtain=where_to_obtain,
                required_by=[],
                citations=[],
            )
        item = items_by_document[document]
        if rule.id not in item.required_by:
            item.required_by.append(rule.id)
        item.citations.extend(citations)
        item.citations = list(
            {
                (citation.document, citation.page, citation.exact_clause): citation
                for citation in item.citations
            }.values()
        )
    return list(items_by_document.values())


def _phrase_action(action: str, llm_call: PhraseCall | None) -> str:
    settings = get_llm_settings()
    if llm_call is None and not (settings.base_url and settings.model):
        return action
    phrase_call = llm_call or _call_llm
    prompt = (
        "Rephrase this application-plan action clearly and concisely. Preserve "
        "its meaning. Do not add facts, requirements, dates, priorities, rule "
        f"IDs, or citations. Return only the rephrased action text:\n{action}"
    )
    phrased = phrase_call(prompt).strip()
    if not phrased:
        raise ValueError("The language model returned an empty action phrase.")
    return phrased


def _step_priority(blocking_priority: int) -> Literal["high", "medium", "low"]:
    return "high" if blocking_priority == 1 else "medium"


def make_plan(
    profile: Profile,
    scheme: Scheme,
    results: list[RuleResult],
    *,
    start_date: date | None = None,
    llm_call: PhraseCall | None = None,
) -> list[PlanStep]:
    """Create a dated, citation-linked action for each unmet or unknown rule."""
    rules_by_id = {rule.id: rule for rule in scheme.rules}
    gaps = find_gaps(results, profile.name)
    plan_start = start_date or date.today()
    steps: list[PlanStep] = []
    for index, gap in enumerate(gaps):
        rule = rules_by_id.get(gap.rule_id)
        if rule is None:
            logger.warning("Skipping gap for rule %s not present in scheme.", gap.rule_id)
            continue
        citation = gap.citations or rule.citations
        action = _phrase_action(f"For {profile.name}: {gap.next_step}", llm_call)
        reference = (
            f" [Rule {gap.rule_id}; "
            + "; ".join(
                f'{item.document}, p. {item.page}: "{item.exact_clause}"'
                for item in citation
            )
            + "]"
            if citation
            else f" [Rule {gap.rule_id}; source citation still needs verification]"
        )
        steps.append(
            PlanStep(
                date=plan_start + timedelta(days=index),
                priority=_step_priority(gap.blocking_priority),
                action=f"{action}{reference}",
                rule_id=gap.rule_id,
                citations=citation,
            )
        )
    return steps


def build_action_plan(evaluation: SchemeEvaluation) -> list[str]:
    """Retain the original UI's concise evaluation summary actions."""
    if evaluation.status == "unknown":
        return [
            "Collect missing profile information or verified source citations for unknown rules.",
            "Re-run the deterministic eligibility evaluation after updating the data.",
        ]
    if evaluation.status == "ineligible":
        return [
            "Review the cited rules marked not satisfied.",
            "Confirm profile data against the supporting documents before taking action.",
        ]
    return [
        "Review the source citations attached to every satisfied rule.",
        "Confirm the profile data is current before relying on this result.",
    ]
