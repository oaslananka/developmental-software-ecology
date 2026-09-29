from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalProposal(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=320)
    motivation_summary: str = Field(min_length=1, max_length=240)
    expected_value: float = Field(ge=0.0, le=1.0)
    estimated_cost: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class CognitionDecision(StrictModel):
    decision: Literal["idle", "observe", "propose_goal"]
    reason_summary: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)
    focus: str | None = Field(default=None, max_length=160)
    goal: GoalProposal | None = None

    @model_validator(mode="after")
    def validate_goal_shape(self):
        if self.decision == "propose_goal" and self.goal is None:
            raise ValueError("propose_goal requires goal")
        if self.decision != "propose_goal" and self.goal is not None:
            raise ValueError("goal is only allowed with propose_goal")
        return self


class ModelUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelRequest(StrictModel):
    call_id: str
    experiment_id: str
    agent_id: str
    world_tick: int = Field(ge=0)
    context: dict[str, Any]
    response_schema: Literal[
        "CognitionDecision/v0.1",
        "CognitionDecision/v0.2",
    ] = "CognitionDecision/v0.1"


class ModelResponse(StrictModel):
    call_id: str
    provider: str
    model: str
    model_version: str | None = None
    decision: CognitionDecision
    usage: ModelUsage
    request_hash: str
    response_hash: str
