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


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    experiment_id: str
    generation: int = Field(default=0, ge=0)
    birth_tick: int = Field(default=0, ge=0)
    lifecycle_state: LifecycleState = LifecycleState.BORN
    traits: TraitState
    last_active_tick: int = Field(default=0, ge=0)
    state_version: int = Field(default=0, ge=0)
