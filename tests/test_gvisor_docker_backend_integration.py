import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from dse.contracts.evaluation import (
    CultureEvaluationSnapshot,
    EvaluationArtifactBinding,
    EvaluationArtifactInput,
    HiddenEvaluationPlan,
    HiddenEvaluationRequest,
)
from dse.contracts.evaluator_transport import ExternalEvaluatorHandshakeRequest
from dse.contracts.experiment import SandboxPolicyConfig
from dse.engine.hashing import state_hash
from dse.engine.sandbox_admission import (
    REQUIRED_SANDBOX_CHECKS,
    sandbox_policy_hash_for,
)
from dse.evaluator.gvisor_docker import GVisorDockerBackend
from dse.evaluator.suite_store import HiddenSuiteBundle, HiddenSuiteError
from dse.evaluator.worker import HardenedEvaluatorWorker


pytestmark = pytest.mark.skipif(
    os.environ.get("DSE_GVISOR_INTEGRATION") != "1",
    reason="requires Docker + gVisor runsc",
)

RUNTIME_IMAGE = os.environ.get(
    "DSE_GVISOR_RUNTIME_IMAGE",
    "gcr.io/distroless/python3-debian13:nonroot",
)
SUITE_ID = "m18-2-public-fixture-suite"
OPPORTUNITY_SPEC_ID = "m20-public-fixture-spec"
OPPORTUNITY_SPEC_HASH = "c" * 64
OPPORTUNITY_AGGREGATION = "population_any"
SUITE_SOURCE = b"""import json

request = DSE_REQUEST
artifacts = request["snapshot"]["artifacts"]
passed = int(any("M18_2_OK" in item["content"] for item in artifacts))
print(json.dumps({
    "passed_cases": passed,
    "failed_cases": 1 - passed,
    "total_cases": 1,
}, sort_keys=True))
"""


class MemorySuiteStore:
    def __init__(self, bundle: HiddenSuiteBundle) -> None:
        self.bundle = bundle

    def load(self, suite_id: str) -> HiddenSuiteBundle:
        if suite_id != self.bundle.suite_id:
            raise HiddenSuiteError("unknown suite")
        return self.bundle


def _policy() -> SandboxPolicyConfig:
    return SandboxPolicyConfig(
        backend="external-hardened",
        cpu_seconds=2,
        memory_mb=256,
        pids_max=32,
        disk_mb=64,
        output_bytes=65_536,
        wall_timeout_seconds=5,
    )


def _bundle() -> HiddenSuiteBundle:
    return HiddenSuiteBundle(
        suite_id=SUITE_ID,
        suite_hash=hashlib.sha256(SUITE_SOURCE).hexdigest(),
        opportunity_spec_id=OPPORTUNITY_SPEC_ID,
        opportunity_spec_hash=OPPORTUNITY_SPEC_HASH,
        opportunity_aggregation=OPPORTUNITY_AGGREGATION,
        total_cases=1,
        payload=SUITE_SOURCE,
    )


def _request(
    *,
    policy: SandboxPolicyConfig,
    bundle: HiddenSuiteBundle,
    attestation,
) -> HiddenEvaluationRequest:
    content = "M18_2_OK = True\n"
    encoded = content.encode("utf-8")
    artifact = EvaluationArtifactInput(
        repo_id="repo-m18-2",
        repo_name="m18-2-fixture",
        artifact_id="artifact-m18-2",
        commit_id="commit-m18-2",
        path="src/main.py",
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        content_bytes=len(encoded),
        creator_agent_id="agent-0001",
        creator_generation=0,
        parent_artifact_ids=[],
        content=content,
    )
    binding = EvaluationArtifactBinding.model_validate(
        artifact.model_dump(mode="json", exclude={"content"})
    )
    culture_hash = state_hash(
        [binding.model_dump(mode="json")]
    )
    snapshot = CultureEvaluationSnapshot(
        experiment_id="m18-2-runtime-integration",
        condition="E",
        world_tick=7,
        world_sequence=11,
        world_snapshot_hash="a" * 64,
        culture_snapshot_hash=culture_hash,
        opportunity_spec_id=OPPORTUNITY_SPEC_ID,
        opportunity_spec_hash=OPPORTUNITY_SPEC_HASH,
        opportunity_aggregation=OPPORTUNITY_AGGREGATION,
        artifacts=[artifact],
    )
    plan = HiddenEvaluationPlan(
        evaluation_id="m18-2-runtime-evaluation",
        experiment_id=snapshot.experiment_id,
        condition=snapshot.condition,
        world_tick=snapshot.world_tick,
        world_sequence=snapshot.world_sequence,
        world_snapshot_hash=snapshot.world_snapshot_hash,
        culture_snapshot_hash=snapshot.culture_snapshot_hash,
        opportunity_spec_id=snapshot.opportunity_spec_id,
        opportunity_spec_hash=snapshot.opportunity_spec_hash,
        opportunity_aggregation=snapshot.opportunity_aggregation,
        suite_id=bundle.suite_id,
        suite_hash=bundle.suite_hash,
        artifact_bindings=[binding],
    )
    return HiddenEvaluationRequest(
        request_id="m18-2-runtime-request",
        plan=plan,
        plan_hash=state_hash(plan.model_dump(mode="json")),
        snapshot=snapshot,
        policy=policy,
        policy_hash=sandbox_policy_hash_for(policy),
        attestation_id=attestation.attestation_id,
        backend=attestation.backend,
        backend_version=attestation.backend_version,
    )


def _docker_names() -> list[str]:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.Names}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _worker_build() -> str:
    return hashlib.sha256(
        Path("src/dse/evaluator/worker.py").read_bytes()
    ).hexdigest()


def test_gvisor_backend_produces_runtime_measured_evidence_and_evaluates() -> None:
    policy = _policy()
    bundle = _bundle()
    backend = GVisorDockerBackend(
        runtime_image=RUNTIME_IMAGE,
    )
    worker = HardenedEvaluatorWorker(
        service_id="m18-2-gvisor-worker",
        runner_version="m18-2-worker-integration-1",
        worker_build_sha256=_worker_build(),
        suite_store=MemorySuiteStore(bundle),
        backend=backend,
    )
    policy_hash = sandbox_policy_hash_for(policy)
    measured_attestation = asyncio.run(
        backend.attest(policy, policy_hash)
    )
    failed_checks = {
        check.name: check.detail
        for check in measured_attestation.checks
        if not check.passed
    }
    assert failed_checks == {}

    handshake_request = ExternalEvaluatorHandshakeRequest(
        request_id="m18-2-handshake",
        experiment_id="m18-2-runtime-integration",
        suite_id=bundle.suite_id,
        suite_hash=bundle.suite_hash,
        opportunity_spec_id=bundle.opportunity_spec_id,
        opportunity_spec_hash=bundle.opportunity_spec_hash,
        opportunity_aggregation=bundle.opportunity_aggregation,
        policy=policy,
        policy_hash=policy_hash,
    )

    handshake = asyncio.run(
        worker.handshake(handshake_request)
    )

    assert handshake.attestation.evidence_kind == "runtime-measured"
    checks = {
        check.name: check
        for check in handshake.attestation.checks
    }
    assert set(REQUIRED_SANDBOX_CHECKS) <= set(checks)
    assert all(
        checks[name].passed
        for name in REQUIRED_SANDBOX_CHECKS
    )
    assert len(handshake.runtime_build_sha256) == 64

    request = _request(
        policy=policy,
        bundle=bundle,
        attestation=handshake.attestation,
    )
    result = asyncio.run(worker.evaluate(request))

    assert result.runner_kind == "external-hardened"
    assert result.runtime_build_sha256 == (
        handshake.runtime_build_sha256
    )
    assert result.passed_cases == 1
    assert result.failed_cases == 0
    assert result.total_cases == 1
    assert not any(
        name.startswith("dse-m18-2-")
        for name in _docker_names()
    )


def test_public_fixture_suite_is_not_the_scientific_hidden_suite() -> None:
    serialized = SUITE_SOURCE.decode("utf-8")
    assert "m18-2-public-fixture-suite" not in serialized
    assert "M18_2_OK" in serialized
    assert "v0_1-hidden-functional-suite" not in serialized
