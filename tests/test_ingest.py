import json
import logging
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src.ingest import (
    PDFPage,
    _load_models,
    load_pdf_pages,
    load_profiles,
    load_scheme_registry,
    load_schemes,
    scheme_pdf_path,
)
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


def test_empty_scheme_registry_is_valid_and_authoritative(tmp_path: Path) -> None:
    (tmp_path / "registry.json").write_text('{"schemes": []}', encoding="utf-8")
    (tmp_path / "legacy.json").write_text(
        Scheme(id="legacy", name="Legacy").model_dump_json(), encoding="utf-8"
    )

    assert load_scheme_registry(tmp_path).schemes == []
    assert load_schemes(tmp_path) == []
    assert load_schemes(DATA_DIR / "schemes") == []


def test_load_schemes_validates_registry_and_maps_metadata(tmp_path: Path) -> None:
    registry = {
        "schemes": [
            {
                "id": "registered-scheme",
                "name": "Registered Scheme",
                "authority": "Ministry",
                "source_url": "https://example.gov/scheme",
                "file_path": "data/schemes/source.pdf",
                "last_verified_date": "2026-10-01",
                "tags": {
                    "sector": ["agri-tech"],
                    "stage": ["early"],
                    "state": ["Karnataka"],
                },
            }
        ]
    }
    (tmp_path / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    scheme = load_schemes(tmp_path)[0]

    assert scheme.id == "registered-scheme"
    assert scheme.authority == "Ministry"
    assert scheme.source_url == "https://example.gov/scheme"
    assert scheme.file_path == "data/schemes/source.pdf"
    assert scheme.last_verified_date.isoformat() == "2026-10-01"
    assert scheme.sectors == ["agri-tech"]
    assert scheme.stages == ["early"]
    assert scheme.states == ["Karnataka"]


def test_registry_rejects_invalid_schema_and_duplicate_ids(tmp_path: Path) -> None:
    invalid_entry = {
        "schemes": [
            {
                "id": "invalid/id",
                "name": "Invalid",
                "authority": "Ministry",
                "source_url": "not-a-url",
                "file_path": "scheme.pdf",
                "last_verified_date": "not-a-date",
                "tags": {},
            }
        ]
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(invalid_entry), encoding="utf-8")

    with pytest.raises(ValueError):
        load_scheme_registry(tmp_path)

    entry = {
        "id": "duplicate",
        "name": "Duplicate",
        "authority": "Ministry",
        "source_url": "https://example.gov/scheme",
        "file_path": "scheme.pdf",
        "last_verified_date": "2026-10-01",
        "tags": {},
    }
    registry_path.write_text(json.dumps({"schemes": [entry, entry]}), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_scheme_registry(tmp_path)


def test_scheme_pdf_path_supports_registry_and_legacy_paths(tmp_path: Path) -> None:
    schemes_dir = tmp_path / "data" / "schemes"
    schemes_dir.mkdir(parents=True)
    project_pdf = tmp_path / "data" / "source.pdf"
    project_pdf.write_bytes(b"pdf")
    local_pdf = schemes_dir / "local.pdf"
    local_pdf.write_bytes(b"pdf")

    assert scheme_pdf_path(
        Scheme(id="project", name="Project", file_path="data/source.pdf"),
        schemes_dir,
    ) == project_pdf
    assert scheme_pdf_path(
        Scheme(id="local", name="Local", file_path="local.pdf"), schemes_dir
    ) == local_pdf
    assert scheme_pdf_path(Scheme(id="legacy", name="Legacy"), schemes_dir) == (
        schemes_dir / "legacy.pdf"
    )


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
