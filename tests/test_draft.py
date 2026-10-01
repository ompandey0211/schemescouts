from datetime import date
from pathlib import Path

from src.draft import DRAFT_NOTE, NEEDS_INPUT, _display, _eligibility_line, generate_draft
from src.ingest import load_profiles
from src.models import Citation, Profile, Rule, RuleResult, Scheme

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SAMPLE_PROFILE = load_profiles(DATA_DIR / "profiles")[0]
TEST_CITATION = Citation(
    document="official-scheme.pdf",
    page=5,
    exact_clause="Applicants must be DPIIT recognized startups.",
)


def _scheme() -> Scheme:
    return Scheme(
        id="test-scheme",
        name="Test Scheme",
        rules=[
            Rule(
                id="dpiit",
                field="dpiit_recognized",
                operator="equals",
                value=True,
                citations=[TEST_CITATION],
            )
        ],
    )


def test_display_formats_missing_booleans_numbers_and_text() -> None:
    assert _display(None) == NEEDS_INPUT
    assert _display("") == NEEDS_INPUT
    assert _display(True) == "Yes"
    assert _display(False) == "No"
    assert _display(125000) == "125,000"
    assert _display("Karnataka") == "Karnataka"


def test_eligibility_line_uses_result_status_and_exact_citation() -> None:
    result = RuleResult(
        rule_id="dpiit",
        status="met",
        reason="Condition satisfied.",
        citations=[TEST_CITATION],
    )

    line = _eligibility_line("dpiit", result, [])

    assert "dpiit — met" in line
    assert "official-scheme.pdf, page 5" in line
    assert TEST_CITATION.exact_clause in line


def test_eligibility_line_marks_missing_citation_as_needs_input() -> None:
    result = RuleResult(rule_id="dpiit", status="unknown", reason="No citation.")

    assert NEEDS_INPUT in _eligibility_line("dpiit", result, [])


def test_generate_draft_uses_complete_profile_without_placeholders() -> None:
    profile = SAMPLE_PROFILE.model_copy(
        update={
            "incorporation_date": date(2021, 5, 17),
            "funding_raised_inr": 2_000_000,
            "funding_requirement_inr": 5_000_000,
            "num_founders": 3,
            "use_of_funds": "Expand field trials and improve irrigation sensors.",
            "declaration": "I confirm these details are accurate.",
            "dpiit_recognized": True,
        }
    )
    results = [
        RuleResult(
            rule_id="dpiit",
            status="met",
            reason="Condition satisfied.",
            citations=[TEST_CITATION],
        )
    ]

    draft = generate_draft(profile, _scheme(), results)

    assert NEEDS_INPUT not in draft
    for section in (
        "Applicant Details",
        "Startup Overview",
        "Eligibility Summary",
        "Funding Requirement",
        "Use of Funds",
        "Team",
        "Declaration",
    ):
        assert f"## {section}" in draft
    assert "Greenfield Labs" in draft
    assert "Incorporation date: 2021-05-17" in draft
    assert "2,000,000" in draft
    assert "5,000,000" in draft
    assert "Expand field trials and improve irrigation sensors." in draft
    assert "I confirm these details are accurate." in draft
    assert "dpiit — met" in draft
    assert TEST_CITATION.exact_clause in draft
    assert "official-scheme.pdf, page 5" in draft
    assert DRAFT_NOTE in draft


def test_generate_draft_marks_all_missing_profile_values() -> None:
    profile = Profile(
        id="incomplete",
        name="Sparse startup",
        sector=None,
        stage=None,
        state=None,
        incorporation_year=None,
        annual_turnover_inr=None,
        funding_raised_inr=None,
        funding_requirement_inr=None,
        employee_count=None,
        num_founders=None,
        entity_type=None,
        women_led=None,
        dpiit_recognized=None,
        use_of_funds=None,
        declaration=None,
    )
    results = [
        RuleResult(
            rule_id="dpiit",
            status="unknown",
            reason="DPIIT status is missing.",
            citations=[TEST_CITATION],
        )
    ]

    draft = generate_draft(profile, _scheme(), results)

    assert draft.count(NEEDS_INPUT) >= 10
    assert "dpiit — unknown" in draft
    assert TEST_CITATION.exact_clause in draft
    assert DRAFT_NOTE in draft


def test_generate_draft_includes_every_scheme_rule_even_if_result_missing() -> None:
    scheme = Scheme(
        id="two-rules",
        name="Two rules",
        rules=[
            _scheme().rules[0],
            Rule(
                id="turnover",
                field="annual_turnover_inr",
                operator="less_than",
                value=10_000_000,
                citations=[TEST_CITATION],
            ),
        ],
    )

    draft = generate_draft(SAMPLE_PROFILE, scheme, [])

    assert "dpiit — unknown" in draft
    assert "turnover — unknown" in draft


def test_generate_draft_uses_scheme_citation_if_result_has_no_citation() -> None:
    result = RuleResult(
        rule_id="dpiit",
        status="unknown",
        reason="Rule is unverified.",
    )

    draft = generate_draft(SAMPLE_PROFILE, _scheme(), [result])

    assert "dpiit — unknown" in draft
    assert TEST_CITATION.exact_clause in draft
