from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LifecycleState(StrEnum):
    BORN = "born"
    AWAKE = "awake"
    IDLE = "idle"
    SLEEPING = "sleeping"
    TURNED_OVER = "turned_over"
    TERMINATED = "terminated"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    EXECUTED = "executed"
    REJECTED = "rejected"


class TraitState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exploration_bias: float = Field(ge=0.0, le=1.0)
    persistence: float = Field(ge=0.0, le=1.0)
    social_bias: float = Field(ge=0.0, le=1.0)
    risk_bias: float = Field(ge=0.0, le=1.0)


class ResourceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_units_remaining: int = Field(ge=0)
    sleep_ticks_remaining: int = Field(default=0, ge=0)
    model_calls_remaining: int = Field(default=0, ge=0)
    action_proposals_remaining: int = Field(default=0, ge=0)
    tool_executions_remaining: int = Field(default=0, ge=0)
    cycles_completed: int = Field(default=0, ge=0)


class CognitionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls_completed: int = Field(default=0, ge=0)
    last_cognition_tick: int | None = Field(default=None, ge=0)
    last_decision: str | None = None
    last_reason_summary: str | None = None
    last_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class GoalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(min_length=1)
    created_tick: int = Field(ge=0)
    source_event_id: str = Field(min_length=1)
    source: str = Field(default="self_generated", pattern="^self_generated$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=320)
    motivation_summary: str = Field(min_length=1, max_length=240)
    expected_value: float = Field(ge=0.0, le=1.0)
    estimated_cost: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    last_progress_tick: int | None = Field(default=None, ge=0)
    last_progress_summary: str | None = Field(default=None, max_length=240)
    completion_tick: int | None = Field(default=None, ge=0)
    completion_summary: str | None = Field(default=None, max_length=320)
    abandonment_tick: int | None = Field(default=None, ge=0)
    abandonment_summary: str | None = Field(default=None, max_length=320)
    status: GoalStatus = GoalStatus.ACTIVE


class GoalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_goal_id: str | None = None
    goals: list[GoalRecord] = Field(default_factory=list)


class ActionIntentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    created_tick: int = Field(ge=0)
    source_event_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    kind: str = Field(
        pattern="^(inspect_workspace|draft_artifact|run_validation)$"
    )
    summary: str = Field(min_length=1, max_length=240)
    target: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=240)
    expected_value: float = Field(ge=0.0, le=1.0)
    estimated_cost: float = Field(ge=0.0, le=1.0)
    draft_content: str | None = Field(default=None, max_length=4000)
    status: ActionStatus = ActionStatus.PROPOSED

    @model_validator(mode="after")
    def validate_draft_content(self):
        if self.kind == "draft_artifact" and not self.draft_content:
            raise ValueError("draft_artifact requires draft_content")
        if self.kind != "draft_artifact" and self.draft_content is not None:
            raise ValueError(
                "draft_content is only allowed for draft_artifact"
            )
        return self


class ToolExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    created_tick: int = Field(ge=0)
    executor: str = Field(pattern="^deterministic-fake$")
    success: bool
    result_type: str = Field(
        pattern=(
            "^(workspace_inspection|artifact_draft_preview|validation_report)$"
        )
    )
    summary: str = Field(min_length=1, max_length=320)
    result_data: dict[str, Any]
    result_hash: str = Field(min_length=64, max_length=64)


class ActionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[ActionIntentRecord] = Field(default_factory=list)
    executions: list[ToolExecutionRecord] = Field(default_factory=list)


class EpisodicMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1)
    created_tick: int = Field(ge=0)
    source_event_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=240)
    salience: float = Field(ge=0.0, le=1.0)
    decision: str | None = None
    focus: str | None = None


class MemoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episodes: list[EpisodicMemory] = Field(default_factory=list)


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    experiment_id: str
    generation: int = Field(default=0, ge=0)
    birth_tick: int = Field(default=0, ge=0)
    lifecycle_state: LifecycleState = LifecycleState.BORN
    traits: TraitState
    resources: ResourceState
    cognition: CognitionState = Field(default_factory=CognitionState)
    goals: GoalState = Field(default_factory=GoalState)
    actions: ActionState = Field(default_factory=ActionState)
    memory: MemoryState = Field(default_factory=MemoryState)
    last_active_tick: int = Field(default=0, ge=0)
    state_version: int = Field(default=0, ge=0)
