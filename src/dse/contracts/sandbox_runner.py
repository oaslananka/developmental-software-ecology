from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dse.contracts.sandbox import SandboxAdmissionDecision, SandboxExecutionPlan


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SandboxRunRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=220)
    admission_id: str = Field(min_length=1, max_length=200)
    plan: SandboxExecutionPlan
    plan_hash: str = Field(
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


class SandboxRunResult(StrictModel):
    request_id: str = Field(min_length=1, max_length=220)
    admission_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=200)
    plan_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    policy_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    artifact_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    attestation_id: str = Field(min_length=1, max_length=160)
    backend: str = Field(min_length=1, max_length=64)
    backend_version: str = Field(min_length=1, max_length=120)
    status: Literal["succeeded", "failed", "timed_out"]
    exit_code: int | None = None
    stdout: str = Field(default="", max_length=4_194_304)
    stderr: str = Field(default="", max_length=4_194_304)
    duration_ms: int = Field(ge=0, le=3_600_000)

    @model_validator(mode="after")
    def validate_exit_semantics(self):
        if self.status == "succeeded" and self.exit_code != 0:
            raise ValueError("succeeded result requires exit_code=0")
        if self.status == "failed" and (
            self.exit_code is None or self.exit_code == 0
        ):
            raise ValueError("failed result requires a non-zero exit_code")
        return self


class SandboxDispatchOutcome(StrictModel):
    admission: SandboxAdmissionDecision
    dispatched: bool
    completed: bool
    reason: Literal[
        "admission_denied",
        "completed",
        "result_binding_mismatch",
        "result_output_limit_exceeded",
        "result_duration_limit_exceeded",
    ]
    request_id: str | None = Field(default=None, max_length=220)
    result_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    rejection_reasons: list[str] = Field(default_factory=list, max_length=32)
