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


class ActionConfig(StrictModel):
    enabled: bool = False
    proposals_per_cycle: int = Field(default=0, ge=0)


class ToolBrokerConfig(StrictModel):
    enabled: bool = False
    executions_per_cycle: int = Field(default=0, ge=0)
    executor: Literal[
        "deterministic-fake",
        "ephemeral-local-workspace",
    ] = "deterministic-fake"


class ForgeConfig(StrictModel):
    enabled: bool = False
    provider: Literal["deterministic-memory"] = "deterministic-memory"
    default_branch: str = Field(
        default="main",
        min_length=1,
        max_length=80,
        pattern="^[A-Za-z0-9._/-]+$",
    )
    max_artifact_bytes: int = Field(default=16_384, ge=1, le=65_536)
    max_repositories: int = Field(default=32, ge=1, le=256)
    max_artifacts_per_repository: int = Field(default=256, ge=1, le=4096)


class SandboxPolicyConfig(StrictModel):
    backend: Literal["none", "external-hardened"] = "none"
    network_enabled: Literal[False] = False
    host_mounts_enabled: Literal[False] = False
    secrets_enabled: Literal[False] = False
    shell_enabled: Literal[False] = False
    cpu_seconds: int = Field(default=2, ge=1, le=60)
    memory_mb: int = Field(default=256, ge=64, le=4096)
    pids_max: int = Field(default=32, ge=1, le=256)
    disk_mb: int = Field(default=64, ge=1, le=1024)
    output_bytes: int = Field(default=65_536, ge=1024, le=4_194_304)
    wall_timeout_seconds: int = Field(default=5, ge=1, le=120)


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
    actions: ActionConfig = Field(default_factory=ActionConfig)
    tool_broker: ToolBrokerConfig = Field(default_factory=ToolBrokerConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    sleep_memory: SleepMemoryConfig = Field(default_factory=SleepMemoryConfig)


class RuntimeConfig(StrictModel):
    llm_enabled: bool = False
    forge_enabled: bool = False
    sandbox_enabled: bool = False
    web_enabled: bool = False
    forge: ForgeConfig = Field(default_factory=ForgeConfig)
    sandbox_policy: SandboxPolicyConfig = Field(
        default_factory=SandboxPolicyConfig
    )
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
