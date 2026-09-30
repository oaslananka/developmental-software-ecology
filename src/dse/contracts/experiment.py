import hashlib
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


class EvaluationConfig(StrictModel):
    enabled: bool = False
    suite_id: str | None = Field(default=None, min_length=1, max_length=160)
    suite_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    max_snapshot_artifacts: int = Field(default=512, ge=1, le=4096)
    max_snapshot_bytes: int = Field(default=1_048_576, ge=1024, le=67_108_864)
    max_cases: int = Field(default=256, ge=1, le=4096)
    sandbox_enabled: bool = False
    sandbox_policy: SandboxPolicyConfig = Field(
        default_factory=SandboxPolicyConfig
    )

    @model_validator(mode="after")
    def validate_hidden_evaluator(self):
        if not self.enabled:
            return self
        if self.suite_id is None or self.suite_hash is None:
            raise ValueError(
                "enabled evaluation requires suite_id and suite_hash"
            )
        if not self.sandbox_enabled:
            raise ValueError(
                "enabled evaluation requires evaluation sandbox"
            )
        if self.sandbox_policy.backend == "none":
            raise ValueError(
                "enabled evaluation requires a configured sandbox backend"
            )
        return self


def v0_1_hidden_evaluation_payload() -> dict[str, object]:
    return {
        "enabled": True,
        "suite_id": "v0_1-hidden-functional-suite-m20-v1",
        "suite_hash": (
            "a869623be48e27c4fa39563596c703f1308f0b1a9a9a41c12fdf4a36c28146b1"
        ),
        "max_snapshot_artifacts": 512,
        "max_snapshot_bytes": 1_048_576,
        "max_cases": 256,
        "sandbox_enabled": True,
        "sandbox_policy": {
            "backend": "external-hardened",
            "network_enabled": False,
            "host_mounts_enabled": False,
            "secrets_enabled": False,
            "shell_enabled": False,
            "cpu_seconds": 2,
            "memory_mb": 256,
            "pids_max": 32,
            "disk_mb": 64,
            "output_bytes": 65_536,
            "wall_timeout_seconds": 5,
        },
    }


class FunctionalOpportunityConfig(StrictModel):
    enabled: bool = False
    profile_id: str | None = Field(default=None, min_length=1, max_length=160)
    spec_id: str | None = Field(default=None, min_length=1, max_length=160)
    spec_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    aggregation: Literal["population_any"] = "population_any"
    submission_path: str = Field(
        default="submission.py",
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._/-]+$",
    )
    entrypoint: str = Field(
        default="solve",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    max_submission_bytes: int = Field(default=4000, ge=256, le=16_384)
    public_brief: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def validate_opportunity(self):
        if not self.enabled:
            return self
        if self.profile_id is None:
            raise ValueError(
                "enabled functional opportunity requires profile_id"
            )
        if self.spec_id is None or self.spec_sha256 is None:
            raise ValueError(
                "enabled functional opportunity requires spec identity/hash"
            )
        actual_hash = hashlib.sha256(
            self.public_brief.encode("utf-8")
        ).hexdigest()
        if actual_hash != self.spec_sha256:
            raise ValueError(
                "functional opportunity public_brief hash mismatch"
            )
        normalized = self.submission_path.replace("\\", "/")
        if (
            normalized.startswith("/")
            or any(part == ".." for part in normalized.split("/"))
        ):
            raise ValueError(
                "functional opportunity submission_path must be relative"
            )
        return self


_V0_1_FUNCTIONAL_PUBLIC_SPEC = (
    "DSE Utility Kernel v0.1. The environment accepts capability requests through "
    "solve(payload), which returns a JSON-compatible value. payload is "
    "{'task': T, 'args': A}; inputs follow the valid domains described here. "
    "Use deterministic Python standard-library computation only: no I/O, network, "
    "subprocesses, environment reads, clocks, or randomness. Tasks: stable_unique: "
    "A={'items':[scalar,...]}; scalar is null/bool/int/string. Return "
    "first-occurrence uniques; equality requires the same JSON type and value, so "
    "true differs from 1. range_pack: A={'ranges':[[a,b],...]}; a,b are ints and "
    "a<=b. Sort by (a,b), merge overlaps or integer-adjacent ranges when "
    "next_a<=current_b+1, return merged ranges. bucket_total: "
    "A={'rows':[{'key':k,'value':v},...]}; k is a lowercase ASCII identifier and v "
    "an int. Return [{'key':k,'total':sum},...] sorted by key. rank_select: "
    "A={'rows':[{'id':s,'score':n},...],'k':k}; ids are unique ASCII strings, scores "
    "ints, k>=0. Return up to k ids ordered by score descending then id ascending. "
    "dependency_layers: A={'nodes':[s,...],'edges':[[u,v],...]}; nodes are unique "
    "ASCII strings and edges are directed dependencies u->v. Repeatedly emit each "
    "lexicographically sorted zero-indegree frontier as one layer. Return "
    "{'cycle':false,'layers':[[...],...]} for a DAG; if any cycle exists return "
    "{'cycle':true,'layers':[]}. route_min: "
    "A={'nodes':[s,...],'edges':[[u,v,w],...],'start':s,'goal':s}; directed edges "
    "have positive integer weights. Return {'cost':null,'path':[]} if unreachable; "
    "otherwise return minimum total cost and path, breaking equal-cost ties by "
    "lexicographically smallest full node sequence. counter_patch: "
    "A={'base':{key:int,...},'ops':[op,...]}; op is "
    "{'op':'set','key':k,'value':n}, {'op':'add','key':k,'value':n}, or "
    "{'op':'delete','key':k}. Apply in order; add treats a missing key as 0; delete "
    "of a missing key is a no-op; return the final object. window_sum: "
    "A={'values':[int,...],'width':w}; w>=1. Return sums of each contiguous width-w "
    "window; return [] when w exceeds the input length."
)
_V0_1_FUNCTIONAL_PUBLIC_SPEC_SHA256 = (
    "8c5dbb384df0bb73e2b8db6dd9b8a97cf31ef904ecca8a5bf4411f5e8e696439"
)


def v0_1_functional_opportunity_payload() -> dict[str, object]:
    return {
        "enabled": True,
        "profile_id": "v0_1-functional-submission",
        "spec_id": "dse-utility-kernel-v0.1",
        "spec_sha256": _V0_1_FUNCTIONAL_PUBLIC_SPEC_SHA256,
        "aggregation": "population_any",
        "submission_path": "submission.py",
        "entrypoint": "solve",
        "max_submission_bytes": 4000,
        "public_brief": _V0_1_FUNCTIONAL_PUBLIC_SPEC,
    }


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
    evaluation_profile: Literal[
        "none",
        "v0_1-hidden-functional-suite",
    ] = "none"
    functional_opportunity_profile: Literal[
        "none",
        "v0_1-functional-submission",
    ] = "none"
    study: StudyDesignConfig = Field(default_factory=StudyDesignConfig)
    experiment: ExperimentIdentity
    world: WorldConfig
    agents: AgentConfig
    runtime: RuntimeConfig
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    functional_opportunity: FunctionalOpportunityConfig = Field(
        default_factory=FunctionalOpportunityConfig
    )

    @model_validator(mode="before")
    @classmethod
    def resolve_profiles(cls, value):
        if not isinstance(value, dict):
            return value

        profile = value.get("evaluation_profile", "none")
        if profile == "v0_1-hidden-functional-suite":
            expected = EvaluationConfig.model_validate(
                v0_1_hidden_evaluation_payload()
            ).model_dump(mode="json")
            if "evaluation" in value:
                observed = EvaluationConfig.model_validate(
                    value["evaluation"]
                ).model_dump(mode="json")
                if observed != expected:
                    raise ValueError(
                        "evaluation does not match evaluation_profile"
                    )
            else:
                resolved = dict(value)
                resolved["evaluation"] = expected
                value = resolved

        opportunity_profile = value.get(
            "functional_opportunity_profile",
            "none",
        )
        if opportunity_profile == "none":
            return value
        if opportunity_profile != "v0_1-functional-submission":
            return value

        expected_opportunity = FunctionalOpportunityConfig.model_validate(
            v0_1_functional_opportunity_payload()
        ).model_dump(mode="json")
        if "functional_opportunity" in value:
            observed_opportunity = FunctionalOpportunityConfig.model_validate(
                value["functional_opportunity"]
            ).model_dump(mode="json")
            if observed_opportunity != expected_opportunity:
                raise ValueError(
                    "functional_opportunity does not match "
                    "functional_opportunity_profile"
                )
            return value

        resolved = dict(value)
        resolved["functional_opportunity"] = expected_opportunity
        return resolved

    @model_validator(mode="after")
    def validate_condition_contract(self):
        if self.study.enforce_condition_contract:
            from dse.experiments.conditions import validate_condition_manifest

            validate_condition_manifest(self)
        return self


def load_manifest(path: str | Path) -> ExperimentManifest:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ExperimentManifest.model_validate(raw)
