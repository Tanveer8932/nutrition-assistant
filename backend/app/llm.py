"""The model call. Server-side only; the browser never sees the API key.

Provider is chosen by LLM_PROVIDER ("openai", "groq", or "anthropic"). Each
uses the provider's structured-output mode with the same JSON schema, and every
result is validated against the same Pydantic model. Groq is OpenAI-compatible,
so it goes through the openai SDK pointed at Groq's endpoint. No automatic model fallback:
every call goes to exactly the configured model.
"""
import os
from dataclasses import dataclass

import anthropic
import openai
from pydantic import ValidationError

from .prompts import SYSTEM_PROMPT
from .schemas import MODEL_OUTPUT_SCHEMA, AssistantResponse

PROVIDER = os.environ.get("LLM_PROVIDER", "openai").lower()
if PROVIDER == "anthropic":
    MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
    EFFORT = os.environ.get("ANTHROPIC_EFFORT", "low")
elif PROVIDER == "groq":
    MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
    # Unset = Groq's default reasoning effort for the model.
    EFFORT = os.environ.get("GROQ_REASONING_EFFORT") or None
else:
    PROVIDER = "openai"
    # Pinned snapshot so repeated eval runs hit the same model.
    MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-nano-2025-08-07")
    EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "minimal")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_KEY_ENVS = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def key_env() -> str:
    """The one environment variable that must hold the key for the active provider."""
    return _KEY_ENVS[PROVIDER]


def key_configured() -> bool:
    return bool(os.environ.get(key_env()))

_anthropic: anthropic.Anthropic | None = None
_openai: openai.OpenAI | None = None

# Errors main.py maps to HTTP status codes, across all providers.
RateLimitErrors = (anthropic.RateLimitError, openai.RateLimitError)
StatusErrors = (anthropic.APIStatusError, openai.APIStatusError)
ConnectionErrors = (anthropic.APIConnectionError, openai.APIConnectionError)


class SchemaError(Exception):
    """The model's output did not parse against the response schema."""

    def __init__(self, message: str, raw: str | None):
        super().__init__(message)
        self.raw = raw


class ModelRefusal(Exception):
    pass


class ProviderNotConfigured(Exception):
    """The active provider's API key is missing from the server environment."""


def _require_key() -> str:
    key = os.environ.get(key_env())
    if not key:
        raise ProviderNotConfigured(f"{key_env()} is not set for LLM_PROVIDER={PROVIDER}")
    return key


@dataclass
class ModelResult:
    response: AssistantResponse
    raw: str
    model: str


def _parse(raw: str | None) -> AssistantResponse:
    if raw is None:
        raise SchemaError("Model returned no text.", None)
    try:
        return AssistantResponse.model_validate_json(raw)
    except ValidationError as e:
        raise SchemaError(f"Model output failed schema validation: {e}", raw) from e


def _ask_anthropic(messages: list[dict]) -> ModelResult:
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.Anthropic(api_key=_require_key())
    resp = _anthropic.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={
            "effort": EFFORT,
            "format": {"type": "json_schema", "schema": MODEL_OUTPUT_SCHEMA},
        },
    )
    if resp.stop_reason == "refusal":
        raise ModelRefusal("The model declined to answer this request.")
    if resp.stop_reason == "max_tokens":
        raise SchemaError("Model output was truncated (max_tokens).", None)
    raw = next((b.text for b in resp.content if b.type == "text"), None)
    return ModelResult(response=_parse(raw), raw=raw, model=resp.model)


def _openai_client() -> openai.OpenAI:
    global _openai
    if _openai is None:
        # Explicit key per provider: never let the Groq client fall back to
        # OPENAI_API_KEY and send it to Groq's endpoint.
        if PROVIDER == "groq":
            _openai = openai.OpenAI(api_key=_require_key(), base_url=GROQ_BASE_URL)
        else:
            _openai = openai.OpenAI(api_key=_require_key())
    return _openai


def _ask_openai(messages: list[dict]) -> ModelResult:
    """OpenAI Chat Completions with strict structured outputs (also used for Groq)."""
    kwargs = {}
    if EFFORT and (PROVIDER == "groq" or MODEL.startswith(("gpt-5", "o"))):
        kwargs["reasoning_effort"] = EFFORT
    resp = _openai_client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        max_completion_tokens=4000,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "assistant_response", "strict": True, "schema": MODEL_OUTPUT_SCHEMA},
        },
        **kwargs,
    )
    choice = resp.choices[0]
    if choice.message.refusal:
        raise ModelRefusal("The model declined to answer this request.")
    if choice.finish_reason == "length":
        raise SchemaError("Model output was truncated (length).", choice.message.content)
    raw = choice.message.content
    return ModelResult(response=_parse(raw), raw=raw, model=resp.model)


def ask(messages: list[dict]) -> ModelResult:
    """Call the model with structured outputs and validate the result.

    Raises SchemaError if the output is not valid JSON matching the schema
    (including any non-null source). No retries-until-it-parses: a parse
    failure is surfaced, not papered over.
    """
    if PROVIDER == "anthropic":
        return _ask_anthropic(messages)
    return _ask_openai(messages)
