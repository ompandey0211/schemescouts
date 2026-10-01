import re

import pytest

from src.translate import (
    OFFICIAL_TERMS,
    SUPPORTED_LANGUAGES,
    _protect_text,
    _restore_tokens,
    translate,
    translate_items,
)


def test_supported_languages_are_configured_for_future_expansion() -> None:
    assert SUPPORTED_LANGUAGES == {"en": "English", "hi": "Hindi"}
    assert "Startup India" in OFFICIAL_TERMS


def test_protect_text_and_restore_explicit_terms_and_citations() -> None:
    citation_clause = "Applicants must hold DPIIT recognition."
    text = f"Review {citation_clause!r} with scheme.pdf."

    protected, tokens = _protect_text(
        text,
        ["scheme.pdf", citation_clause, "Startup India"],
    )

    assert citation_clause not in protected
    assert "scheme.pdf" not in protected
    assert _restore_tokens(protected, tokens) == text


def test_translate_english_returns_text_without_llm() -> None:
    assert translate("Keep this as-is.", "en", llm_call=lambda _: "changed") == (
        "Keep this as-is."
    )


def test_translate_hindi_preserves_citation_documents_terms_numbers_and_dates() -> None:
    clause = "Applicants must hold DPIIT recognition."
    text = (
        f"Apply through Startup India by 2026-10-01. See scheme-rules.pdf, "
        f"page 4: \u201c{clause}\u201d"
    )
    observed_prompt: list[str] = []

    def mocked_llm(prompt: str) -> str:
        observed_prompt.append(prompt)
        tokens = re.findall(r"SCOUTPROTECTEDTOKEN\d+END", prompt)
        return f"कृपया आवेदन करें: {' '.join(tokens)}"

    translated = translate(text, "hi", llm_call=mocked_llm)

    assert "2026-10-01" in translated
    assert "scheme-rules.pdf" in translated
    assert "4" in translated
    assert f"\u201c{clause}\u201d" in translated
    assert "Startup India" in translated
    assert "DPIIT" in translated
    assert observed_prompt


def test_translate_preserves_all_numbers_and_dates_byte_for_byte() -> None:
    text = "Submit by 2026-10-01; request INR 12,500,000 for 18 months."

    def mocked_llm(prompt: str) -> str:
        tokens = re.findall(r"SCOUTPROTECTEDTOKEN\d+END", prompt)
        return f"जमा करने की अंतिम तिथि और राशि: {' '.join(tokens)}"

    translated = translate(text, "hi", llm_call=mocked_llm)

    for value in ("2026-10-01", "12,500,000", "18"):
        assert value in translated


def test_translate_protects_tokens_in_their_text_order() -> None:
    text = "See Startup India, document.pdf on page 3 for DPIIT rules."

    def mocked_llm(prompt: str) -> str:
        tokens = re.findall(r"SCOUTPROTECTEDTOKEN\d+END", prompt)
        assert tokens == re.findall(r"SCOUTPROTECTEDTOKEN\d+END", prompt.split("\n")[-1])
        return f"स्रोत: {' '.join(tokens)}"

    translated = translate(text, "hi", llm_call=mocked_llm)

    assert translated.index("Startup India") < translated.index("document.pdf")
    assert translated.index("document.pdf") < translated.index("DPIIT")


def test_translate_rejects_unsupported_language() -> None:
    with pytest.raises(ValueError, match="Unsupported language"):
        translate("Hello.", "ta", llm_call=lambda _: "வணக்கம்.")


def test_translate_fails_if_provider_changes_protected_citation() -> None:
    with pytest.raises(ValueError, match="protected"):
        translate(
            'Rule citation: "Keep this clause."',
            "hi",
            llm_call=lambda _: "नियम बदल दिया",
        )


def test_translate_rejects_added_numbers() -> None:
    with pytest.raises(ValueError, match="numeric values"):
        translate(
            "Submit by 2026-10-01.",
            "hi",
            llm_call=lambda prompt: "2027 " + " ".join(
                re.findall(r"SCOUTPROTECTEDTOKEN\d+END", prompt)
            ),
        )


def test_translate_items_protects_supplied_scheme_name_and_rule_identifier() -> None:
    phrase = "SchemeScout rule DPIIT needs review."

    translated = translate_items(
        phrase,
        "hi",
        protected_terms=["SchemeScout", "DPIIT"],
        llm_call=lambda prompt: "समीक्षा करें " + " ".join(
            re.findall(r"SCOUTPROTECTEDTOKEN\d+END", prompt)
        ),
    )

    assert "SchemeScout" in translated
    assert "DPIIT" in translated
