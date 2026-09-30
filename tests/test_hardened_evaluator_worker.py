import asyncio
import hashlib
import json
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
from dse.contracts.evaluator_worker import WorkerEvaluationAggregate
from dse.contracts.experiment import load_manifest
from dse.contracts.sandbox import SandboxAttestation, SandboxCheck
from dse.engine.hashing import state_hash
from dse.engine.hidden_evaluator import evaluate_hidden_functional_culture
from dse.engine.sandbox_admission import (
    REQUIRED_SANDBOX_CHECKS,
    sandbox_policy_hash_for,
)
from dse.engine.world import create_world
from dse.evaluator.suite_store import (
    FilesystemHiddenSuiteStore,
    HiddenSuiteBundle,
    HiddenSuiteError,
)
from dse.evaluator.worker import (
    EvaluatorWorkerError,
    HardenedEvaluatorWorker,
)
from dse.experiments.conditions import assess_condition_runtime_readiness


E_MANIFEST = Path("experiments/v0_1/conditions/e-executable-culture.yaml")
WORKER_BUILD = "1" * 64
RUNTIME_BUILD = "2" * 64
PRIVATE_SUITE_BYTES = b"PRIVATE_M18_SUITE_MUST_NEVER_LEAVE_WORKER"


def _check(
    condition: bool,
    message: str = "test condition failed",
) -> None:
    if not condition:
        raise AssertionError(message)


class MemorySuiteStore:
    def __init__(self, bundle: HiddenSuiteBundle) -> None:
        self.bundle = bundle
        self.loads: list[str] = []

    def load(self, suite_id: str) -> HiddenSuiteBundle:
        self.loads.append(suite_id)
        if suite_id != self.bundle.suite_id:
            raise HiddenSuiteError("unknown suite")
        return self.bundle


class RecordingBackend:
    backend = "external-hardened"
    backend_version = "m18-fixture-backend-1"
    runtime_build_sha256 = RUNTIME_BUILD

    def __init__(
        self,
        *,
        failed_check: str | None = None,
        rotate_attestation: bool = False,
        total_cases: int = 1,
        evidence_kind: str = "test-fixture",
    ) -> None:
        self.failed_check = failed_check
        self.rotate_attestation = rotate_attestation
        self.total_cases = total_cases
        self.evidence_kind = evidence_kind
        self.attest_calls = 0
        self.evaluate_calls = 0
        self.last_suite_payload: bytes | None = None

    async def attest(self, policy, policy_hash: str) -> SandboxAttestation:
        self.attest_calls += 1
        suffix = (
            str(self.attest_calls)
            if self.rotate_attestation
            else "stable"
        )
        return SandboxAttestation(
            attestation_id=f"m18-attestation-{suffix}",
            evidence_kind=self.evidence_kind,
            backend=self.backend,
            backend_version=self.backend_version,
            policy_hash=policy_hash,
            checks=[
                SandboxCheck(
                    name=name,
                    passed=name != self.failed_check,
                    detail=(
                        "measured by M18 fixture"
                        if name != self.failed_check
                        else "intentional fixture failure"
                    ),
                )
                for name in REQUIRED_SANDBOX_CHECKS
            ],
        )

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
        suite: HiddenSuiteBundle,
    ) -> WorkerEvaluationAggregate:
        self.evaluate_calls += 1
        self.last_suite_payload = suite.payload
        return WorkerEvaluationAggregate(
            passed_cases=self.total_cases,
            failed_cases=0,
            total_cases=self.total_cases,
            duration_ms=8,
        )


def _bundle(manifest, *, total_cases: int = 1) -> HiddenSuiteBundle:
    opportunity = manifest.functional_opportunity
    if opportunity.spec_id is None or opportunity.spec_sha256 is None:
        raise AssertionError("fixture requires functional opportunity spec")
    return HiddenSuiteBundle(
        suite_id=manifest.evaluation.suite_id,
        suite_hash=manifest.evaluation.suite_hash,
        opportunity_spec_id=opportunity.spec_id,
        opportunity_spec_hash=opportunity.spec_sha256,
        opportunity_aggregation=opportunity.aggregation,
        total_cases=total_cases,
        payload=PRIVATE_SUITE_BYTES,
    )


def _worker(
    manifest,
    *,
    backend: RecordingBackend | None = None,
    bundle: HiddenSuiteBundle | None = None,
) -> tuple[HardenedEvaluatorWorker, RecordingBackend]:
    selected_backend = backend or RecordingBackend()
    worker = HardenedEvaluatorWorker(
        service_id="m18-worker-fixture",
        runner_version="m18-worker-core-1",
        worker_build_sha256=WORKER_BUILD,
        suite_store=MemorySuiteStore(bundle or _bundle(manifest)),
        backend=selected_backend,
    )
    return worker, selected_backend


def _handshake_request(manifest) -> ExternalEvaluatorHandshakeRequest:
    policy = manifest.evaluation.sandbox_policy
    opportunity = manifest.functional_opportunity
    if opportunity.spec_id is None or opportunity.spec_sha256 is None:
        raise AssertionError("fixture requires functional opportunity spec")
    return ExternalEvaluatorHandshakeRequest(
        request_id="m18-handshake-request",
        experiment_id=manifest.experiment.id,
        suite_id=manifest.evaluation.suite_id,
        suite_hash=manifest.evaluation.suite_hash,
        opportunity_spec_id=opportunity.spec_id,
        opportunity_spec_hash=opportunity.spec_sha256,
        opportunity_aggregation=opportunity.aggregation,
        policy=policy,
        policy_hash=sandbox_policy_hash_for(policy),
    )


def _evaluation_request(
    manifest,
    attestation: SandboxAttestation,
    *,
    content: str = "print('m18')\n",
) -> HiddenEvaluationRequest:
    encoded = content.encode("utf-8")
    artifact = EvaluationArtifactInput(
        repo_id="repo-m18",
        repo_name="m18-fixture",
        artifact_id="artifact-m18",
        commit_id="commit-m18",
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
    culture_hash = state_hash([binding.model_dump(mode="json")])
    opportunity = manifest.functional_opportunity
    if opportunity.spec_id is None or opportunity.spec_sha256 is None:
        raise AssertionError("fixture requires functional opportunity spec")
    snapshot = CultureEvaluationSnapshot(
        experiment_id=manifest.experiment.id,
        condition=manifest.experiment.condition,
        world_tick=7,
        world_sequence=11,
        world_snapshot_hash="a" * 64,
        culture_snapshot_hash=culture_hash,
        opportunity_spec_id=opportunity.spec_id,
        opportunity_spec_hash=opportunity.spec_sha256,
        opportunity_aggregation=opportunity.aggregation,
        artifacts=[artifact],
    )
    plan = HiddenEvaluationPlan(
        evaluation_id="evaluation-m18-fixture",
        experiment_id=snapshot.experiment_id,
        condition=snapshot.condition,
        world_tick=snapshot.world_tick,
        world_sequence=snapshot.world_sequence,
        world_snapshot_hash=snapshot.world_snapshot_hash,
        culture_snapshot_hash=snapshot.culture_snapshot_hash,
        opportunity_spec_id=snapshot.opportunity_spec_id,
        opportunity_spec_hash=snapshot.opportunity_spec_hash,
        opportunity_aggregation=snapshot.opportunity_aggregation,
        suite_id=manifest.evaluation.suite_id,
        suite_hash=manifest.evaluation.suite_hash,
        artifact_bindings=[binding],
    )
    policy = manifest.evaluation.sandbox_policy
    return HiddenEvaluationRequest(
        request_id="evaluation-m18-fixture:request",
        plan=plan,
        plan_hash=state_hash(plan.model_dump(mode="json")),
        snapshot=snapshot,
        policy=policy,
        policy_hash=sandbox_policy_hash_for(policy),
        attestation_id=attestation.attestation_id,
        backend=attestation.backend,
        backend_version=attestation.backend_version,
    )


def test_filesystem_suite_store_hashes_private_bundle(tmp_path: Path) -> None:
    suite_id = "private-suite-v1"
    suite_dir = tmp_path / suite_id
    suite_dir.mkdir()
    payload = b"opaque-private-suite-bytes"
    (suite_dir / "suite.bundle").write_bytes(payload)
    (suite_dir / "manifest.json").write_text(
        json.dumps(
            {
                "total_cases": 3,
                "opportunity_spec_id": "fixture-spec",
                "opportunity_spec_hash": "c" * 64,
                "opportunity_aggregation": "population_any",
            }
        ),
        encoding="utf-8",
    )

    bundle = FilesystemHiddenSuiteStore(tmp_path).load(suite_id)

    assert bundle.suite_id == suite_id
    assert bundle.suite_hash == hashlib.sha256(payload).hexdigest()
    assert bundle.total_cases == 3
    _check(bundle.opportunity_spec_id == "fixture-spec")
    _check(bundle.opportunity_spec_hash == "c" * 64)
    _check(bundle.opportunity_aggregation == "population_any")
    assert bundle.payload == payload


@pytest.mark.parametrize("suite_id", ["../escape", "nested/name", "/absolute"])
def test_filesystem_suite_store_rejects_unsafe_suite_ids(
    tmp_path: Path,
    suite_id: str,
) -> None:
    with pytest.raises(HiddenSuiteError, match="invalid hidden suite id"):
        FilesystemHiddenSuiteStore(tmp_path).load(suite_id)


def test_worker_rejects_handshake_policy_hash_drift() -> None:
    manifest = load_manifest(E_MANIFEST)
    worker, backend = _worker(manifest)
    request = _handshake_request(manifest).model_copy(
        update={"policy_hash": "b" * 64}
    )

    with pytest.raises(EvaluatorWorkerError, match="policy hash mismatch"):
        asyncio.run(worker.handshake(request))

    _check(backend.attest_calls == 0)


def test_worker_rejects_private_suite_hash_drift() -> None:
    manifest = load_manifest(E_MANIFEST)
    opportunity = manifest.functional_opportunity
    if opportunity.spec_id is None or opportunity.spec_sha256 is None:
        raise AssertionError("fixture requires functional opportunity spec")
    mismatched = HiddenSuiteBundle(
        suite_id=manifest.evaluation.suite_id,
        suite_hash="b" * 64,
        opportunity_spec_id=opportunity.spec_id,
        opportunity_spec_hash=opportunity.spec_sha256,
        opportunity_aggregation=opportunity.aggregation,
        total_cases=1,
        payload=PRIVATE_SUITE_BYTES,
    )
    worker, backend = _worker(manifest, bundle=mismatched)

    with pytest.raises(EvaluatorWorkerError, match="hidden suite hash mismatch"):
        asyncio.run(worker.handshake(_handshake_request(manifest)))

    _check(backend.attest_calls == 0)


def test_worker_rejects_handshake_opportunity_spec_drift() -> None:
    manifest = load_manifest(E_MANIFEST)
    worker, backend = _worker(manifest)
    request = _handshake_request(manifest).model_copy(
        update={"opportunity_spec_hash": "b" * 64}
    )

    with pytest.raises(
        EvaluatorWorkerError,
        match="hidden suite opportunity binding mismatch",
    ):
        asyncio.run(worker.handshake(request))

    _check(backend.attest_calls == 0)


def test_worker_rejects_failed_runtime_attestation() -> None:
    manifest = load_manifest(E_MANIFEST)
    backend = RecordingBackend(failed_check="network_disabled")
    worker, _ = _worker(manifest, backend=backend)

    with pytest.raises(
        EvaluatorWorkerError,
        match="runtime attestation rejected",
    ):
        asyncio.run(worker.handshake(_handshake_request(manifest)))


def test_worker_handshake_exposes_hashes_not_private_suite_bytes() -> None:
    manifest = load_manifest(E_MANIFEST)
    worker, _ = _worker(manifest)

    response = asyncio.run(worker.handshake(_handshake_request(manifest)))
    serialized = json.dumps(response.model_dump(mode="json"), sort_keys=True)

    assert response.suite_hash == manifest.evaluation.suite_hash
    assert response.worker_build_sha256 == WORKER_BUILD
    assert response.runtime_build_sha256 == RUNTIME_BUILD
    assert response.attestation.evidence_kind == "test-fixture"
    assert PRIVATE_SUITE_BYTES.decode() not in serialized


def test_worker_revalidates_artifact_content_before_backend_execution() -> None:
    manifest = load_manifest(E_MANIFEST)
    worker, backend = _worker(manifest)
    handshake = asyncio.run(worker.handshake(_handshake_request(manifest)))
    request = _evaluation_request(manifest, handshake.attestation)

    artifact = request.snapshot.artifacts[0].model_copy(
        update={"content": "tampered\n"}
    )
    snapshot = request.snapshot.model_copy(update={"artifacts": [artifact]})
    tampered = request.model_copy(update={"snapshot": snapshot})

    with pytest.raises(EvaluatorWorkerError, match="artifact content hash mismatch"):
        asyncio.run(worker.evaluate(tampered))

    _check(backend.evaluate_calls == 0)


def test_worker_rejects_opportunity_spec_drift() -> None:
    manifest = load_manifest(E_MANIFEST)
    worker, backend = _worker(manifest)
    handshake = asyncio.run(worker.handshake(_handshake_request(manifest)))
    request = _evaluation_request(manifest, handshake.attestation)

    drifted_plan = request.plan.model_copy(
        update={"opportunity_spec_hash": "b" * 64}
    )
    tampered = request.model_copy(
        update={
            "plan": drifted_plan,
            "plan_hash": state_hash(drifted_plan.model_dump(mode="json")),
        }
    )

    with pytest.raises(
        EvaluatorWorkerError,
        match="hidden suite opportunity binding mismatch: opportunity_spec_hash",
    ):
        asyncio.run(worker.evaluate(tampered))

    _check(backend.evaluate_calls == 0)


def test_worker_rejects_attestation_rotation_after_handshake() -> None:
    manifest = load_manifest(E_MANIFEST)
    backend = RecordingBackend(rotate_attestation=True)
    worker, _ = _worker(manifest, backend=backend)
    handshake = asyncio.run(worker.handshake(_handshake_request(manifest)))
    request = _evaluation_request(manifest, handshake.attestation)

    with pytest.raises(
        EvaluatorWorkerError,
        match="runtime attestation changed after handshake",
    ):
        asyncio.run(worker.evaluate(request))

    _check(backend.evaluate_calls == 0)


def test_worker_rejects_aggregate_case_count_drift() -> None:
    manifest = load_manifest(E_MANIFEST)
    backend = RecordingBackend(total_cases=2)
    worker, _ = _worker(manifest, backend=backend)
    handshake = asyncio.run(worker.handshake(_handshake_request(manifest)))
    request = _evaluation_request(manifest, handshake.attestation)

    with pytest.raises(
        EvaluatorWorkerError,
        match="total_cases does not match private suite manifest",
    ):
        asyncio.run(worker.evaluate(request))


def test_worker_result_is_aggregate_only_and_build_bound() -> None:
    manifest = load_manifest(E_MANIFEST)
    worker, backend = _worker(manifest)
    handshake = asyncio.run(worker.handshake(_handshake_request(manifest)))
    request = _evaluation_request(manifest, handshake.attestation)

    result = asyncio.run(worker.evaluate(request))
    serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert result.passed_cases == 1
    assert result.failed_cases == 0
    assert result.total_cases == 1
    assert result.worker_build_sha256 == WORKER_BUILD
    assert result.runtime_build_sha256 == RUNTIME_BUILD
    assert backend.last_suite_payload == PRIVATE_SUITE_BYTES
    assert PRIVATE_SUITE_BYTES.decode() not in serialized
    assert "stdout" not in serialized
    assert "stderr" not in serialized
    assert "case_results" not in serialized


def test_worker_core_integrates_with_control_plane_but_fixture_stays_not_ready() -> None:
    manifest = load_manifest(E_MANIFEST)
    worker, _ = _worker(manifest)
    handshake = asyncio.run(worker.handshake(_handshake_request(manifest)))
    world = create_world(manifest)

    outcome = asyncio.run(
        evaluate_hidden_functional_culture(
            world,
            manifest,
            attestation=handshake.attestation,
            runner=worker,
        )
    )

    assert outcome.completed is True
    assert outcome.report is not None
    assert outcome.report.runner_kind == "external-hardened"
    assert outcome.report.attestation_evidence_kind == "test-fixture"
    assert outcome.report.worker_build_sha256 == WORKER_BUILD
    assert outcome.report.runtime_build_sha256 == RUNTIME_BUILD

    readiness = assess_condition_runtime_readiness(manifest, outcome.report)
    assert readiness.research_runtime_ready is False
    assert readiness.missing_surfaces == [
        "attested_hardened_evaluator_runtime",
    ]


def test_worker_can_preserve_runtime_measured_evidence_classification() -> None:
    manifest = load_manifest(E_MANIFEST)
    backend = RecordingBackend(evidence_kind="runtime-measured")
    worker, _ = _worker(manifest, backend=backend)
    handshake = asyncio.run(worker.handshake(_handshake_request(manifest)))
    world = create_world(manifest)

    outcome = asyncio.run(
        evaluate_hidden_functional_culture(
            world,
            manifest,
            attestation=handshake.attestation,
            runner=worker,
        )
    )

    assert outcome.completed is True
    assert outcome.report is not None
    assert outcome.report.attestation_evidence_kind == "runtime-measured"
    readiness = assess_condition_runtime_readiness(manifest, outcome.report)
    assert readiness.research_runtime_ready is True
    assert readiness.missing_surfaces == []
