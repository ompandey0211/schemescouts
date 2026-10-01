import json
import logging
import tempfile
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from src.extract import _call_llm
from src.ingest import PDFPage, load_pdf_pages
from src.models import Profile

logger = logging.getLogger(__name__)

DocumentType = Literal[
    "dpiit_certificate",
    "incorporation_certificate",
    "financial_summary",
]
ExtractableField = Literal[
    "name",
    "entity_type",
    "incorporation_date",
    "dpiit_recognized",
    "state",
    "annual_turnover_inr",
]
Confidence = Literal["high", "medium", "low"]
LLMCall = Callable[[str], str]

FIELD_TYPES: dict[str, type] = {
    "name": str,
    "entity_type": str,
    "incorporation_date": str,
    "dpiit_recognized": bool,
    "state": str,
    "annual_turnover_inr": int,
}
DOCUMENT_TYPE_LABELS: dict[str, str] = {
    "dpiit_certificate": "DPIIT recognition certificate",
    "incorporation_certificate": "certificate of incorporation",
    "financial_summary": "one-page financial summary",
}


class UploadedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    document_type: DocumentType
    content: bytes = Field(min_length=1)


class ExtractedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: ExtractableField
    value: Any
    page: int = Field(ge=1)
    confidence: Confidence
    source_document: str = Field(min_length=1)


class DocumentConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: ExtractableField
    values: list[ExtractedField] = Field(min_length=2)
    message: str


class DocumentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[ExtractedField] = Field(default_factory=list)
    missing_fields: list[ExtractableField] = Field(default_factory=list)
    conflicts: list[DocumentConflict] = Field(default_factory=list)


def _validate_field_value(field: str, value: Any) -> Any:
    if field not in FIELD_TYPES:
        raise ValueError(f"Unsupported extracted profile field: {field}")
    if value is None:
        return None
    annotation = FIELD_TYPES[field]
    if field == "incorporation_date":
        parsed = TypeAdapter(Profile.model_fields[field].annotation).validate_python(
            value
        )
        return parsed.isoformat()
    if annotation is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be true, false, or null.")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer or null.")
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string or null.")
    return value.strip()


def _build_prompt(document: UploadedDocument, pages: list[PDFPage]) -> str:
    page_texts = [
        {"page": page.page_number, "text": page.text} for page in pages
    ]
    return (
        "Extract only profile values explicitly stated in this uploaded official "
        "startup document. Return valid JSON with exactly a fields array. Each "
        "item has field, value, page, confidence. Supported fields: name "
        "(string), entity_type (string), incorporation_date (ISO date string), "
        "dpiit_recognized (boolean), state (string), annual_turnover_inr "
        "(non-negative integer in INR). Use null for a value not explicitly "
        "present and omit that field from the array. Every included value needs "
        "the one-based source page and confidence high, medium, or low. Never "
        "infer, calculate, or guess values. This document is classified as "
        f"{DOCUMENT_TYPE_LABELS[document.document_type]}. "
        f"Filename: {json.dumps(document.filename)}. Page text:\n"
        f"{json.dumps(page_texts, ensure_ascii=False)}"
    )


def _parse_response(
    response_text: str, document: UploadedDocument, pages: list[PDFPage]
) -> list[ExtractedField]:
    payload = json.loads(response_text)
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list):
        raise ValueError("Document extraction must return a JSON object with fields.")
    page_numbers = {page.page_number for page in pages}
    results: list[ExtractedField] = []
    seen: set[str] = set()
    for item in payload["fields"]:
        if not isinstance(item, dict):
            raise ValueError("Each extracted field must be a JSON object.")
        field = item.get("field")
        if field not in FIELD_TYPES:
            logger.warning("Ignoring unsupported extracted field %r.", field)
            continue
        if field in seen:
            raise ValueError(f"LLM returned duplicate values for {field}.")
        seen.add(field)
        value = _validate_field_value(field, item.get("value"))
        if value is None:
            continue
        page = item.get("page")
        if page not in page_numbers:
            raise ValueError(
                f"Extracted value for {field} refers to a page not in the document."
            )
        results.append(
            ExtractedField(
                field=field,
                value=value,
                page=page,
                confidence=item.get("confidence"),
                source_document=document.filename,
            )
        )
    return results


def find_conflicts(fields: Sequence[ExtractedField]) -> list[DocumentConflict]:
    """Flag fields with distinct values asserted by different uploaded documents."""
    by_field: dict[str, list[ExtractedField]] = defaultdict(list)
    for extracted in fields:
        by_field[extracted.field].append(extracted)
    conflicts: list[DocumentConflict] = []
    for field, assertions in by_field.items():
        by_value: dict[str, list[ExtractedField]] = defaultdict(list)
        for assertion in assertions:
            value_key = json.dumps(assertion.value, sort_keys=True, default=str)
            by_value[value_key].append(assertion)
        if len(by_value) < 2:
            continue
        conflicts.append(
            DocumentConflict(
                field=field,
                values=assertions,
                message=(
                    f"Uploaded documents contain conflicting values for {field}; "
                    "review the sources before applying a value."
                ),
            )
        )
    return conflicts


def analyze_documents(
    documents: Sequence[UploadedDocument],
    *,
    llm_call: LLMCall | None = None,
) -> DocumentAnalysis:
    """Extract profile fields from uploaded PDFs without retaining their files."""
    extracted_fields: list[ExtractedField] = []
    required_fields = list(FIELD_TYPES)
    for document in documents:
        if Path(document.filename).suffix.lower() != ".pdf":
            raise ValueError(f"Uploaded document must be a PDF: {document.filename}")
        with tempfile.TemporaryDirectory(prefix="schemescout-doc-") as temporary_dir:
            pdf_path = Path(temporary_dir) / Path(document.filename).name
            pdf_path.write_bytes(document.content)
            pages = load_pdf_pages(pdf_path)
            prompt = _build_prompt(document, pages)
            response = (
                llm_call(prompt)
                if llm_call is not None
                else _call_llm(prompt, json_mode=True)
            )
        extracted_fields.extend(_parse_response(response, document, pages))

    conflicts = find_conflicts(extracted_fields)
    distinct_fields = {
        field.field for field in extracted_fields
    }
    missing_fields = [
        field for field in required_fields if field not in distinct_fields
    ]
    return DocumentAnalysis(
        fields=extracted_fields,
        missing_fields=missing_fields,
        conflicts=conflicts,
    )


def apply_confirmed_fields(
    profile: Profile,
    analysis: DocumentAnalysis,
    confirmed_fields: Sequence[str],
) -> Profile:
    """Apply only explicitly confirmed, non-conflicting extracted fields."""
    conflicts = {conflict.field for conflict in analysis.conflicts}
    fields_by_name: dict[str, list[ExtractedField]] = defaultdict(list)
    for extracted in analysis.fields:
        fields_by_name[extracted.field].append(extracted)
    updates: dict[str, Any] = {}
    for field in confirmed_fields:
        if field not in FIELD_TYPES:
            raise ValueError(f"Unsupported profile field: {field}")
        if field in conflicts:
            raise ValueError(f"Resolve the conflicting documents for {field} first.")
        assertions = fields_by_name.get(field, [])
        if not assertions:
            raise ValueError(f"No extracted value is available for {field}.")
        values = {json.dumps(item.value, sort_keys=True, default=str) for item in assertions}
        if len(values) != 1:
            raise ValueError(f"Resolve the conflicting documents for {field} first.")
        updates[field] = assertions[0].value
    try:
        return Profile.model_validate({**profile.model_dump(), **updates})
    except ValidationError as exc:
        raise ValueError(f"Confirmed values do not fit the profile: {exc}") from exc
