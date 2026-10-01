import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from src.models import Profile, Scheme

ModelT = TypeVar("ModelT", bound=BaseModel)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PDFPage:
    page_number: int
    text: str


class SchemeTags(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector: list[str] = Field(default_factory=list)
    stage: list[str] = Field(default_factory=list)
    state: list[str] = Field(default_factory=list)


class SchemeRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    source_url: HttpUrl
    file_path: str = Field(min_length=1)
    last_verified_date: date
    tags: SchemeTags = Field(default_factory=SchemeTags)


class SchemeRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemes: list[SchemeRegistryEntry] = Field(default_factory=list)


def _load_models(directory: Path, model_type: type[ModelT]) -> list[ModelT]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Data directory does not exist: {directory}")

    models: list[ModelT] = []
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(".rules.json"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        models.append(model_type.model_validate(data))
    return models


def load_profiles(directory: Path) -> list[Profile]:
    return _load_models(directory, Profile)


def load_scheme_registry(directory: Path) -> SchemeRegistry:
    """Load and validate the registry document in a scheme data directory."""
    registry_path = directory / "registry.json"
    if not registry_path.is_file():
        raise FileNotFoundError(f"Scheme registry does not exist: {registry_path}")
    registry = SchemeRegistry.model_validate_json(
        registry_path.read_text(encoding="utf-8")
    )
    ids = [scheme.id for scheme in registry.schemes]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Scheme IDs must be unique in {registry_path}.")
    return registry


def load_schemes(directory: Path) -> list[Scheme]:
    registry_path = directory / "registry.json"
    if registry_path.is_file():
        registry = load_scheme_registry(directory)
        return [
            Scheme(
                id=entry.id,
                name=entry.name,
                authority=entry.authority,
                source_url=str(entry.source_url),
                file_path=entry.file_path,
                last_verified_date=entry.last_verified_date,
                sectors=entry.tags.sector,
                stages=entry.tags.stage,
                states=entry.tags.state,
            )
            for entry in registry.schemes
        ]
    return _load_models(directory, Scheme)


def scheme_pdf_path(scheme: Scheme, schemes_dir: Path) -> Path:
    """Resolve the registry's project-relative PDF path or legacy <id>.pdf path."""
    if not scheme.file_path:
        return schemes_dir / f"{scheme.id}.pdf"
    configured_path = Path(scheme.file_path)
    if configured_path.is_absolute():
        return configured_path
    project_root = schemes_dir.resolve().parent.parent
    project_relative_path = project_root / configured_path
    if project_relative_path.is_file():
        return project_relative_path
    return schemes_dir / configured_path


def load_pdf_pages(path: Path) -> list[PDFPage]:
    """Extract cleaned text from a PDF while retaining its one-based page numbers."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[PDFPage] = []
    for page_number, page in enumerate(reader.pages, start=1):
        extracted_text = page.extract_text()
        cleaned_text = re.sub(r"\s+", " ", extracted_text or "").strip()
        if not cleaned_text:
            logger.warning(
                "No text extracted from PDF page %s in %s; it may be scanned or empty.",
                page_number,
                path,
            )
        pages.append(PDFPage(page_number=page_number, text=cleaned_text))
    return pages