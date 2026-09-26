from __future__ import annotations

from types import SimpleNamespace

from wechat_oracle.llm import OpenAICompatLLM


class _Completions:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
        )


def test_complete_json_bounds_reasoning_model_output() -> None:
    completions = _Completions()
    llm = object.__new__(OpenAICompatLLM)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm._json_mode = "prompt"

    result = llm.complete_json(model="reasoning-model", system="system", user="user")

    assert result == '{"ok":true}'
    assert completions.kwargs is not None
    assert completions.kwargs["max_tokens"] == OpenAICompatLLM.JSON_MAX_TOKENS
    assert completions.kwargs["reasoning_effort"] == "low"
    assert "response_format" not in completions.kwargs


def test_complete_json_rejects_empty_standard_content() -> None:
    class EmptyCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=""))]
            )

    llm = object.__new__(OpenAICompatLLM)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=EmptyCompletions()))
    llm._json_mode = "prompt"

    import pytest

    with pytest.raises(ValueError, match="empty structured content"):
        llm.complete_json(model="reasoning-model", system="system", user="user")


def test_complete_text_gives_reasoning_models_enough_output_budget() -> None:
    completions = _Completions()
    llm = object.__new__(OpenAICompatLLM)
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm._json_mode = "prompt"

    result = llm.complete_text(
        model="reasoning-model", system="system", user="user", max_tokens=5000
    )

    assert result == '{"ok":true}'
    assert completions.kwargs is not None
    assert completions.kwargs["max_tokens"] == OpenAICompatLLM.JSON_MAX_TOKENS
    assert completions.kwargs["reasoning_effort"] == "low"
