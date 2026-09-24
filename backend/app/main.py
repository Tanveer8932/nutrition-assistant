import json
import logging
import os
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import db, llm, scope_guard
from .prompts import DECLINE_MESSAGES, PROMPT_VERSION
from .schemas import (
    AssistantResponse,
    ChatRequest,
    ChatResponse,
    Conversation,
    StoredMessage,
)

log = logging.getLogger("nutrition")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Nutrition Assistant", version="0.1.0", lifespan=lifespan)

_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:4200").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=os.environ.get("CORS_ORIGIN_REGEX") or None,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _model_messages(hist: list[dict], new_message: str) -> list[dict]:
    """Prior turns as plain text, keeping only complete user->assistant pairs."""
    out: list[dict] = []
    pending_user: str | None = None
    for m in hist:
        if m["role"] == "user":
            pending_user = m["content"]
        elif pending_user is not None:
            out.append({"role": "user", "content": pending_user})
            out.append({"role": "assistant", "content": m["content"]})
            pending_user = None
    out.append({"role": "user", "content": new_message})
    return out


def _decline(category: str) -> AssistantResponse:
    return AssistantResponse(answer=DECLINE_MESSAGES[category], claims=[])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "model": llm.MODEL, "prompt_version": PROMPT_VERSION}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    conversation_id = db.get_or_create_conversation(req.conversation_id)
    hist = db.history(conversation_id)
    previous_decline = hist[-1]["decline_category"] if hist and hist[-1]["role"] == "assistant" and hist[-1]["declined"] else None
    earlier_decline = next((m["decline_category"] for m in reversed(hist) if m["declined"]), None)
    db.add_user_message(conversation_id, req.message)

    guard = scope_guard.check_input(req.message, previous_decline, earlier_decline)
    raw: str | None = None
    model: str | None = None

    if guard:
        response = _decline(guard.category)
    else:
        try:
            result = llm.ask(_model_messages(hist, req.message))
        except llm.SchemaError as e:
            log.error("schema failure: %s | raw=%r", e, e.raw)
            raise HTTPException(status_code=502, detail="The model returned a response that did not match the schema.")
        except llm.ModelRefusal:
            raise HTTPException(status_code=422, detail="The model declined to answer this request.")
        except anthropic.RateLimitError:
            raise HTTPException(status_code=429, detail="Rate limited by the model provider. Try again shortly.")
        except anthropic.APIStatusError as e:
            log.error("anthropic status error: %s", e)
            raise HTTPException(status_code=502, detail="Model provider error.")
        except anthropic.APIConnectionError:
            raise HTTPException(status_code=503, detail="Could not reach the model provider.")

        raw, model, response = result.raw, result.model, result.response
        guard = scope_guard.check_output(response)
        if guard:
            response = _decline(guard.category)

    message_id = db.add_assistant_message(
        conversation_id,
        response.model_dump(),
        declined=guard is not None,
        decline_category=guard.category if guard else None,
        guard_stage=guard.stage if guard else None,
        guard_pattern=guard.pattern if guard else None,
        raw_model_output=raw,
        model=model,
        prompt_version=PROMPT_VERSION,
    )
    return ChatResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        response=response,
        declined=guard is not None,
        decline_category=guard.category if guard else None,
        guard_stage=guard.stage if guard else None,
        model=model,
    )


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
def get_conversation(conversation_id: str) -> Conversation:
    data = db.get_conversation(conversation_id)
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return Conversation(
        id=data["conversation"]["id"],
        created_at=data["conversation"]["created_at"],
        messages=[
            StoredMessage(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                response=AssistantResponse.model_validate(json.loads(m["response_json"])) if m["response_json"] else None,
                declined=bool(m["declined"]),
                created_at=m["created_at"],
            )
            for m in data["messages"]
        ],
    )


@app.get("/api/claims/unsupported")
def unsupported_claims(limit: int = 200) -> list[dict]:
    """Every claim made without a source. In Milestone 1, that's all of them."""
    return db.unsupported_claims(limit)
