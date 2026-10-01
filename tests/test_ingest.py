import json
import logging
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.ingest import PDFPage, _load_models, load_pdf_pages, load_profiles, load_schemes
from src.models import Profile, Scheme

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _write_sample_pdf(path: Path, page_texts: list[str | None]) -> None:
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if text is not None:
            content = DecodedStreamObject()
            escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped_text}) Tj ET".encode())
            page[NameObject("/Contents")] = writer._add_object(content)
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): DictionaryObject(
                            {
                                NameObject("/Type"): NameObject("/Font"),
                                NameObject("/Subtype"): NameObject("/Type1"),
                                NameObject("/BaseFont"): NameObject("/Helvetica"),
                            }
                        )}
                    )
                }
            )
    with path.open("wb") as pdf_file:
        writer.write(pdf_file)


def test_load_models_reads_profile_models_from_json(tmp_path: Path) -> None:
    profile = load_profiles(DATA_DIR / "profiles")[0]
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(profile.model_dump_json(), encoding="utf-8")

    loaded = _load_models(tmp_path, Profile)

    assert loaded == [profile]


def test_load_profiles_validates_diverse_sample_profiles() -> None:
    profiles = load_profiles(DATA_DIR / "profiles")

    assert len(profiles) == 3
    assert len({profile.sector for profile in profiles}) == 3
    assert profiles[0].annual_turnover_inr is not None
    assert profiles[2].annual_turnover_inr is None
    assert profiles[2].women_led is None


def test_load_schemes_loads_models_from_json(tmp_path: Path) -> None:
    citation = {
        "document": "test source",
        "page": 1,
        "exact_clause": "test clause",
    }
    scheme = Scheme(
        id="test-scheme",
        name="Test Scheme",
        rules=[
            {
                "id": "test-rule",
                "field": "sector",
                "operator": "equals",
                "value": "agri-tech",
                "citations": [citation],
            }
        ],
    )
    (tmp_path / "scheme.json").write_text(scheme.model_dump_json(), encoding="utf-8")

    assert load_schemes(tmp_path) == [scheme]


def test_load_profiles_rejects_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_profiles(tmp_path / "missing")


def test_load_schemes_returns_empty_for_empty_directory(tmp_path: Path) -> None:
    assert load_schemes(tmp_path) == []


def test_load_models_surfaces_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "invalid.json").write_text("{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        _load_models(tmp_path, Profile)


def test_load_pdf_pages_extracts_clean_text_with_page_numbers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _write_sample_pdf(pdf_path, ["  First\n page   text  ", "Second page text"])

    pages = load_pdf_pages(pdf_path)

    assert pages == [
        PDFPage(page_number=1, text="First page text"),
        PDFPage(page_number=2, text="Second page text"),
    ]


def test_load_pdf_pages_keeps_empty_scanned_page_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _write_sample_pdf(pdf_path, ["Readable page", None, ""])

    with caplog.at_level(logging.WARNING, logger="src.ingest"):
        pages = load_pdf_pages(pdf_path)

    assert pages == [
        PDFPage(page_number=1, text="Readable page"),
        PDFPage(page_number=2, text=""),
        PDFPage(page_number=3, text=""),
    ]
    assert "page 2" in caplog.text
    assert "page 3" in caplog.text
