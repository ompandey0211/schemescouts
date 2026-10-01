import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class LLMSettings:
    base_url: str | None
    model: str | None
    api_key: str | None


def get_llm_settings() -> LLMSettings:
    """Read legacy SchemeScout settings or their Render-friendly aliases."""
    return LLMSettings(
        base_url=os.environ.get("SCHEMESCOUT_LLM_BASE_URL")
        or os.environ.get("LLM_PROVIDER"),
        model=os.environ.get("SCHEMESCOUT_LLM_MODEL")
        or os.environ.get("LLM_MODEL"),
        api_key=os.environ.get("SCHEMESCOUT_LLM_API_KEY")
        or os.environ.get("LLM_API_KEY"),
    )
