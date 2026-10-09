"""Live model choice comes from operator configuration."""

import pytest

from aeroagentsim.agents.provider import OpenAIProvider


def test_default_provider_requires_explicit_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AAS_LLM_MODEL", raising=False)
    with pytest.raises(ValueError, match="requires AAS_LLM_MODEL"):
        OpenAIProvider.from_config()
    monkeypatch.setenv("AAS_LLM_MODEL", "test-model")
    assert OpenAIProvider.from_config().model == "test-model"
