from src.config import get_llm_settings


def test_missing_llm_environment_is_supported(monkeypatch) -> None:
    for variable in (
        "SCHEMESCOUT_LLM_BASE_URL",
        "SCHEMESCOUT_LLM_MODEL",
        "SCHEMESCOUT_LLM_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)

    assert get_llm_settings().base_url is None
    assert get_llm_settings().model is None
    assert get_llm_settings().api_key is None


def test_render_environment_aliases_are_supported(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "render-model")
    monkeypatch.setenv("LLM_API_KEY", "render-test-key")

    settings = get_llm_settings()

    assert settings.base_url == "https://llm.example/v1"
    assert settings.model == "render-model"
    assert settings.api_key == "render-test-key"


def test_legacy_environment_values_take_precedence(monkeypatch) -> None:
    monkeypatch.setenv("SCHEMESCOUT_LLM_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("SCHEMESCOUT_LLM_MODEL", "legacy-model")
    monkeypatch.setenv("SCHEMESCOUT_LLM_API_KEY", "legacy-test-key")
    monkeypatch.setenv("LLM_PROVIDER", "https://render.example/v1")
    monkeypatch.setenv("LLM_MODEL", "render-model")
    monkeypatch.setenv("LLM_API_KEY", "render-test-key")

    settings = get_llm_settings()

    assert settings.base_url == "https://legacy.example/v1"
    assert settings.model == "legacy-model"
    assert settings.api_key == "legacy-test-key"
