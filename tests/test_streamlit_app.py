from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

import app.streamlit_app as streamlit_app
from src.extract import RuleExtraction
from src.evaluate import evaluate
from app.streamlit_app import (
    _translated_display,
    apply_document_review,
    document_review_rows,
    plan_markdown,
    plan_pdf,
    rank_relevant_schemes,
    render_scheme_verification,
    scheme_match_score,
    verification_is_stale,
)
from src.doc_analysis import (
    DocumentAnalysis,
    DocumentConflict,
    ExtractedField,
)
from src.ingest import load_profiles
from src.models import Citation, Profile, Rule, Scheme
from src.plan import ChecklistItem, PlanStep, RequirementGap

APP_PATH = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SAMPLE_PROFILE = load_profiles(DATA_DIR / "profiles")[0]
CITATION = Citation(
    document="scheme.pdf",
    page=4,
    exact_clause="Applicants must hold DPIIT recognition.",
)


def test_rank_relevant_schemes_filters_and_ranks_specific_matches() -> None:
    schemes = [
        Scheme(id="general", name="General"),
        Scheme(id="sector", name="Sector", sectors=["agri-tech"]),
        Scheme(
            id="specific",
            name="Specific",
            sectors=["AGRI-TECH"],
            stages=["growth"],
            states=["Karnataka"],
        ),
        Scheme(id="mismatch", name="Mismatch", sectors=["fintech"]),
    ]

    ranked = rank_relevant_schemes(SAMPLE_PROFILE, schemes)

    assert [scheme.id for scheme in ranked] == ["specific", "sector", "general"]


def test_scheme_match_score_counts_target_dimensions() -> None:
    scheme = Scheme(
        id="targeted",
        name="Targeted",
        sectors=["agri-tech"],
        states=["Karnataka"],
    )

    assert scheme_match_score(scheme) == 2


def test_verification_stale_boundary_is_strictly_older_than_six_months() -> None:
    assert not verification_is_stale(date(2026, 4, 1), date(2026, 10, 1))
    assert verification_is_stale(date(2026, 3, 31), date(2026, 10, 1))
    assert not verification_is_stale(None, date(2026, 10, 1))


def test_render_scheme_verification_shows_date_and_stale_warning(monkeypatch) -> None:
    scheme = Scheme(
        id="stale",
        name="Stale Scheme",
        last_verified_date=date(2026, 1, 1),
    )
    caption = Mock()
    warning = Mock()
    monkeypatch.setattr(streamlit_app.st, "caption", caption)
    monkeypatch.setattr(streamlit_app.st, "warning", warning)
    monkeypatch.setattr(streamlit_app, "verification_is_stale", lambda _date: True)

    render_scheme_verification(scheme)

    caption.assert_called_once_with("Last verified: 2026-01-01")
    warning.assert_called_once_with(
        "Stale Scheme has not been verified in over six months."
    )


def test_plan_markdown_includes_gaps_checklist_steps_and_disclaimer() -> None:
    scheme = Scheme(id="scheme", name="Startup scheme")
    gap = RequirementGap(
        rule_id="dpiit",
        status="not_met",
        requirement=CITATION.exact_clause,
        blocking_priority=1,
        next_step="Apply for recognition.",
        citations=[CITATION],
    )
    checklist = [
        ChecklistItem(
            document="DPIIT recognition certificate",
            where_to_obtain="Startup India portal.",
            required_by=["dpiit"],
            citations=[CITATION],
        )
    ]
    plan = [
        PlanStep(
            date=date(2026, 10, 1),
            priority="high",
            action="Apply [Rule dpiit].",
            rule_id="dpiit",
            citations=[CITATION],
        )
    ]

    markdown = plan_markdown(
        SAMPLE_PROFILE, scheme, "needs more info", [gap], checklist, plan
    )

    assert "Startup scheme" in markdown
    assert CITATION.exact_clause in markdown
    assert "DPIIT recognition certificate" in markdown
    assert "2026-10-01" in markdown
    assert "Guidance only, not legal or financial advice." in markdown


def test_plan_markdown_handles_empty_sections() -> None:
    markdown = plan_markdown(SAMPLE_PROFILE, Scheme(id="empty", name="Empty"), "eligible", [], [], [])

    assert "No unmet or unknown requirements" in markdown
    assert "No supporting documents" in markdown
    assert "No follow-up actions" in markdown


def test_plan_pdf_is_searchable_pdf() -> None:
    pdf_data = plan_pdf("# Action plan\nApply for recognition.")
    reader = PdfReader(BytesIO(pdf_data))

    assert len(reader.pages) == 1
    assert "Action plan" in reader.pages[0].extract_text()
    assert "Apply for recognition." in reader.pages[0].extract_text()


def test_plan_pdf_includes_content_after_first_page() -> None:
    markdown = "\n".join(f"Action item {index:02}" for index in range(70))
    reader = PdfReader(BytesIO(plan_pdf(markdown)))

    assert len(reader.pages) == 2
    assert "Action item 69" in reader.pages[-1].extract_text()


def test_run_analysis_returns_pipeline_outputs(monkeypatch) -> None:
    profile = SAMPLE_PROFILE
    scheme = Scheme(
        id="analysis",
        name="Analysis",
        sectors=["agri-tech"],
        rules=[
            Rule(
                id="sector",
                field="sector",
                operator="equals",
                value="agri-tech",
                citations=[CITATION],
            )
        ],
    )
    monkeypatch.setattr(
        streamlit_app,
        "extract_rules_tool",
        lambda target, directory: RuleExtraction(rules=target.rules),
    )

    analyses = streamlit_app.run_analysis(profile, [scheme])

    assert len(analyses) == 1
    assert analyses[0]["verdict"] == "eligible"
    assert analyses[0]["results"] == evaluate(profile, scheme)


def test_app_displays_profile_selector_and_disclaimer() -> None:
    app = AppTest.from_file(str(APP_PATH)).run()

    assert not app.exception
    assert app.selectbox(key="sample_profile").value == "Greenfield Labs"
    assert any(
        "Guidance only, not legal or financial advice." in warning.value
        for warning in app.warning
    )
    assert app.info


def test_app_can_select_each_sample_profile() -> None:
    app = AppTest.from_file(str(APP_PATH)).run()

    app.selectbox(key="sample_profile").select("BlueCanopy Systems").run()

    assert not app.exception
    assert app.selectbox(key="sample_profile").value == "BlueCanopy Systems"


def test_translated_display_is_english_without_provider() -> None:
    assert _translated_display("Review the checklist.", "en") == (
        "Review the checklist."
    )


def test_translation_cache_reuses_translated_string(monkeypatch) -> None:
    translate_call = Mock(return_value="अनुवादित")
    monkeypatch.setattr(streamlit_app, "translate_items", translate_call)
    cache = {}

    first = streamlit_app._cached_translation("Translate me", "hi", (), cache)
    second = streamlit_app._cached_translation("Translate me", "hi", (), cache)

    assert first == second == "अनुवादित"
    translate_call.assert_called_once_with("Translate me", "hi", ())


def test_translated_display_reports_missing_hindi_provider(
    monkeypatch,
) -> None:
    monkeypatch.delenv("SCHEMESCOUT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SCHEMESCOUT_LLM_MODEL", raising=False)

    app = AppTest.from_file(str(APP_PATH)).run()
    app.selectbox(key="output_language").select("हिन्दी").run()

    assert not app.exception
    assert app.warning
    assert any(
        "यह केवल मार्गदर्शन है, कानूनी या वित्तीय सलाह नहीं।" in info.value
        for info in app.info
    )


def test_app_analyzes_selected_profile_and_shows_empty_scheme_state() -> None:
    app = AppTest.from_file(str(APP_PATH)).run()
    app.button[0].click().run()

    assert not app.exception
    assert any(
        "No relevant scheme definitions" in info.value
        for info in app.info
    )


def test_app_custom_profile_form_validates_and_waits_for_submit() -> None:
    app = AppTest.from_file(str(APP_PATH)).run()
    app.radio[0].set_value("Fill in a profile").run()

    assert not app.exception
    assert app.text_input[0].label == "Startup name"
    assert app.button
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["scheme_scout_profile"]["id"] == "custom-profile"


def test_document_review_does_not_apply_unconfirmed_fields() -> None:
    extracted = ExtractedField(
        field="name",
        value="New name",
        page=1,
        confidence="high",
        source_document="incorporation.pdf",
    )
    analysis = DocumentAnalysis(fields=[extracted])
    rows = document_review_rows(analysis)

    updated = apply_document_review(Profile(id="x", name="Original"), analysis, rows)

    assert rows[0]["Confirm"] is False
    assert updated.name == "Original"


def test_document_review_applies_checked_fields_with_table_type_conversion() -> None:
    extracted = ExtractedField(
        field="dpiit_recognized",
        value=True,
        page=1,
        confidence="high",
        source_document="dpiit.pdf",
    )
    analysis = DocumentAnalysis(fields=[extracted])
    rows = document_review_rows(analysis)
    rows[0]["Value"] = "False"
    rows[0]["Confirm"] = True

    updated = apply_document_review(Profile(id="x", name="Original"), analysis, rows)

    assert updated.dpiit_recognized is False


def test_document_review_requires_single_source_for_conflicting_field() -> None:
    first = ExtractedField(
        field="name",
        value="Name One",
        page=1,
        confidence="high",
        source_document="incorporation.pdf",
    )
    second = first.model_copy(
        update={"value": "Name Two", "source_document": "dpiit.pdf"}
    )
    analysis = DocumentAnalysis(
        fields=[first, second],
        conflicts=[
            DocumentConflict(
                field="name", values=[first, second], message="Documents disagree."
            )
        ],
    )
    rows = document_review_rows(analysis)
    rows[0]["Confirm"] = True
    rows[1]["Confirm"] = True

    with pytest.raises(ValueError, match="Select exactly one source row"):
        apply_document_review(Profile(id="x", name="Original"), analysis, rows)

    rows[1]["Confirm"] = False
    updated = apply_document_review(Profile(id="x", name="Original"), analysis, rows)
    assert updated.name == "Name One"


def test_document_analysis_ui_shows_temporary_storage_notice() -> None:
    app = AppTest.from_file(str(APP_PATH)).run()
    app.button[0].click().run()

    assert not app.exception
    assert any(
        "not retained" in caption.value.lower()
        for caption in app.caption
    )
