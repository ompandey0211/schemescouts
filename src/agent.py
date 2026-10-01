import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from src.evaluate import OverallVerdict, evaluate, evaluate_scheme, overall_verdict
from src.extract import (
    DEFAULT_CACHE_DIR,
    ManualReviewItem,
    RuleExtraction,
    extract_rules,
)
from src.ingest import PDFPage, load_pdf_pages, load_profiles, load_schemes
from src.models import Profile, RuleResult, Scheme, SchemeEvaluation
from src.plan import (
    ChecklistItem,
    PlanStep,
    RequirementGap,
    build_checklist,
    find_gaps,
    make_plan,
)

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
RULE_CACHE_DIR = DEFAULT_CACHE_DIR
T = TypeVar("T")
RetryableStep = Callable[[], T]


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    step: str
    status: Literal["started", "completed", "retrying", "failed", "filtered"]
    attempt: int = Field(ge=1, le=MAX_ATTEMPTS)
    message: str


class SchemeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme_id: str
    scheme_name: str
    verdict: OverallVerdict
    rule_results: list[RuleResult] = Field(default_factory=list)
    manual_review: list[ManualReviewItem] = Field(default_factory=list)
    gaps: list[RequirementGap] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)


class AgentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    profile_name: str | None = None
    status: Literal["completed", "partial", "failed"]
    schemes: list[SchemeReport] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _record_event(
    events: list[AgentEvent] | None,
    step: str,
    status: Literal["started", "completed", "retrying", "failed", "filtered"],
    message: str,
    attempt: int = 1,
) -> None:
    logger.info("agent step=%s status=%s attempt=%s: %s", step, status, attempt, message)
    if events is not None:
        events.append(
            AgentEvent(
                step=step,
                status=status,
                attempt=attempt,
                message=message,
            )
        )


def _run_with_retries(
    step: str,
    operation: RetryableStep[T],
    events: list[AgentEvent],
    max_attempts: int,
) -> T:
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}.")
    for attempt in range(1, max_attempts + 1):
        _record_event(events, step, "started", "Running pipeline step.", attempt)
        try:
            result = operation()
        except Exception as exc:
            final_attempt = attempt == max_attempts
            status = "failed" if final_attempt else "retrying"
            _record_event(
                events,
                step,
                status,
                f"Attempt failed: {type(exc).__name__}: {exc}",
                attempt,
            )
            if final_attempt:
                raise
        else:
            _record_event(events, step, "completed", "Pipeline step completed.", attempt)
            return result
    raise RuntimeError(f"Pipeline step {step!r} exhausted all attempts.")


def load_profile_tool(
    profile_id: str,
    profiles_dir: Path,
    *,
    events: list[AgentEvent] | None = None,
) -> Profile:
    """Load one validated startup profile by its stable ID."""
    profiles = load_profiles(profiles_dir)
    for profile in profiles:
        if profile.id == profile_id:
            _record_event(
                events,
                "load_profile",
                "completed",
                f"Loaded profile {profile.id}.",
            )
            return profile
    raise FileNotFoundError(f"No profile with id {profile_id!r} in {profiles_dir}.")


def _matches_target(profile_value: str | None, targets: list[str]) -> bool:
    if not targets:
        return True
    if profile_value is None:
        return False
    return profile_value.strip().casefold() in {
        target.strip().casefold() for target in targets
    }


def find_relevant_schemes_tool(
    profile: Profile,
    schemes: Sequence[Scheme],
    *,
    events: list[AgentEvent] | None = None,
) -> list[Scheme]:
    """Filter schemes by optional sector, stage, and state targeting metadata."""
    relevant: list[Scheme] = []
    for scheme in schemes:
        matches = (
            _matches_target(profile.sector, scheme.sectors)
            and _matches_target(profile.stage, scheme.stages)
            and _matches_target(profile.state, scheme.states)
        )
        if matches:
            relevant.append(scheme)
        else:
            _record_event(
                events,
                "find_relevant_schemes",
                "filtered",
                f"Scheme {scheme.id} did not match the profile's sector, stage, or state.",
            )
    _record_event(
        events,
        "find_relevant_schemes",
        "completed",
        f"Selected {len(relevant)} of {len(schemes)} schemes.",
    )
    return relevant


def extract_rules_tool(
    scheme: Scheme,
    schemes_dir: Path,
    *,
    cache_dir: Path = RULE_CACHE_DIR,
    events: list[AgentEvent] | None = None,
) -> RuleExtraction:
    """Read a programme PDF and cache verified rules outside its source directory."""
    pdf_path = schemes_dir / f"{scheme.id}.pdf"
    pages: list[PDFPage] = load_pdf_pages(pdf_path)
    extracted = extract_rules(
        pages,
        scheme_id=scheme.id,
        document=pdf_path.name,
        cache_dir=cache_dir,
    )
    _record_event(
        events,
        "extract_rules",
        "completed",
        f"Verified {len(extracted.rules)} rules and {len(extracted.manual_review)} manual-review items for {scheme.id}.",
    )
    return extracted


def evaluate_tool(
    profile: Profile,
    scheme: Scheme,
    *,
    events: list[AgentEvent] | None = None,
) -> list[RuleResult]:
    """Evaluate the scheme rules deterministically against the profile."""
    results = evaluate(profile, scheme)
    _record_event(
        events,
        "evaluate",
        "completed",
        f"Evaluated {len(results)} rules for {scheme.id}.",
    )
    return results


def find_gaps_tool(
    results: list[RuleResult],
    *,
    events: list[AgentEvent] | None = None,
) -> list[RequirementGap]:
    """Find and prioritize unmet and unknown requirements."""
    gaps = find_gaps(results)
    _record_event(
        events,
        "find_gaps",
        "completed",
        f"Found {len(gaps)} unmet or unknown requirements.",
    )
    return gaps


def build_checklist_tool(
    scheme: Scheme,
    results: list[RuleResult],
    *,
    events: list[AgentEvent] | None = None,
) -> list[ChecklistItem]:
    """Build the evidence checklist for a scheme."""
    checklist = build_checklist(scheme, results)
    _record_event(
        events,
        "build_checklist",
        "completed",
        f"Prepared {len(checklist)} checklist documents.",
    )
    return checklist


def make_plan_tool(
    profile: Profile,
    scheme: Scheme,
    results: list[RuleResult],
    *,
    events: list[AgentEvent] | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> list[PlanStep]:
    """Build dated actions, optionally using an LLM only for action phrasing."""
    plan = make_plan(profile, scheme, results, llm_call=llm_call)
    _record_event(
        events,
        "make_plan",
        "completed",
        f"Created {len(plan)} citation-linked actions.",
    )
    return plan


def evaluate_profile_against_schemes(
    profile: Profile, schemes: Sequence[Scheme]
) -> list[SchemeEvaluation]:
    """Compatibility helper returning scheme evaluations for callers of the starter API."""
    return [evaluate_scheme(profile, scheme) for scheme in schemes]


def run_agent(
    profile_id: str,
    *,
    profiles_dir: Path | None = None,
    schemes_dir: Path | None = None,
    rule_cache_dir: Path = RULE_CACHE_DIR,
    max_attempts: int = MAX_ATTEMPTS,
    llm_call: Callable[[str], str] | None = None,
) -> AgentReport:
    """Run the SchemeScout pipeline and return a structured, logged report."""
    root = Path(__file__).resolve().parents[1]
    profile_directory = profiles_dir or root / "data" / "profiles"
    scheme_directory = schemes_dir or root / "data" / "schemes"
    report = AgentReport(profile_id=profile_id, status="completed")

    try:
        profile = _run_with_retries(
            "load_profile",
            lambda: load_profile_tool(profile_id, profile_directory),
            report.events,
            max_attempts,
        )
    except Exception as exc:
        report.status = "failed"
        report.errors.append(f"load_profile: {type(exc).__name__}: {exc}")
        return report
    report.profile_name = profile.name

    try:
        schemes = _run_with_retries(
            "load_schemes",
            lambda: load_schemes(scheme_directory),
            report.events,
            max_attempts,
        )
        relevant_schemes = _run_with_retries(
            "find_relevant_schemes",
            lambda: find_relevant_schemes_tool(profile, schemes, events=report.events),
            report.events,
            max_attempts,
        )
    except Exception as exc:
        report.status = "failed"
        report.errors.append(f"load_schemes: {type(exc).__name__}: {exc}")
        return report

    for target_scheme in relevant_schemes:
        try:
            extracted = _run_with_retries(
                "extract_rules",
                lambda target=target_scheme: extract_rules_tool(
                    target, scheme_directory, cache_dir=rule_cache_dir
                ),
                report.events,
                max_attempts,
            )
            scheme = target_scheme.model_copy(update={"rules": extracted.rules})
            results = _run_with_retries(
                "evaluate",
                lambda: evaluate_tool(profile, scheme),
                report.events,
                max_attempts,
            )
            gaps = _run_with_retries(
                "find_gaps",
                lambda: find_gaps_tool(results),
                report.events,
                max_attempts,
            )
            checklist = _run_with_retries(
                "build_checklist",
                lambda: build_checklist_tool(scheme, results),
                report.events,
                max_attempts,
            )
            plan = _run_with_retries(
                "make_plan",
                lambda: make_plan_tool(
                    profile, scheme, results, llm_call=llm_call
                ),
                report.events,
                max_attempts,
            )
        except Exception as exc:
            report.errors.append(
                f"{target_scheme.id}: {type(exc).__name__}: {exc}"
            )
            continue

        report.schemes.append(
            SchemeReport(
                scheme_id=scheme.id,
                scheme_name=scheme.name,
                verdict=overall_verdict(results),
                rule_results=results,
                manual_review=extracted.manual_review,
                gaps=gaps,
                checklist=checklist,
                plan=plan,
            )
        )

    if report.errors:
        report.status = "partial" if report.schemes else "failed"
    return report
