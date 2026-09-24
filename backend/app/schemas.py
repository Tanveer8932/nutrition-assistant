"""The response contract.

Milestone 1: every `source` is null. Milestone 2 fills `source` from the
retrieval layer without changing this shape.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="One factual statement made in the answer.")
    source: Optional[str] = Field(
        default=None,
        description="Citation for the claim. Always null in Milestone 1.",
    )


class AssistantResponse(BaseModel):
    """What the model must return. Parsed strictly; anything else is an error."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    claims: list[Claim]

    @field_validator("claims")
    @classmethod
    def sources_must_be_null(cls, claims: list[Claim]) -> list[Claim]:
        # Milestone 1 rule: no retrieval layer exists, so any non-null source
        # is an invented citation. Reject rather than pass it to the user.
        for c in claims:
            if c.source is not None:
                raise ValueError(f"source must be null in Milestone 1, got {c.source!r}")
        return claims


# JSON schema sent to the model via structured outputs. Written by hand so the
# model sees exactly this contract (source is typed null-only this milestone).
MODEL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "null"},
                },
                "required": ["text", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer", "claims"],
    "additionalProperties": False,
}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: int
    response: AssistantResponse
    declined: bool
    decline_category: Optional[str] = None
    guard_stage: Optional[Literal["input", "output"]] = None
    model: Optional[str] = None


class StoredMessage(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    response: Optional[AssistantResponse] = None
    declined: bool = False
    created_at: str


class Conversation(BaseModel):
    id: str
    created_at: str
    messages: list[StoredMessage]
