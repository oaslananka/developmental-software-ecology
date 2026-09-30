import hashlib
from typing import Protocol

from dse.contracts.evaluation import (
    EvaluationArtifactBinding,
    HiddenEvaluationRequest,
    HiddenEvaluationRunnerResult,
)
from dse.contracts.evaluator_transport import (
    ExternalEvaluatorHandshakeRequest,
    ExternalEvaluatorHandshakeResponse,
)
from dse.contracts.evaluator_worker import WorkerEvaluationAggregate
from dse.contracts.experiment import SandboxPolicyConfig
from dse.contracts.sandbox import SandboxAttestation
from dse.engine.hashing import state_hash
from dse.engine.sandbox_admission import (
    evaluate_sandbox_runtime_evidence,
    sandbox_policy_hash_for,
)
from dse.evaluator.suite_store import HiddenSuiteBundle, HiddenSuiteStore


class EvaluatorWorkerError(ValueError):
    """Raised when worker-side validation fails closed."""


class HardenedEvaluationBackend(Protocol):
    backend: str
    backend_version: str
    runtime_build_sha256: str

    async def attest(
        self,
        policy: SandboxPolicyConfig,
        policy_hash: str,
    ) -> SandboxAttestation:
        """Return evidence measured from the current isolated runtime."""
        ...

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
        suite: HiddenSuiteBundle,
    ) -> WorkerEvaluationAggregate:
        """Execute the private suite inside the backend's isolated runtime."""
        ...


class HardenedEvaluatorWorker:
    runner_kind = "external-hardened"

    def __init__(
        self,
        *,
        service_id: str,
        runner_version: str,
        worker_build_sha256: str,
        suite_store: HiddenSuiteStore,
        backend: HardenedEvaluationBackend,
    ) -> None:
        if not service_id:
            raise ValueError("service_id must not be empty")
        if not runner_version:
            raise ValueError("runner_version must not be empty")
        _require_sha256("worker_build_sha256", worker_build_sha256)
        _require_sha256(
            "backend.runtime_build_sha256",
            backend.runtime_build_sha256,
        )
        if backend.backend != "external-hardened":
            raise ValueError(
                "hardened evaluator backend must identify as external-hardened"
            )

        self.service_id = service_id
        self.runner_version = runner_version
        self.worker_build_sha256 = worker_build_sha256
        self.suite_store = suite_store
        self.backend = backend

    @property
    def runtime_build_sha256(self) -> str:
        return self.backend.runtime_build_sha256

    async def handshake(
        self,
        request: ExternalEvaluatorHandshakeRequest,
    ) -> ExternalEvaluatorHandshakeResponse:
        policy_hash = sandbox_policy_hash_for(request.policy)
        if request.policy_hash != policy_hash:
            raise EvaluatorWorkerError("handshake policy hash mismatch")

        suite = self.suite_store.load(request.suite_id)
        if suite.suite_hash != request.suite_hash:
            raise EvaluatorWorkerError("hidden suite hash mismatch")
        _require_suite_opportunity_binding(
            suite,
            spec_id=request.opportunity_spec_id,
            spec_hash=request.opportunity_spec_hash,
            aggregation=request.opportunity_aggregation,
        )

        attestation = await self._admitted_attestation(
            request.policy,
            request.policy_hash,
            plan_id=f"{request.request_id}:plan",
            action_id=f"{request.request_id}:handshake",
            plan_hash=state_hash(request.model_dump(mode="json")),
        )

        return ExternalEvaluatorHandshakeResponse(
            request_id=request.request_id,
            service_id=self.service_id,
            suite_id=suite.suite_id,
            suite_hash=suite.suite_hash,
            opportunity_spec_id=suite.opportunity_spec_id,
            opportunity_spec_hash=suite.opportunity_spec_hash,
            opportunity_aggregation=suite.opportunity_aggregation,
            runner_kind=self.runner_kind,
            runner_version=self.runner_version,
            worker_build_sha256=self.worker_build_sha256,
            runtime_build_sha256=self.runtime_build_sha256,
            attestation=attestation,
        )

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
    ) -> HiddenEvaluationRunnerResult:
        suite = self.suite_store.load(request.plan.suite_id)
        self._validate_request(request, suite)

        attestation = await self._admitted_attestation(
            request.policy,
            request.policy_hash,
            plan_id=f"{request.plan.evaluation_id}:worker-plan",
            action_id=f"{request.plan.evaluation_id}:worker-evaluate",
            plan_hash=request.plan_hash,
        )
        _require_same_attestation(request, attestation)

        aggregate = await self.backend.evaluate(request, suite)
        if aggregate.total_cases != suite.total_cases:
            raise EvaluatorWorkerError(
                "backend total_cases does not match private suite manifest"
            )

        duration_limit_ms = request.policy.wall_timeout_seconds * 1000
        if aggregate.duration_ms > duration_limit_ms:
            raise EvaluatorWorkerError(
                "backend duration exceeds manifest-pinned wall timeout"
            )

        return HiddenEvaluationRunnerResult(
            request_id=request.request_id,
            evaluation_id=request.plan.evaluation_id,
            plan_hash=request.plan_hash,
            culture_snapshot_hash=request.plan.culture_snapshot_hash,
            suite_hash=request.plan.suite_hash,
            policy_hash=request.policy_hash,
            attestation_id=request.attestation_id,
            backend=request.backend,
            backend_version=request.backend_version,
            runner_kind=self.runner_kind,
            runner_version=self.runner_version,
            worker_build_sha256=self.worker_build_sha256,
            runtime_build_sha256=self.runtime_build_sha256,
            passed_cases=aggregate.passed_cases,
            failed_cases=aggregate.failed_cases,
            total_cases=aggregate.total_cases,
            duration_ms=aggregate.duration_ms,
        )

    async def _admitted_attestation(
        self,
        policy: SandboxPolicyConfig,
        policy_hash: str,
        *,
        plan_id: str,
        action_id: str,
        plan_hash: str,
    ) -> SandboxAttestation:
        attestation = await self.backend.attest(policy, policy_hash)

        if attestation.backend != self.backend.backend:
            raise EvaluatorWorkerError("backend attestation identity mismatch")
        if attestation.backend_version != self.backend.backend_version:
            raise EvaluatorWorkerError("backend attestation version mismatch")

        admission = evaluate_sandbox_runtime_evidence(
            enabled=True,
            policy=policy,
            plan_id=plan_id,
            action_id=action_id,
            plan_hash=plan_hash,
            attestation=attestation,
        )
        if not admission.admitted:
            reasons = [admission.reason, *admission.failed_checks]
            raise EvaluatorWorkerError(
                "worker runtime attestation rejected: " + ", ".join(reasons)
            )
        return attestation

    def _validate_request(
        self,
        request: HiddenEvaluationRequest,
        suite: HiddenSuiteBundle,
    ) -> None:
        if request.policy_hash != sandbox_policy_hash_for(request.policy):
            raise EvaluatorWorkerError("evaluation policy hash mismatch")
        if request.plan_hash != state_hash(request.plan.model_dump(mode="json")):
            raise EvaluatorWorkerError("evaluation plan hash mismatch")
        if request.plan.suite_hash != suite.suite_hash:
            raise EvaluatorWorkerError("evaluation suite hash mismatch")
        if request.plan.suite_id != suite.suite_id:
            raise EvaluatorWorkerError("evaluation suite id mismatch")
        _require_suite_opportunity_binding(
            suite,
            spec_id=request.plan.opportunity_spec_id,
            spec_hash=request.plan.opportunity_spec_hash,
            aggregation=request.plan.opportunity_aggregation,
        )

        identity_fields = (
            "experiment_id",
            "condition",
            "world_tick",
            "world_sequence",
            "world_snapshot_hash",
            "culture_snapshot_hash",
            "opportunity_spec_id",
            "opportunity_spec_hash",
            "opportunity_aggregation",
        )
        for field in identity_fields:
            if getattr(request.plan, field) != getattr(request.snapshot, field):
                raise EvaluatorWorkerError(
                    f"evaluation plan/snapshot mismatch: {field}"
                )

        bindings = []
        for artifact in request.snapshot.artifacts:
            encoded = artifact.content.encode("utf-8")
            if hashlib.sha256(encoded).hexdigest() != artifact.content_sha256:
                raise EvaluatorWorkerError("artifact content hash mismatch")
            if len(encoded) != artifact.content_bytes:
                raise EvaluatorWorkerError("artifact content byte count mismatch")
            bindings.append(
                EvaluationArtifactBinding.model_validate(
                    artifact.model_dump(mode="json", exclude={"content"})
                )
            )

        if bindings != request.plan.artifact_bindings:
            raise EvaluatorWorkerError("artifact binding mismatch")

        culture_hash = state_hash(
            [binding.model_dump(mode="json") for binding in bindings]
        )
        if culture_hash != request.snapshot.culture_snapshot_hash:
            raise EvaluatorWorkerError("culture snapshot hash mismatch")


def _require_suite_opportunity_binding(
    suite: HiddenSuiteBundle,
    *,
    spec_id: str,
    spec_hash: str,
    aggregation: str,
) -> None:
    checks = (
        ("opportunity_spec_id", suite.opportunity_spec_id, spec_id),
        ("opportunity_spec_hash", suite.opportunity_spec_hash, spec_hash),
        (
            "opportunity_aggregation",
            suite.opportunity_aggregation,
            aggregation,
        ),
    )
    mismatches = [
        field
        for field, expected, actual in checks
        if expected != actual
    ]
    if mismatches:
        raise EvaluatorWorkerError(
            "hidden suite opportunity binding mismatch: "
            + ", ".join(mismatches)
        )


def _require_same_attestation(
    request: HiddenEvaluationRequest,
    attestation: SandboxAttestation,
) -> None:
    checks = (
        ("attestation_id", request.attestation_id, attestation.attestation_id),
        ("backend", request.backend, attestation.backend),
        ("backend_version", request.backend_version, attestation.backend_version),
        ("policy_hash", request.policy_hash, attestation.policy_hash),
    )
    mismatches = [
        field
        for field, expected, actual in checks
        if expected != actual
    ]
    if mismatches:
        raise EvaluatorWorkerError(
            "runtime attestation changed after handshake: "
            + ", ".join(mismatches)
        )


def _require_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef"
        for character in value
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
