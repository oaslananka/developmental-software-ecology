from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LifecycleState(StrEnum):
    BORN = "born"
    AWAKE = "awake"
    IDLE = "idle"
    SLEEPING = "sleeping"
    TURNED_OVER = "turned_over"
    TERMINATED = "terminated"


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
    cycles_completed: int = Field(default=0, ge=0)


class CognitionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls_completed: int = Field(default=0, ge=0)
    last_cognition_tick: int | None = Field(default=None, ge=0)
    last_decision: str | None = None
    last_reason_summary: str | None = None
    last_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


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
    memory: MemoryState = Field(default_factory=MemoryState)
    last_active_tick: int = Field(default=0, ge=0)
    state_version: int = Field(default=0, ge=0)
