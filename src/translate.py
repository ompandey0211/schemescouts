import re
from collections.abc import Callable, Iterable

from src.extract import _call_llm

TranslationCall = Callable[[str], str]

SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
}
TRANSLATION_TARGETS = {
    "hi": "Hindi (हिन्दी)",
}
OFFICIAL_TERMS = ("DPIIT", "Startup India")
_PROTECTED_PATTERNS = (
    re.compile(r'"[^"\r\n]*"'),
    re.compile(r"\u201c[^\u201d\r\n]*\u201d"),
    re.compile(r"\b[\w.-]+\.(?:pdf|docx?|xlsx?|pptx?)\b", re.IGNORECASE),
    re.compile(r"\bDPIIT\b", re.IGNORECASE),
    re.compile(r"\b\d[\d,]*(?:\.\d+)?(?:[-/]\d[\d,]*)*\b"),
    re.compile(r"\b[A-Z][A-Z0-9&/-]{1,}\b"),
)
_PLACEHOLDER_TEMPLATE = "SCOUTPROTECTEDTOKEN{index}END"
_TOKEN_PATTERN = re.compile(r"SCOUTPROTECTEDTOKEN\d+END")


def _protect_text(
    text: str, protected_terms: Iterable[str]
) -> tuple[str, dict[str, str]]:
    tokens: dict[str, str] = {}
    protected_values = sorted(
        {term for term in (*OFFICIAL_TERMS, *protected_terms) if term},
        key=len,
        reverse=True,
    )

    def replace_match(match: re.Match[str]) -> str:
        token = _PLACEHOLDER_TEMPLATE.format(index=len(tokens))
        tokens[token] = match.group(0)
        return token

    def replace_outside_tokens(
        value: str,
        pattern: re.Pattern[str],
    ) -> str:
        chunks = _TOKEN_PATTERN.split(value)
        existing_tokens = _TOKEN_PATTERN.findall(value)
        protected_chunks = [
            pattern.sub(replace_match, chunk) for chunk in chunks
        ]
        combined: list[str] = []
        for index, chunk in enumerate(protected_chunks):
            combined.append(chunk)
            if index < len(existing_tokens):
                combined.append(existing_tokens[index])
        return "".join(combined)

    protected_text = text
    for pattern in _PROTECTED_PATTERNS:
        protected_text = replace_outside_tokens(protected_text, pattern)
    for term in protected_values:
        protected_text = replace_outside_tokens(
            protected_text,
            re.compile(
                re.escape(term),
                flags=re.IGNORECASE if term.casefold() == "dpiit" else 0,
            ),
        )
    return protected_text, tokens


def _restore_tokens(text: str, tokens: dict[str, str]) -> str:
    restored = text
    for token, original in tokens.items():
        restored = restored.replace(token, original)
    return restored


def translate(
    text: str,
    target_lang: str,
    *,
    llm_call: TranslationCall | None = None,
    protected_terms: Iterable[str] = (),
) -> str:
    """Translate user-facing text while preserving citations and official tokens."""
    if target_lang not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported language {target_lang!r}; choose one of "
            f"{', '.join(SUPPORTED_LANGUAGES)}."
        )
    if target_lang == "en" or not text:
        return text

    protected_text, tokens = _protect_text(text, protected_terms)
    if not protected_text.strip():
        return text
    prompt = (
        f"Translate the user-facing text into {TRANSLATION_TARGETS[target_lang]}. "
        "Keep every "
        "SCOUTPROTECTEDTOKEN...END token exactly unchanged, in the same order. "
        "Do not add facts, numbers, dates, citations, names, or official terms. "
        "Return only the translated text:\n"
        f"{protected_text}"
    )
    translated = (llm_call or _call_llm)(prompt)
    if not isinstance(translated, str):
        raise ValueError("Translation provider returned non-text output.")
    expected_tokens = re.findall(
        r"SCOUTPROTECTEDTOKEN\d+END", protected_text
    )
    translated_tokens = re.findall(
        r"SCOUTPROTECTEDTOKEN\d+END", translated
    )
    if translated_tokens != expected_tokens:
        raise ValueError(
            "Translation provider changed, omitted, duplicated, or reordered "
            "protected citation, number, date, or official-term tokens."
        )
    if re.search(r"\d", _TOKEN_PATTERN.sub("", translated)):
        raise ValueError("Translation provider added or changed numeric values.")
    return _restore_tokens(translated, tokens)


def translate_items(
    text: str,
    target_lang: str,
    protected_terms: Iterable[str] = (),
    *,
    llm_call: TranslationCall | None = None,
) -> str:
    """Translate a permitted display item while protecting named source content."""
    return translate(
        text,
        target_lang,
        llm_call=llm_call,
        protected_terms=protected_terms,
    )
