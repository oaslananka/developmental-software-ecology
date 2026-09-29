from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CognitionDecision(StrictModel):
    decision: Literal["idle", "observe"]
    reason_summary: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)
    focus: str | None = Field(default=None, max_length=160)


class ModelUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelRequest(StrictModel):
    call_id: str
    experiment_id: str
    agent_id: str
    world_tick: int = Field(ge=0)
    context: dict[str, Any]
    response_schema: Literal["CognitionDecision/v0.1"] = "CognitionDecision/v0.1"


class ModelResponse(StrictModel):
    call_id: str
    provider: str
    model: str
    model_version: str | None = None
    decision: CognitionDecision
    usage: ModelUsage
    request_hash: str
    response_hash: str
