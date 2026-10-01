import hashlib
import json
import logging
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from src.config import get_llm_settings
from src.ingest import PDFPage
from src.models import Citation, Profile, Rule

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "schemescout" / "rule-cache"
LLMCall = Callable[[str], str]


class ManualReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1)
    citation: Citation


class RuleExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[Rule] = Field(default_factory=list)
    manual_review: list[ManualReviewItem] = Field(default_factory=list)


class _CachedExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_digest: str
    extraction: RuleExtraction


def extract_pdf_pages(path: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [
        (page_number, page.extract_text() or "")
        for page_number, page in enumerate(reader.pages, start=1)
    ]


def _normalise_clause(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _source_digest(pages: list[PDFPage], document: str) -> str:
    source = json.dumps(
        {
            "document": document,
            "pages": [
                {"page_number": page.page_number, "text": page.text}
                for page in pages
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _cache_path(scheme_id: str, cache_dir: Path | None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", scheme_id):
        raise ValueError("scheme_id may contain only letters, numbers, '_' and '-'.")
    return (cache_dir or DEFAULT_CACHE_DIR) / f"{scheme_id}.rules.json"


def _build_prompt(pages: list[PDFPage], document: str) -> str:
    profile_fields = sorted(Profile.model_fields)
    source_pages = [
        {"page_number": page.page_number, "text": page.text} for page in pages
    ]
    return (
        "Extract eligibility conditions from the supplied official document pages. "
        "Return only a JSON object with exactly two keys: rules and manual_review. "
        "Each rule must match this shape: "
        '{"id": string, "field": Profile field, "operator": one of '
        '"equals", "not_equals", "greater_than", "greater_than_or_equal", '
        '"less_than", "less_than_or_equal", "in", "not_in", "contains", '
        '"value": JSON value, "citations": [{"document": document name, '
        '"page": one-based page number, "exact_clause": verbatim quote}]} . '
        "Use only these Profile fields: "
        f"{json.dumps(profile_fields)}. Conditions that cannot be expressed "
        "using those fields must go in manual_review as objects with condition "
        'and citation keys, where citation has document, page, and exact_clause. '
        "Do not invent conditions or paraphrase quoted clauses. Every condition "
        "must cite the page and quote its exact text. "
        f"Use {json.dumps(document)} as the document name. Pages follow as JSON:\n"
        f"{json.dumps(source_pages, ensure_ascii=False)}"
    )


def _call_llm(prompt: str, *, json_mode: bool = False) -> str:
    settings = get_llm_settings()
    base_url = settings.base_url
    model = settings.model
    if not base_url or not model:
        raise RuntimeError(
            "Set SCHEMESCOUT_LLM_BASE_URL (or LLM_PROVIDER) and "
            "SCHEMESCOUT_LLM_MODEL (or LLM_MODEL) to configure an "
            "OpenAI-compatible chat-completions provider."
        )

    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    request_body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON matching the requested schema.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    if json_mode:
        request_body["response_format"] = {"type": "json_object"}
    payload = json.dumps(request_body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = settings.api_key
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(endpoint, data=payload, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        response_data = json.loads(response.read().decode("utf-8"))
    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM provider returned no chat-completion content.") from exc
    if not isinstance(content, str):
        raise ValueError("LLM provider returned non-text chat-completion content.")
    return content


def _parse_extraction(
    response_text: str, pages: list[PDFPage], document: str
) -> RuleExtraction:
    response = json.loads(response_text)
    if not isinstance(response, dict):
        raise ValueError("LLM extraction response must be a JSON object.")
    page_text = {page.page_number: page.text for page in pages}
    verified_rules: list[Rule] = []
    verified_manual: list[ManualReviewItem] = []
    manual_review: list[ManualReviewItem] = []

    for item in response.get("manual_review", []):
        if not isinstance(item, dict):
            raise ValueError("Each manual_review item must be a JSON object.")
        item = {**item, "citation": {**item.get("citation", {}), "document": document}}
        manual_review.append(ManualReviewItem.model_validate(item))

    for rule_data in response.get("rules", []):
        if not isinstance(rule_data, dict):
            raise ValueError("Each rule must be a JSON object.")
        field = rule_data.get("field")
        if field not in Profile.model_fields:
            citations = rule_data.get("citations", [])
            if citations:
                citation = {**citations[0], "document": document}
                manual_review.append(
                    ManualReviewItem(
                        condition=f"Unmappable condition for field {field!r}",
                        citation=Citation.model_validate(citation),
                    )
                )
            else:
                logger.warning("Skipping unmappable condition without a citation.")
            continue

        citations = [
            Citation.model_validate({**citation, "document": document})
            for citation in rule_data.get("citations", [])
        ]
        verified_citations = [
            citation
            for citation in citations
            if citation.page in page_text
            and _normalise_clause(citation.exact_clause)
            and _normalise_clause(citation.exact_clause)
            in _normalise_clause(page_text[citation.page])
        ]
        if not verified_citations:
            logger.warning(
                "Dropping rule %r because no quoted clause matches its cited page.",
                rule_data.get("id", "<unknown>"),
            )
            continue
        verified_rules.append(
            Rule.model_validate({**rule_data, "citations": verified_citations})
        )

    for item in manual_review:
        citation = item.citation
        if (
            citation.page in page_text
            and _normalise_clause(citation.exact_clause)
            and _normalise_clause(citation.exact_clause)
            in _normalise_clause(page_text[citation.page])
        ):
            verified_manual.append(item)
        else:
            logger.warning(
                "Dropping manual-review condition because its quoted clause "
                "does not match the cited page."
            )
    return RuleExtraction(rules=verified_rules, manual_review=verified_manual)


def extract_rules(
    pages: list[PDFPage],
    scheme_id: str,
    document: str,
    *,
    cache_dir: Path | None = None,
    llm_call: LLMCall | None = None,
) -> RuleExtraction:
    """Extract citation-verified rules, using a source-aware cache when available."""
    if not document.strip():
        raise ValueError("document must not be empty.")
    destination = _cache_path(scheme_id, cache_dir)
    digest = _source_digest(pages, document)
    if destination.is_file():
        cached = _CachedExtraction.model_validate_json(
            destination.read_text(encoding="utf-8")
        )
        if cached.source_digest == digest:
            return cached.extraction

    prompt = _build_prompt(pages, document)
    response_text = (llm_call or _call_llm)(prompt)
    extraction = _parse_extraction(response_text, pages, document)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        _CachedExtraction(source_digest=digest, extraction=extraction).model_dump_json(
            indent=2
        ),
        encoding="utf-8",
    )
    return extraction
