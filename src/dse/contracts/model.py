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


class GoalProgressUpdate(StrictModel):
    progress: float = Field(gt=0.0, le=1.0)
    progress_summary: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)


class GoalClosure(StrictModel):
    summary: str = Field(min_length=1, max_length=320)
    confidence: float = Field(ge=0.0, le=1.0)


class ActionProposal(StrictModel):
    kind: Literal[
        "inspect_workspace",
        "draft_artifact",
        "run_validation",
    ]
    summary: str = Field(min_length=1, max_length=240)
    target: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=240)
    expected_value: float = Field(ge=0.0, le=1.0)
    estimated_cost: float = Field(ge=0.0, le=1.0)
    draft_content: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_draft_content(self):
        if self.kind == "draft_artifact" and not self.draft_content:
            raise ValueError("draft_artifact requires draft_content")
        if self.kind != "draft_artifact" and self.draft_content is not None:
            raise ValueError(
                "draft_content is only allowed for draft_artifact"
            )
        return self


class CognitionDecision(StrictModel):
    decision: Literal[
        "idle",
        "observe",
        "propose_goal",
        "update_goal",
        "complete_goal",
        "abandon_goal",
        "propose_action",
    ]
    reason_summary: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)
    focus: str | None = Field(default=None, max_length=160)
    goal: GoalProposal | None = None
    goal_update: GoalProgressUpdate | None = None
    goal_closure: GoalClosure | None = None
    action: ActionProposal | None = None

    @model_validator(mode="after")
    def validate_payload_shape(self):
        expected = {
            "propose_goal": ("goal",),
            "update_goal": ("goal_update",),
            "complete_goal": ("goal_closure",),
            "abandon_goal": ("goal_closure",),
            "propose_action": ("action",),
        }

        populated = {
            name
            for name, value in (
                ("goal", self.goal),
                ("goal_update", self.goal_update),
                ("goal_closure", self.goal_closure),
                ("action", self.action),
            )
            if value is not None
        }

        required = set(expected.get(self.decision, ()))
        if populated != required:
            raise ValueError(
                f"{self.decision} requires exactly {sorted(required)}, "
                f"received {sorted(populated)}"
            )
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
        "CognitionDecision/v0.3",
        "CognitionDecision/v0.4",
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
