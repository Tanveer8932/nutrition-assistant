"""The model call. Server-side only; the browser never sees the API key."""
import os
from dataclasses import dataclass

import anthropic
from pydantic import ValidationError

from .prompts import SYSTEM_PROMPT
from .schemas import MODEL_OUTPUT_SCHEMA, AssistantResponse

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
EFFORT = os.environ.get("ANTHROPIC_EFFORT", "low")

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class SchemaError(Exception):
    """The model's output did not parse against the response schema."""

    def __init__(self, message: str, raw: str | None):
        super().__init__(message)
        self.raw = raw


class ModelRefusal(Exception):
    pass


@dataclass
class ModelResult:
    response: AssistantResponse
    raw: str
    model: str


def ask(messages: list[dict]) -> ModelResult:
    """Call the model with structured outputs and validate the result.

    Raises SchemaError if the output is not valid JSON matching the schema
    (including any non-null source). No retries-until-it-parses: a parse
    failure is surfaced, not papered over.
    """
    resp = _get_client().beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": MODEL_OUTPUT_SCHEMA},
        },
        # On a safety-classifier decline, re-run on Anthropic's recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    if resp.stop_reason == "refusal":
        raise ModelRefusal("The model declined to answer this request.")
    if resp.stop_reason == "max_tokens":
        raise SchemaError("Model output was truncated (max_tokens).", None)

    raw = next((b.text for b in resp.content if b.type == "text"), None)
    if raw is None:
        raise SchemaError("Model returned no text block.", None)
    try:
        parsed = AssistantResponse.model_validate_json(raw)
    except ValidationError as e:
        raise SchemaError(f"Model output failed schema validation: {e}", raw) from e
    return ModelResult(response=parsed, raw=raw, model=resp.model)
