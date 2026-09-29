from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentIdentity(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    condition: Literal["P", "T", "E", "ES", "RIL"]
    seed: int


class WorldConfig(StrictModel):
    agent_count: int = Field(ge=1)
    max_world_ticks: int = Field(gt=0)
    tick_duration_sim_seconds: int = Field(gt=0)
    snapshot_interval_ticks: int = Field(gt=0)


class TraitConfig(StrictModel):
    exploration_bias: float = Field(ge=0.0, le=1.0)
    persistence: float = Field(ge=0.0, le=1.0)
    social_bias: float = Field(ge=0.0, le=1.0)
    risk_bias: float = Field(ge=0.0, le=1.0)


class LifecycleConfig(StrictModel):
    active_ticks_per_cycle: int = Field(default=960, gt=0)
    sleep_ticks_per_cycle: int = Field(default=480, gt=0)


class CognitionConfig(StrictModel):
    enabled: bool = False
    model_calls_per_cycle: int = Field(default=0, ge=0)
    interval_ticks: int = Field(default=200, gt=0)


class GoalConfig(StrictModel):
    enabled: bool = False
    lifecycle_enabled: bool = False
    max_active_goals: int = Field(default=1, ge=1, le=1)


class MemoryConfig(StrictModel):
    enabled: bool = False
    capacity: int = Field(default=32, ge=1)
    retrieval_limit: int = Field(default=4, ge=1)


class SleepMemoryConfig(StrictModel):
    enabled: bool = False
    strengthen_top_k: int = Field(default=0, ge=0)
    salience_boost: float = Field(default=0.0, ge=0.0, le=1.0)
    forget_below_salience: float = Field(default=0.0, ge=0.0, le=1.0)
    forget_older_than_ticks: int = Field(default=0, ge=0)
    max_forget_per_sleep: int = Field(default=0, ge=0)


class ModelProviderConfig(StrictModel):
    kind: Literal["fake", "opencode"] = "fake"
    model: str = Field(default="fake-cognition-v0.1", min_length=1)
    base_url: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)


class AgentConfig(StrictModel):
    initial_traits: TraitConfig
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)
    cognition: CognitionConfig = Field(default_factory=CognitionConfig)
    goals: GoalConfig = Field(default_factory=GoalConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    sleep_memory: SleepMemoryConfig = Field(default_factory=SleepMemoryConfig)


class RuntimeConfig(StrictModel):
    llm_enabled: bool = False
    forge_enabled: bool = False
    sandbox_enabled: bool = False
    web_enabled: bool = False
    model_provider: ModelProviderConfig = Field(default_factory=ModelProviderConfig)


class ExperimentManifest(StrictModel):
    schema_version: Literal["0.1"]
    experiment: ExperimentIdentity
    world: WorldConfig
    agents: AgentConfig
    runtime: RuntimeConfig


def load_manifest(path: str | Path) -> ExperimentManifest:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ExperimentManifest.model_validate(raw)
