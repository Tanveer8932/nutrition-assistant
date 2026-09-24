from types import SimpleNamespace

import pytest

from app import llm


def _fake_client(content: str, captured: dict):
    def create(**kwargs):
        captured.update(kwargs)
        msg = SimpleNamespace(content=content, refusal=None)
        return SimpleNamespace(model="fake-model", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_openai_uses_strict_schema_and_parses(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(llm, "_openai", _fake_client('{"answer": "a", "claims": [{"text": "t", "source": null}]}', captured))
    result = llm._ask_openai([{"role": "user", "content": "q"}])
    assert result.response.claims[0].source is None
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert captured["messages"][0]["role"] == "system"


def test_openai_non_null_source_is_schema_error(monkeypatch):
    monkeypatch.setattr(llm, "_openai", _fake_client('{"answer": "a", "claims": [{"text": "t", "source": "WHO"}]}', {}))
    with pytest.raises(llm.SchemaError):
        llm._ask_openai([{"role": "user", "content": "q"}])


def test_groq_client_uses_groq_endpoint_and_key(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "groq")
    monkeypatch.setattr(llm, "_openai", None)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = llm._openai_client()
    assert str(client.base_url).startswith("https://api.groq.com/openai/v1")
    assert client.api_key == "test-key"


def test_groq_request_is_strict_schema_with_no_fallback(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(llm, "PROVIDER", "groq")
    monkeypatch.setattr(llm, "MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(llm, "EFFORT", None)
    monkeypatch.setattr(llm, "_openai", _fake_client('{"answer": "a", "claims": [{"text": "t", "source": null}]}', captured))
    result = llm.ask([{"role": "user", "content": "q"}])
    assert result.response.claims[0].source is None
    assert captured["model"] == "openai/gpt-oss-20b"
    rf = captured["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == llm.MODEL_OUTPUT_SCHEMA
    assert "reasoning_effort" not in captured
    assert not any(k in captured for k in ("fallbacks", "models", "extra_body"))


def test_groq_non_null_source_is_schema_error(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "groq")
    monkeypatch.setattr(llm, "_openai", _fake_client('{"answer": "a", "claims": [{"text": "t", "source": "USDA"}]}', {}))
    with pytest.raises(llm.SchemaError):
        llm.ask([{"role": "user", "content": "q"}])


def test_groq_missing_key_never_falls_back_to_openai_key(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "groq")
    monkeypatch.setattr(llm, "_openai", None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key-must-not-reach-groq")
    with pytest.raises(llm.ProviderNotConfigured):
        llm._openai_client()
