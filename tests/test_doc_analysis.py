import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.doc_analysis import (
    DocumentAnalysis,
    UploadedDocument,
    _build_prompt,
    _parse_response,
    _validate_field_value,
    analyze_documents,
    apply_confirmed_fields,
    find_conflicts,
)
from src.ingest import PDFPage
from src.models import Profile


def _pdf_bytes(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    content = DecodedStreamObject()
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped_text}) Tj ET".encode())
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
    buffer = __import__("io").BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _document(
    filename: str,
    document_type: str = "incorporation_certificate",
) -> UploadedDocument:
    return UploadedDocument(
        filename=filename,
        document_type=document_type,
        content=_pdf_bytes("Greenfield Labs is registered in Karnataka."),
    )


def _response(fields: list[dict[str, object]]) -> str:
    return json.dumps({"fields": fields})


def test_validate_field_value_validates_supported_profile_types() -> None:
    assert _validate_field_value("name", " Greenfield Labs ") == "Greenfield Labs"
    assert _validate_field_value("annual_turnover_inr", 125000) == 125000
    assert _validate_field_value("dpiit_recognized", False) is False
    assert _validate_field_value("incorporation_date", "2021-05-17") == "2021-05-17"
    assert _validate_field_value("name", None) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dpiit_recognized", "yes"),
        ("annual_turnover_inr", -1),
        ("annual_turnover_inr", True),
        ("state", ""),
        ("sector", "unsupported"),
    ],
)
def test_validate_field_value_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _validate_field_value(field, value)


def test_build_prompt_includes_document_type_and_page_text() -> None:
    prompt = _build_prompt(
        _document("certificate.pdf"),
        [PDFPage(page_number=2, text="Registered in Karnataka.")],
    )

    assert "certificate of incorporation" in prompt
    assert '"page": 2' in prompt
    assert "Registered in Karnataka." in prompt
    assert "Never infer" in prompt


def test_parse_response_preserves_page_confidence_and_source() -> None:
    document = _document("incorporation.pdf")
    parsed = _parse_response(
        _response(
            [
                {
                    "field": "name",
                    "value": "Greenfield Labs",
                    "page": 1,
                    "confidence": "high",
                },
                {
                    "field": "incorporation_date",
                    "value": "2021-05-17",
                    "page": 1,
                    "confidence": "medium",
                },
            ]
        ),
        document,
        [PDFPage(page_number=1, text="Company facts.")],
    )

    assert parsed[0].source_document == "incorporation.pdf"
    assert parsed[0].page == 1
    assert parsed[0].confidence == "high"
    assert parsed[1].value == "2021-05-17"


def test_parse_response_omits_explicit_null_values() -> None:
    parsed = _parse_response(
        _response(
            [
                {
                    "field": "state",
                    "value": None,
                    "page": 1,
                    "confidence": "low",
                }
            ]
        ),
        _document("financial.pdf", "financial_summary"),
        [PDFPage(page_number=1, text="Revenue summary.")],
    )

    assert parsed == []


def test_parse_response_rejects_invalid_page_and_duplicate_field() -> None:
    document = _document("certificate.pdf")
    page = [PDFPage(page_number=1, text="Company details.")]
    invalid_page = _response(
        [
            {
                "field": "name",
                "value": "Name",
                "page": 2,
                "confidence": "high",
            }
        ]
    )
    with pytest.raises(ValueError, match="page not in"):
        _parse_response(invalid_page, document, page)

    duplicate = _response(
        [
            {"field": "name", "value": "A", "page": 1, "confidence": "high"},
            {"field": "name", "value": "B", "page": 1, "confidence": "low"},
        ]
    )
    with pytest.raises(ValueError, match="duplicate"):
        _parse_response(duplicate, document, page)


def test_find_conflicts_flags_distinct_document_values() -> None:
    call = Mock()
    response1 = _response(
        [{"field": "name", "value": "Greenfield Labs", "page": 1, "confidence": "high"}]
    )
    response2 = _response(
        [{"field": "name", "value": "Greenfield Limited", "page": 1, "confidence": "medium"}]
    )
    call.side_effect = [response1, response2]

    analysis = analyze_documents(
        [
            _document("incorporation.pdf"),
            _document("dpiit.pdf", "dpiit_certificate"),
        ],
        llm_call=call,
    )

    assert len(analysis.conflicts) == 1
    assert analysis.conflicts[0].field == "name"
    assert [value.value for value in analysis.conflicts[0].values] == [
        "Greenfield Labs",
        "Greenfield Limited",
    ]
    assert "conflicting values" in analysis.conflicts[0].message


def test_analyze_documents_marks_missing_fields_without_guessing() -> None:
    response = _response(
        [
            {
                "field": "state",
                "value": "Karnataka",
                "page": 1,
                "confidence": "high",
            }
        ]
    )

    analysis = analyze_documents(
        [_document("financial.pdf", "financial_summary")],
        llm_call=Mock(return_value=response),
    )

    assert [field.field for field in analysis.fields] == ["state"]
    assert set(analysis.missing_fields) == {
        "name",
        "entity_type",
        "incorporation_date",
        "dpiit_recognized",
        "annual_turnover_inr",
    }


def test_analyze_documents_reads_pdf_and_deletes_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.doc_analysis as doc_analysis

    original_loader = doc_analysis.load_pdf_pages
    observed_paths: list[Path] = []

    def record_path(path: Path) -> list[PDFPage]:
        observed_paths.append(path)
        return original_loader(path)

    monkeypatch.setattr(doc_analysis, "load_pdf_pages", record_path)
    response = _response(
        [{"field": "state", "value": "Karnataka", "page": 1, "confidence": "high"}]
    )

    analyze_documents(
        [_document("upload.pdf")],
        llm_call=Mock(return_value=response),
    )

    assert len(observed_paths) == 1
    assert not observed_paths[0].exists()


def test_analyze_documents_rejects_non_pdf_upload() -> None:
    document = UploadedDocument(
        filename="notes.txt",
        document_type="financial_summary",
        content=b"not a pdf",
    )

    with pytest.raises(ValueError, match="must be a PDF"):
        analyze_documents([document], llm_call=Mock())


def test_apply_confirmed_fields_changes_only_confirmed_values() -> None:
    profile = Profile(id="startup-1", name="Old name")
    document = _document("incorporation.pdf")
    from src.doc_analysis import ExtractedField

    analysis = DocumentAnalysis(
        fields=[
            ExtractedField(
                field="name",
                value="New name",
                page=1,
                confidence="high",
                source_document=document.filename,
            ),
            ExtractedField(
                field="state",
                value="Karnataka",
                page=1,
                confidence="high",
                source_document=document.filename,
            ),
        ]
    )

    updated = apply_confirmed_fields(profile, analysis, ["name"])

    assert updated.name == "New name"
    assert updated.state is None


def test_apply_confirmed_fields_rejects_conflicting_values() -> None:
    document = _document("incorporation.pdf")
    from src.doc_analysis import DocumentConflict, ExtractedField

    one = ExtractedField(
        field="name",
        value="Name One",
        page=1,
        confidence="high",
        source_document=document.filename,
    )
    two = one.model_copy(update={"value": "Name Two", "source_document": "dpiit.pdf"})
    analysis = DocumentAnalysis(
        fields=[one, two],
        conflicts=[
            DocumentConflict(field="name", values=[one, two], message="Conflict.")
        ],
    )

    with pytest.raises(ValueError, match="conflicting documents"):
        apply_confirmed_fields(Profile(id="x", name="Original"), analysis, ["name"])
