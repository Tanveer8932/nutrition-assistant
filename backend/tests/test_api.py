import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import db, llm, main
from app.schemas import AssistantResponse


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with TestClient(main.app) as c:
        yield c


def _fake_ask(answer: str, claims: list[str], calls: list | None = None):
    def fake(messages):
        if calls is not None:
            calls.append(messages)
        body = {"answer": answer, "claims": [{"text": t, "source": None} for t in claims]}
        raw = json.dumps(body)
        return llm.ModelResult(response=AssistantResponse.model_validate_json(raw), raw=raw, model="fake")

    return fake


def test_schema_rejects_non_null_source():
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate({"answer": "x", "claims": [{"text": "y", "source": "WHO"}]})


def test_schema_rejects_missing_claims():
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate({"answer": "x"})


def test_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate({"answer": "x", "claims": [], "confidence": 0.9})


def test_chat_happy_path_and_history(client, monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm, "ask", _fake_ask("Rice keeps 3-4 days.", ["Cooked rice keeps 3-4 days refrigerated."], calls))
    r = client.post("/api/chat", json={"message": "How long does cooked rice keep?"})
    assert r.status_code == 200
    data = r.json()
    assert data["declined"] is False
    assert data["response"]["claims"][0]["source"] is None

    r2 = client.post("/api/chat", json={"message": "And pasta?", "conversation_id": data["conversation_id"]})
    assert r2.status_code == 200
    assert [m["role"] for m in calls[1]] == ["user", "assistant", "user"]

    conv = client.get(f"/api/conversations/{data['conversation_id']}").json()
    assert len(conv["messages"]) == 4
    assert len(client.get("/api/claims/unsupported").json()) == 2


def test_input_guard_never_calls_model(client, monkeypatch):
    def boom(_):
        raise AssertionError("model must not be called")

    monkeypatch.setattr(llm, "ask", boom)
    r = client.post("/api/chat", json={"message": "How many calories should I eat a day?"})
    assert r.status_code == 200
    data = r.json()
    assert data["declined"] is True
    assert data["guard_stage"] == "input"
    assert data["response"]["claims"] == []


def test_scope_limit_holds_after_unrelated_messages(client, monkeypatch):
    monkeypatch.setattr(llm, "ask", _fake_ask("Fine.", []))
    cid = client.post("/api/chat", json={"message": "How many calories should I eat?"}).json()["conversation_id"]
    for q in ["How do I store onions?", "Is sous vide safe?", "What is braising?"]:
        assert client.post("/api/chat", json={"message": q, "conversation_id": cid}).json()["declined"] is False
    r = client.post("/api/chat", json={"message": "Back to my daily calorie number then?", "conversation_id": cid}).json()
    assert r["declined"] is True


def test_output_guard_replaces_answer(client, monkeypatch):
    monkeypatch.setattr(llm, "ask", _fake_ask("Most adults need about 2000 calories per day.", ["Adults need 2000 kcal a day."]))
    r = client.post("/api/chat", json={"message": "Tell me about energy in food"}).json()
    assert r["declined"] is True
    assert r["guard_stage"] == "output"
    assert "2000" not in r["response"]["answer"]


def test_schema_failure_is_an_error(client, monkeypatch):
    def bad(_):
        raise llm.SchemaError("bad", '{"answer": "x"}')

    monkeypatch.setattr(llm, "ask", bad)
    r = client.post("/api/chat", json={"message": "How long do eggs keep?"})
    assert r.status_code == 502


def test_missing_provider_key_is_clean_503(client, monkeypatch):
    def not_configured(_):
        raise llm.ProviderNotConfigured("GROQ_API_KEY is not set for LLM_PROVIDER=groq")

    monkeypatch.setattr(llm, "ask", not_configured)
    r = client.post("/api/chat", json={"message": "How long do eggs keep?"})
    assert r.status_code == 503
    assert r.json()["detail"] == "The model provider is not configured on the server."
    assert "GROQ_API_KEY" not in r.text
