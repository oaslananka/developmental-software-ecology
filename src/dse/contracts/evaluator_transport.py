from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dse.contracts.experiment import SandboxPolicyConfig
from dse.contracts.sandbox import SandboxAttestation


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExternalEvaluatorHandshakeRequest(StrictModel):
    protocol_version: Literal["0.1"] = "0.1"
    request_id: str = Field(min_length=1, max_length=200)
    experiment_id: str = Field(min_length=1, max_length=200)
    suite_id: str = Field(min_length=1, max_length=160)
    suite_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    policy: SandboxPolicyConfig
    policy_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )


class ExternalEvaluatorHandshakeResponse(StrictModel):
    protocol_version: Literal["0.1"] = "0.1"
    request_id: str = Field(min_length=1, max_length=200)
    service_id: str = Field(min_length=1, max_length=160)
    suite_id: str = Field(min_length=1, max_length=160)
    suite_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    runner_kind: Literal["external-hardened"]
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
    attestation: SandboxAttestation
