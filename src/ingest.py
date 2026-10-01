import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from src.models import Profile, Scheme

ModelT = TypeVar("ModelT", bound=BaseModel)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PDFPage:
    page_number: int
    text: str


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


def load_schemes(directory: Path) -> list[Scheme]:
    return _load_models(directory, Scheme)


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