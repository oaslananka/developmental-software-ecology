from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StudyDesignConfig(StrictModel):
    enforce_condition_contract: bool = False
    text_culture: Literal["none", "persistent", "docs_only"] = "none"
    artifact_culture: Literal["none", "executable"] = "none"
    social_channel: Literal[
        "none",
        "limited",
        "direct",
        "issues_pr_messages",
        "isolated",
    ] = "none"
    ril_control: bool = False
    compute_match_group: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    ril_topology: Literal[
        "not_applicable",
        "single_isolated",
        "independent_isolated",
    ] = "not_applicable"


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


class TurnoverConfig(StrictModel):
    enabled: bool = False
    ticks: list[int] = Field(default_factory=list, max_length=128)
    agent_ids: list[str] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def validate_schedule(self):
        if len(self.ticks) != len(set(self.ticks)):
            raise ValueError("turnover ticks must be unique")
        if any(tick <= 0 for tick in self.ticks):
            raise ValueError("turnover ticks must be positive")
        if self.ticks != sorted(self.ticks):
            raise ValueError("turnover ticks must be sorted")
        if len(self.agent_ids) != len(set(self.agent_ids)):
            raise ValueError("turnover agent_ids must be unique")
        if any(
            not agent_id.startswith("agent-")
            or len(agent_id) != 10
            or not agent_id[6:].isdigit()
            for agent_id in self.agent_ids
        ):
            raise ValueError("turnover agent_ids must match agent-XXXX")
        if self.enabled and (not self.ticks or not self.agent_ids):
            raise ValueError(
                "enabled turnover requires non-empty ticks and agent_ids"
            )
        return self


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


class TextCultureConfig(StrictModel):
    enabled: bool = False
    operations_per_cycle: int = Field(default=4, ge=0, le=64)
    max_entries: int = Field(default=512, ge=1, le=4096)
    max_entry_bytes: int = Field(default=4096, ge=256, le=65_536)
    context_entry_limit: int = Field(default=16, ge=1, le=64)
    context_content_chars: int = Field(default=1024, ge=0, le=8192)


class SocialConfig(StrictModel):
    enabled: bool = False
    mode: Literal["none", "direct", "issues_pr_messages"] = "none"
    operations_per_cycle: int = Field(default=4, ge=0, le=64)
    max_messages: int = Field(default=1024, ge=1, le=8192)
    max_threads: int = Field(default=256, ge=1, le=2048)
    max_message_bytes: int = Field(default=2048, ge=128, le=32_768)
    context_message_limit: int = Field(default=16, ge=1, le=64)
    context_content_chars: int = Field(default=512, ge=0, le=4096)


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
    operations_per_cycle: int = Field(default=0, ge=0, le=64)
    inspection_artifact_limit: int = Field(default=16, ge=1, le=64)
    inspection_content_chars: int = Field(default=2048, ge=0, le=8192)


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
    turnover: TurnoverConfig = Field(default_factory=TurnoverConfig)
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
    text_culture: TextCultureConfig = Field(default_factory=TextCultureConfig)
    social: SocialConfig = Field(default_factory=SocialConfig)
    forge: ForgeConfig = Field(default_factory=ForgeConfig)
    sandbox_policy: SandboxPolicyConfig = Field(
        default_factory=SandboxPolicyConfig
    )
    model_provider: ModelProviderConfig = Field(default_factory=ModelProviderConfig)


class ExperimentManifest(StrictModel):
    schema_version: Literal["0.1"]
    study: StudyDesignConfig = Field(default_factory=StudyDesignConfig)
    experiment: ExperimentIdentity
    world: WorldConfig
    agents: AgentConfig
    runtime: RuntimeConfig

    @model_validator(mode="after")
    def validate_condition_contract(self):
        if self.study.enforce_condition_contract:
            from dse.experiments.conditions import validate_condition_manifest

            validate_condition_manifest(self)
        return self


def load_manifest(path: str | Path) -> ExperimentManifest:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ExperimentManifest.model_validate(raw)
