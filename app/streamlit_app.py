from __future__ import annotations

import logging
import os
from calendar import monthrange
from datetime import date
from io import BytesIO
from pathlib import Path

import streamlit as st
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.agent import (
    build_checklist_tool,
    evaluate_tool,
    extract_rules_tool,
    find_gaps_tool,
    find_relevant_schemes_tool,
    make_plan_tool,
)
from src.draft import generate_draft
from src.doc_analysis import (
    DOCUMENT_TYPE_LABELS,
    DocumentAnalysis,
    ExtractedField,
    UploadedDocument,
    apply_confirmed_fields,
    _validate_field_value,
    analyze_documents,
)
from src.evaluate import overall_verdict
from src.ingest import load_profiles, load_schemes
from src.models import Profile, Scheme
from src.plan import ChecklistItem, PlanStep, RequirementGap
from src.translate import translate_items

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "data" / "profiles"
SCHEMES_DIR = ROOT / "data" / "schemes"
logger = logging.getLogger(__name__)


def _cached_translation(
    text: str,
    target_lang: str,
    protected_terms: tuple[str, ...],
    cache: dict[tuple[str, str, tuple[str, ...]], str],
) -> str:
    """Cache translations to avoid repeated provider calls during UI reruns."""
    cache_key = (text, target_lang, protected_terms)
    if cache_key not in cache:
        cache[cache_key] = translate_items(text, target_lang, protected_terms)
    return cache[cache_key]


def _translated_display(
    text: str, target_lang: str, protected_terms: tuple[str, ...] = ()
) -> str:
    """Translate an allowed display string and visibly report provider failures."""
    if target_lang == "en":
        return text
    if not (
        os.environ.get("SCHEMESCOUT_LLM_BASE_URL")
        and os.environ.get("SCHEMESCOUT_LLM_MODEL")
    ):
        if "translation_configuration_warning" not in st.session_state:
            st.warning(
                "Hindi translation needs SCHEMESCOUT_LLM_BASE_URL and "
                "SCHEMESCOUT_LLM_MODEL; untranslated English is shown until configured."
            )
            st.session_state["translation_configuration_warning"] = True
        return text
    try:
        cache = st.session_state.setdefault("translation_cache", {})
        return _cached_translation(text, target_lang, protected_terms, cache)
    except Exception as exc:
        logger.exception("Could not translate user-facing text.")
        st.error(f"Translation unavailable: {exc}")
        return text


def rank_relevant_schemes(profile: Profile, schemes: list[Scheme]) -> list[Scheme]:
    """Filter by target metadata and rank the most specifically matched first."""
    relevant = find_relevant_schemes_tool(profile, schemes)
    return sorted(
        relevant,
        key=lambda scheme: (
            -scheme_match_score(scheme),
            scheme.name.casefold(),
        ),
    )


def scheme_match_score(scheme: Scheme) -> int:
    """Score explicit target dimensions for relevant-scheme ranking."""
    return sum(bool(targets) for targets in (scheme.sectors, scheme.stages, scheme.states))


def verification_is_stale(
    last_verified_date: date | None, today: date | None = None
) -> bool:
    """Treat verification as stale only when it is strictly older than six months."""
    if last_verified_date is None:
        return False
    current_date = today or date.today()
    cutoff_month = current_date.month - 6
    cutoff_year = current_date.year
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1
    cutoff = date(
        cutoff_year,
        cutoff_month,
        min(current_date.day, monthrange(cutoff_year, cutoff_month)[1]),
    )
    return last_verified_date < cutoff


def render_scheme_verification(scheme: Scheme) -> None:
    """Show a scheme's verification date and flag stale source reviews."""
    verified_date = (
        scheme.last_verified_date.isoformat()
        if scheme.last_verified_date
        else "date unavailable"
    )
    st.caption(f"Last verified: {verified_date}")
    if verification_is_stale(scheme.last_verified_date):
        st.warning(f"{scheme.name} has not been verified in over six months.")


def plan_markdown(
    profile: Profile,
    scheme: Scheme,
    verdict: str,
    gaps: list[RequirementGap],
    checklist: list[ChecklistItem],
    plan: list[PlanStep],
    target_lang: str = "en",
) -> str:
    """Render the selected scheme's actionable application plan as Markdown."""
    protected_terms = [scheme.name]
    lines = [
        f"# SchemeScout application plan: {scheme.name}",
        "",
        f"**Applicant:** {profile.name}",
        f"**Assessment:** {_translated_display(verdict, target_lang, tuple(protected_terms))}",
        "",
        "## Missing requirements",
    ]
    if gaps:
        for gap in gaps:
            clause_terms = [
                scheme.name,
                *[citation.exact_clause for citation in gap.citations],
            ]
            lines.append(
                f"- **{gap.status} — rule {gap.rule_id}:** {gap.requirement}"
            )
            lines.append(
                f"  - {_translated_display(gap.next_step, target_lang, tuple(clause_terms))}"
            )
    else:
        lines.append("- No unmet or unknown requirements were identified.")
    lines.extend(["", "## Document checklist"])
    if checklist:
        for item in checklist:
            protected_terms.extend([item.document, *[c.document for c in item.citations]])
            lines.append(
                f"- [ ] **{item.document}** — "
                f"{_translated_display(item.where_to_obtain, target_lang, tuple(protected_terms))} "
                f"(required by {', '.join(item.required_by)})"
            )
    else:
        lines.append("- No supporting documents were mapped from the scheme rules.")
    lines.extend(["", "## Dated action plan"])
    if plan:
        for step in plan:
            protected_terms.extend(
                [
                    step.rule_id,
                    *[citation.document for citation in step.citations],
                    *[citation.exact_clause for citation in step.citations],
                ]
            )
            lines.append(
                f"- **{step.date.isoformat()} · {step.priority.title()} · rule "
                f"{step.rule_id}:** "
                f"{_translated_display(step.action, target_lang, tuple(protected_terms))}"
            )
    else:
        lines.append("- No follow-up actions are needed based on the available rules.")
    lines.extend(
        [
            "",
            "---",
            "*Guidance only, not legal or financial advice.*",
            *(
                ["*यह केवल मार्गदर्शन है, कानूनी या वित्तीय सलाह नहीं।*"]
                if target_lang == "hi"
                else []
            ),
            "",
        ]
    )
    return "\n".join(lines)


def plan_pdf(markdown: str) -> bytes:
    """Create a simple searchable PDF for the Markdown application plan."""
    writer = PdfWriter()
    lines: list[str] = []
    for paragraph in markdown.splitlines():
        while len(paragraph) > 88:
            split_at = paragraph.rfind(" ", 0, 89)
            if split_at <= 0:
                split_at = 88
            lines.append(paragraph[:split_at])
            paragraph = paragraph[split_at:].lstrip()
        lines.append(paragraph)

    for start in range(0, max(len(lines), 1), 48):
        page = writer.add_blank_page(width=612, height=792)
        commands = ["BT", "/F1 10 Tf", "50 750 Td"]
        for index, line in enumerate(lines[start : start + 48]):
            escaped = line.encode("cp1252", errors="replace").decode("cp1252")
            escaped = escaped.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if index:
                commands.append("0 -14 Td")
            commands.append(f"({escaped}) Tj")
        commands.append("ET")
        content = DecodedStreamObject()
        content.set_data("\n".join(commands).encode("cp1252"))
        page[NameObject("/Contents")] = writer._add_object(content)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {
                        NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )
                    }
                )
            }
        )
    pdf_buffer = BytesIO()
    writer.write(pdf_buffer)
    return pdf_buffer.getvalue()


def run_analysis(profile: Profile, schemes: list[Scheme]) -> list[dict[str, object]]:
    """Run each relevant scheme through citation extraction and planning."""
    analyses: list[dict[str, object]] = []
    for scheme in rank_relevant_schemes(profile, schemes):
        try:
            extraction = extract_rules_tool(scheme, SCHEMES_DIR)
            evaluated_scheme = scheme.model_copy(update={"rules": extraction.rules})
            results = evaluate_tool(profile, evaluated_scheme)
            analyses.append(
                {
                    "scheme": evaluated_scheme,
                    "extraction": extraction,
                    "results": results,
                    "verdict": overall_verdict(results),
                    "gaps": find_gaps_tool(results),
                    "checklist": build_checklist_tool(evaluated_scheme, results),
                    "plan": make_plan_tool(profile, evaluated_scheme, results),
                }
            )
        except Exception as exc:
            logger.exception("Could not analyze scheme %s", scheme.id)
            analyses.append({"scheme": scheme, "error": str(exc)})
    return analyses


def document_review_rows(analysis: DocumentAnalysis) -> list[dict[str, object]]:
    """Build editable, provenance-aware rows with confirmation off by default."""
    conflict_fields = {conflict.field for conflict in analysis.conflicts}
    return [
        {
            "Field": extracted.field,
            "Value": str(extracted.value),
            "Page": extracted.page,
            "Confidence": extracted.confidence,
            "Source document": extracted.source_document,
            "Conflict": extracted.field in conflict_fields,
            "Confirm": False,
        }
        for extracted in analysis.fields
    ]


def _review_value(field: str, value: object) -> object:
    """Convert editable table text to the expected primitive field type."""
    if field == "dpiit_recognized" and isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes"}:
            return True
        if normalized in {"false", "no"}:
            return False
    if field == "annual_turnover_inr" and isinstance(value, str):
        normalized = value.replace(",", "").strip()
        if normalized.isdigit():
            return int(normalized)
    return value


def apply_document_review(
    profile: Profile,
    analysis: DocumentAnalysis,
    rows: list[dict[str, object]],
) -> Profile:
    """Apply only checked table rows after explicitly resolving conflicts."""
    original_fields = {
        (
            extracted.field,
            extracted.source_document,
            extracted.page,
        ): extracted
        for extracted in analysis.fields
    }
    selected: list[ExtractedField] = []
    selected_by_field: dict[str, int] = {}
    for row in rows:
        if not row.get("Confirm"):
            continue
        key = (
            str(row["Field"]),
            str(row["Source document"]),
            int(row["Page"]),
        )
        original = original_fields.get(key)
        if original is None:
            raise ValueError("The review table contains an unknown extracted value.")
        value = _validate_field_value(
            original.field,
            _review_value(original.field, row["Value"]),
        )
        if value is None:
            raise ValueError(f"Confirmed value for {original.field} cannot be empty.")
        selected.append(original.model_copy(update={"value": value}))
        selected_by_field[original.field] = selected_by_field.get(original.field, 0) + 1

    for conflict in analysis.conflicts:
        selected_count = selected_by_field.get(conflict.field, 0)
        if selected_count > 1:
            raise ValueError(
                f"Select exactly one source row to resolve the conflict for "
                f"{conflict.field}."
            )

    selected_analysis = DocumentAnalysis(
        fields=selected,
        missing_fields=analysis.missing_fields,
        conflicts=[],
    )
    return apply_confirmed_fields(
        profile,
        selected_analysis,
        [field for field, count in selected_by_field.items() if count == 1],
    )


def render_profile_form(profiles: list[Profile]) -> Profile | None:
    """Render sample-profile selection and a manual profile form."""
    mode = st.radio("Profile source", ["Sample profile", "Fill in a profile"], horizontal=True)
    if mode == "Sample profile":
        selected_name = st.selectbox(
            "Startup profile",
            options=[profile.name for profile in profiles],
            key="sample_profile",
        )
        selected = next(profile for profile in profiles if profile.name == selected_name)
        st.caption(
            f"{selected.sector or 'Sector not provided'} · "
            f"{selected.stage or 'Stage not provided'} · {selected.state or 'State not provided'}"
        )
        if st.button("Analyze profile", type="primary"):
            return selected
        return None

    default = profiles[0] if profiles else None
    with st.form("custom_profile_form"):
        st.subheader("Startup details")
        name = st.text_input("Startup name", value=default.name if default else "")
        sector = st.text_input("Sector", value=default.sector if default and default.sector else "")
        stage = st.text_input("Startup stage", value=default.stage if default and default.stage else "")
        state = st.text_input("State", value=default.state if default and default.state else "")
        entity_type = st.text_input(
            "Entity type",
            value=default.entity_type if default and default.entity_type else "",
        )
        incorporation_year = st.number_input(
            "Incorporation year",
            min_value=1800,
            max_value=date.today().year,
            value=default.incorporation_year if default and default.incorporation_year else 2024,
        )
        annual_turnover = st.number_input(
            "Annual turnover (INR; enter 0 if unknown)",
            min_value=0,
            value=default.annual_turnover_inr if default and default.annual_turnover_inr else 0,
        )
        employee_count = st.number_input(
            "Employee count",
            min_value=0,
            value=default.employee_count if default and default.employee_count else 0,
        )
        num_founders = st.number_input("Number of founders", min_value=0, value=0)
        funding_raised = st.number_input("Funding raised to date (INR)", min_value=0, value=0)
        funding_requirement = st.number_input(
            "Funding requested (INR)", min_value=0, value=0
        )
        use_of_funds = st.text_area("Proposed use of funds")
        declaration = st.text_area("Applicant declaration")
        women_led = st.selectbox("Women-led", ["Unknown", "Yes", "No"])
        dpiit_recognized = st.selectbox(
            "DPIIT recognized", ["Unknown", "Yes", "No"]
        )
        submitted = st.form_submit_button("Analyze profile", type="primary")
    if not submitted:
        return None
    if not name.strip():
        st.error("Enter a startup name.")
        return None
    return Profile(
        id="custom-profile",
        name=name.strip(),
        sector=sector.strip() or None,
        stage=stage.strip() or None,
        state=state.strip() or None,
        entity_type=entity_type.strip() or None,
        incorporation_year=int(incorporation_year) or None,
        annual_turnover_inr=int(annual_turnover) or None,
        employee_count=int(employee_count) or None,
        num_founders=int(num_founders),
        funding_raised_inr=int(funding_raised),
        funding_requirement_inr=int(funding_requirement),
        use_of_funds=use_of_funds.strip() or None,
        declaration=declaration.strip() or None,
        women_led={"Unknown": None, "Yes": True, "No": False}[women_led],
        dpiit_recognized={"Unknown": None, "Yes": True, "No": False}[
            dpiit_recognized
        ],
    )


st.set_page_config(page_title="SchemeScout", page_icon=":material/track_changes:", layout="wide")
st.markdown(
    """
    <style>
    html, body, [data-testid="stAppViewContainer"] {
        font-family: "Nirmala UI", "Noto Sans Devanagari", "Mangal", sans-serif;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("SchemeScout")
st.write("Discover government-scheme opportunities with evidence-linked eligibility guidance.")
st.warning("Guidance only, not legal or financial advice.")
target_language_label = st.selectbox(
    "Output language",
    options=["English", "हिन्दी"],
    key="output_language",
)
target_lang = "hi" if target_language_label == "हिन्दी" else "en"
if target_lang == "hi":
    st.info("यह केवल मार्गदर्शन है, कानूनी या वित्तीय सलाह नहीं।")
st.caption("Eligibility checks are deterministic; no conclusion is made without a source citation.")

try:
    profiles = load_profiles(PROFILES_DIR)
    schemes = load_schemes(SCHEMES_DIR)
except (OSError, ValueError) as exc:
    st.error(f"Could not load SchemeScout data: {exc}")
    st.stop()

if not profiles:
    st.info("Add sample startup profiles as JSON files in data/profiles.")
else:
    if not schemes:
        st.info(
            "No scheme definitions are available yet. Add official scheme PDFs "
            "and matching definitions under data/schemes."
        )
    selected_profile = render_profile_form(profiles)
    if selected_profile is not None:
        st.session_state["scheme_scout_profile"] = selected_profile.model_dump()
        with st.spinner("Finding relevant schemes and checking cited requirements..."):
            st.session_state["scheme_scout_analyses"] = run_analysis(
                selected_profile, schemes
            )

    profile_data = st.session_state.get("scheme_scout_profile")
    analyses = st.session_state.get("scheme_scout_analyses")
    if profile_data:
        profile = Profile.model_validate(profile_data)
        st.header("Auto-fill from documents")
        st.caption(
            "Uploaded PDFs are processed from temporary files and are not retained "
            "after document analysis/session cleanup."
        )
        upload_columns = st.columns(3)
        upload_specs = [
            ("dpiit_certificate", "DPIIT recognition certificate"),
            ("incorporation_certificate", "Certificate of incorporation"),
            ("financial_summary", "One-page financial summary"),
        ]
        uploaded_documents = []
        for column, (document_type, label) in zip(
            upload_columns, upload_specs, strict=True
        ):
            with column:
                uploaded_file = st.file_uploader(
                    label,
                    type=["pdf"],
                    key=f"upload_{document_type}",
                )
            if uploaded_file is not None:
                uploaded_documents.append(
                    UploadedDocument(
                        filename=uploaded_file.name,
                        document_type=document_type,
                        content=uploaded_file.getvalue(),
                    )
                )
        if st.button("Extract profile fields", key="extract_profile_documents"):
            if not uploaded_documents:
                st.warning("Upload at least one supported PDF.")
            else:
                try:
                    document_analysis = analyze_documents(uploaded_documents)
                    st.session_state["document_analysis"] = (
                        document_analysis.model_dump()
                    )
                except Exception as exc:
                    logger.exception("Could not analyze uploaded profile documents.")
                    st.error(f"Document analysis failed: {exc}")

        raw_document_analysis = st.session_state.get("document_analysis")
        if raw_document_analysis:
            document_analysis = DocumentAnalysis.model_validate(
                raw_document_analysis
            )
            for conflict in document_analysis.conflicts:
                st.warning(conflict.message)
                for value in conflict.values:
                    st.caption(
                        f"{conflict.field}: {value.value} — "
                        f"{value.source_document}, page {value.page} "
                        f"(confidence: {value.confidence})"
                    )
            if document_analysis.missing_fields:
                st.info(
                    "Not found (left empty): "
                    + ", ".join(document_analysis.missing_fields)
                )
            review_rows = document_review_rows(document_analysis)
            if review_rows:
                edited_rows = st.data_editor(
                    review_rows,
                    key="document_extraction_review",
                    hide_index=True,
                    disabled=[
                        "Field",
                        "Page",
                        "Confidence",
                        "Source document",
                        "Conflict",
                    ],
                    column_config={
                        "Value": st.column_config.TextColumn(
                            "Extracted value (editable)"
                        ),
                        "Confirm": st.column_config.CheckboxColumn(
                            "Confirm", default=False
                        ),
                    },
                )
                st.caption(
                    "Review and edit the values. Tick Confirm for each value to "
                    "apply; for conflicts, tick exactly one source row."
                )
                if st.button(
                    "Apply confirmed fields to profile",
                    key="apply_document_fields",
                ):
                    try:
                        updated_profile = apply_document_review(
                            profile, document_analysis, edited_rows.to_dict("records")
                        )
                        st.session_state["scheme_scout_profile"] = (
                            updated_profile.model_dump()
                        )
                        st.session_state["scheme_scout_analyses"] = run_analysis(
                            updated_profile, schemes
                        )
                        st.success(
                            "Confirmed fields were applied to the profile."
                        )
                        st.rerun()
                    except (ValueError, ValidationError) as exc:
                        st.error(f"Profile was not updated: {exc}")

    if profile_data and analyses is not None:
        profile = Profile.model_validate(profile_data)
        ranked = [
            analysis
            for analysis in analyses
            if "error" not in analysis
        ]
        st.header("Relevant schemes")
        if not analyses:
            st.info(
                "No relevant scheme definitions were found. Add official scheme "
                "documents and matching definitions under data/schemes."
            )
        for index, analysis in enumerate(analyses, start=1):
            scheme = analysis["scheme"]
            if "error" in analysis:
                st.warning(f"{index}. {scheme.name}: {analysis['error']}")
                render_scheme_verification(scheme)
                continue
            st.write(f"**{index}. {scheme.name}**")
            render_scheme_verification(scheme)
            target_dimensions = []
            if scheme.sectors:
                target_dimensions.append(f"Sector: {', '.join(scheme.sectors)}")
            if scheme.stages:
                target_dimensions.append(f"Stage: {', '.join(scheme.stages)}")
            if scheme.states:
                target_dimensions.append(f"State: {', '.join(scheme.states)}")
            st.caption(
                f"Match score {scheme_match_score(scheme)} · "
                f"{'; '.join(target_dimensions) or 'General scheme'}"
            )

        if ranked:
            selected_scheme_id = st.selectbox(
                "Selected scheme",
                options=[analysis["scheme"].id for analysis in ranked],
                format_func=lambda scheme_id: next(
                    analysis["scheme"].name
                    for analysis in ranked
                    if analysis["scheme"].id == scheme_id
                ),
            )
            selected = next(
                analysis
                for analysis in ranked
                if analysis["scheme"].id == selected_scheme_id
            )
            scheme = selected["scheme"]
            results = selected["results"]
            gaps = selected["gaps"]
            checklist = selected["checklist"]
            plan = selected["plan"]

            st.header("Eligibility rules")
            verdict_summary = {
                "eligible": "Eligible: all cited eligibility rules are met.",
                "not eligible": "Not eligible: at least one cited eligibility rule is not met.",
                "needs more info": "Needs more information: one or more rules are unknown or lack sufficient profile data.",
            }[selected["verdict"]]
            st.info(
                _translated_display(
                    verdict_summary,
                    target_lang,
                    (scheme.name,),
                )
            )
            for result in results:
                icon = {
                    "met": ":green[✓]",
                    "not_met": ":red[✗]",
                    "unknown": ":yellow[?]",
                }[result.status]
                st.markdown(
                    f"{icon} **{result.status.replace('_', ' ').title()}** · "
                    f"Rule `{result.rule_id}`"
                )
                st.caption(result.reason)
                with st.expander(f"Source clause for rule {result.rule_id}"):
                    if result.citations:
                        for citation in result.citations:
                            st.write(f'“{citation.exact_clause}”')
                            st.caption(f"{citation.document}, page {citation.page}")
                    else:
                        st.warning(
                            "No verified citation is available; this rule cannot "
                            "support an eligibility conclusion."
                        )
                    if result.action_if_unmet:
                        st.write(f"Next step: {result.action_if_unmet}")

            if selected["extraction"].manual_review:
                st.subheader("Manual review")
                for item in selected["extraction"].manual_review:
                    st.write(item.condition)
                    st.caption(
                        f'{item.citation.document}, page {item.citation.page}: '
                        f'“{item.citation.exact_clause}”'
                    )

            st.header("Missing requirements")
            if gaps:
                for gap in gaps:
                    st.write(
                        f"**Priority {gap.blocking_priority} · {gap.status.replace('_', ' ')} · "
                        f"rule `{gap.rule_id}`:** {gap.requirement}"
                    )
                    st.caption(
                        _translated_display(
                            gap.next_step,
                            target_lang,
                            (
                                scheme.name,
                                *[c.exact_clause for c in gap.citations],
                            ),
                        )
                    )
            else:
                st.success("No unmet or unknown requirements were identified.")

            st.header("Document checklist")
            if checklist:
                for item in checklist:
                    st.checkbox(
                        f"{item.document} — "
                        f"{_translated_display(item.where_to_obtain, target_lang, (item.document, *[c.document for c in item.citations]))}",
                        key=f"checklist_{scheme.id}_{item.document}",
                    )
                    st.caption(
                        f"Required by {', '.join(item.required_by)} · "
                        + "; ".join(
                            f"{citation.document}, p. {citation.page}"
                            for citation in item.citations
                        )
                    )
            else:
                st.info("No supporting documents could be mapped from these rules.")

            st.header("Dated action plan")
            if plan:
                for step in plan:
                    st.write(
                        f"**{step.date.isoformat()} · {step.priority.title()} · "
                        f"rule `{step.rule_id}`** — "
                        f"{_translated_display(step.action, target_lang, (scheme.name, step.rule_id, *[c.document for c in step.citations], *[c.exact_clause for c in step.citations]))}"
                    )
            else:
                st.info("No follow-up actions are needed based on the available rules.")
            markdown = plan_markdown(
                profile,
                scheme,
                selected["verdict"],
                gaps,
                checklist,
                plan,
                target_lang,
            )
            pdf_markdown = markdown
            if target_lang == "hi":
                st.caption(
                    "PDF export is in English; download the Markdown file for "
                    "Devanagari text."
                )
                pdf_markdown = plan_markdown(
                    profile,
                    scheme,
                    selected["verdict"],
                    gaps,
                    checklist,
                    plan,
                )
            with st.container(horizontal=True):
                st.download_button(
                    "Download plan as Markdown",
                    data=markdown,
                    file_name=f"{scheme.id}-application-plan.md",
                    mime="text/markdown",
                    key="download_plan_markdown",
                )
                st.download_button(
                    "Download plan as PDF (English)"
                    if target_lang == "hi"
                    else "Download plan as PDF",
                    data=plan_pdf(pdf_markdown),
                    file_name=f"{scheme.id}-application-plan.pdf",
                    mime="application/pdf",
                    key="download_plan_pdf",
                )

            st.header("Application draft")
            draft = generate_draft(profile, scheme, results)
            st.markdown(draft)
            st.download_button(
                "Download application draft",
                data=draft,
                file_name=f"{scheme.id}-application-draft.md",
                mime="text/markdown",
                key="download_application_draft",
            )
