from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SandboxCheckName = Literal[
    "network_disabled",
    "no_host_mounts",
    "no_secrets",
    "no_shell",
    "cpu_limit_enforced",
    "memory_limit_enforced",
    "pid_limit_enforced",
    "disk_limit_enforced",
    "output_limit_enforced",
    "wall_timeout_enforced",
    "ephemeral_filesystem",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SandboxExecutionPlan(StrictModel):
    plan_id: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=200)
    runtime: Literal["python"]
    artifact_path: str = Field(min_length=1, max_length=200)
    artifact_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    arguments: list[
        Annotated[str, Field(max_length=256)]
    ] = Field(default_factory=list, max_length=16)
    stdin: str | None = Field(default=None, max_length=4096)

    @field_validator("artifact_path")
    @classmethod
    def validate_artifact_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/"):
            raise ValueError("artifact_path must be relative")
        if "://" in normalized:
            raise ValueError("artifact_path must not be a network target")
        if any(part == ".." for part in normalized.split("/")):
            raise ValueError("artifact_path must not traverse parents")
        return normalized


class SandboxCheck(StrictModel):
    name: SandboxCheckName
    passed: bool
    detail: str = Field(min_length=1, max_length=240)


class SandboxAttestation(StrictModel):
    attestation_id: str = Field(min_length=1, max_length=160)
    backend: str = Field(
        min_length=1,
        max_length=64,
        pattern="^[a-z0-9][a-z0-9._-]{0,63}$",
    )
    backend_version: str = Field(min_length=1, max_length=120)
    policy_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    checks: list[SandboxCheck] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_unique_checks(self):
        names = [check.name for check in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("sandbox attestation check names must be unique")
        return self


class SandboxAdmissionDecision(StrictModel):
    admission_id: str = Field(min_length=1, max_length=200)
    plan_id: str = Field(min_length=1, max_length=160)
    action_id: str = Field(min_length=1, max_length=200)
    admitted: bool
    reason: Literal[
        "admitted",
        "sandbox_disabled",
        "backend_unconfigured",
        "attestation_missing",
        "backend_mismatch",
        "policy_hash_mismatch",
        "missing_checks",
        "failed_checks",
    ]
    policy_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    plan_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    backend: str | None = Field(default=None, max_length=64)
    backend_version: str | None = Field(default=None, max_length=120)
    attestation_id: str | None = Field(default=None, max_length=160)
    failed_checks: list[str] = Field(default_factory=list, max_length=32)
