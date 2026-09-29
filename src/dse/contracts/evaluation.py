from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dse.contracts.experiment import SandboxPolicyConfig


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationArtifactBinding(StrictModel):
    repo_id: str = Field(min_length=1, max_length=96)
    repo_name: str = Field(min_length=1, max_length=80)
    artifact_id: str = Field(min_length=1, max_length=96)
    commit_id: str = Field(min_length=1, max_length=96)
    path: str = Field(min_length=1, max_length=240)
    content_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    content_bytes: int = Field(ge=0, le=65_536)
    creator_agent_id: str = Field(min_length=1, max_length=120)
    creator_generation: int = Field(ge=0)
    parent_artifact_ids: list[str] = Field(default_factory=list, max_length=64)


class EvaluationArtifactInput(EvaluationArtifactBinding):
    content: str = Field(max_length=65_536)


class EvaluationWorldBinding(StrictModel):
    experiment_id: str = Field(min_length=1)
    condition: Literal["P", "T", "E", "ES", "RIL"]
    world_tick: int = Field(ge=0)
    world_sequence: int = Field(ge=0)
    world_snapshot_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    culture_snapshot_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )


class CultureEvaluationSnapshot(EvaluationWorldBinding):
    artifacts: list[EvaluationArtifactInput] = Field(
        default_factory=list,
        max_length=4096,
    )


class HiddenEvaluationPlan(EvaluationWorldBinding):
    evaluation_id: str = Field(min_length=1, max_length=240)
    suite_id: str = Field(min_length=1, max_length=160)
    suite_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    artifact_bindings: list[EvaluationArtifactBinding] = Field(
        default_factory=list,
        max_length=4096,
    )


class HiddenEvaluationRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=280)
    plan: HiddenEvaluationPlan
    plan_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    snapshot: CultureEvaluationSnapshot
    policy: SandboxPolicyConfig
    policy_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    attestation_id: str = Field(min_length=1, max_length=160)
    backend: str = Field(min_length=1, max_length=64)
    backend_version: str = Field(min_length=1, max_length=120)


class HiddenEvaluationRunnerResult(StrictModel):
    request_id: str = Field(min_length=1, max_length=280)
    evaluation_id: str = Field(min_length=1, max_length=240)
    plan_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    culture_snapshot_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    suite_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    policy_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    attestation_id: str = Field(min_length=1, max_length=160)
    backend: str = Field(min_length=1, max_length=64)
    backend_version: str = Field(min_length=1, max_length=120)
    runner_kind: Literal["test-double", "external-hardened"]
    runner_version: str = Field(min_length=1, max_length=120)
    worker_build_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    runtime_build_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    total_cases: int = Field(ge=1)
    duration_ms: int = Field(ge=0, le=3_600_000)

    @model_validator(mode="after")
    def validate_case_totals(self):
        if self.passed_cases + self.failed_cases != self.total_cases:
            raise ValueError(
                "passed_cases + failed_cases must equal total_cases"
            )
        return self


class FunctionalEvaluationReport(HiddenEvaluationPlan):
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    total_cases: int = Field(ge=1)
    functional_score: float = Field(ge=0.0, le=1.0)
    duration_ms: int = Field(ge=0, le=3_600_000)
    policy_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    attestation_id: str = Field(min_length=1, max_length=160)
    backend: str = Field(min_length=1, max_length=64)
    backend_version: str = Field(min_length=1, max_length=120)
    runner_kind: Literal["test-double", "external-hardened"]
    runner_version: str = Field(min_length=1, max_length=120)
    worker_build_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    runtime_build_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    result_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )


class HiddenEvaluationOutcome(StrictModel):
    completed: bool
    reason: Literal[
        "completed",
        "evaluation_disabled",
        "admission_denied",
        "snapshot_limit_exceeded",
        "result_binding_mismatch",
        "case_limit_exceeded",
        "duration_limit_exceeded",
        "world_state_mutated",
    ]
    evaluation_id: str | None = Field(default=None, max_length=240)
    report: FunctionalEvaluationReport | None = None
    rejection_reasons: list[str] = Field(default_factory=list, max_length=64)
