from dse.contracts.evaluation import (
    CultureEvaluationSnapshot,
    EvaluationArtifactBinding,
    EvaluationArtifactInput,
    FunctionalEvaluationReport,
    HiddenEvaluationOutcome,
    HiddenEvaluationPlan,
    HiddenEvaluationRequest,
)
from dse.contracts.experiment import ExperimentManifest
from dse.contracts.sandbox import SandboxAttestation
from dse.engine.hashing import state_hash
from dse.engine.sandbox_admission import (
    evaluate_sandbox_runtime_evidence,
    sandbox_policy_hash_for,
)
from dse.engine.snapshot import world_state_hash
from dse.engine.world import WorldState
from dse.evaluator.base import HiddenEvaluatorRunner


class EvaluationSnapshotLimitError(ValueError):
    """Raised when a cultural snapshot exceeds manifest-pinned evaluator limits."""


def build_culture_evaluation_snapshot(
    world: WorldState,
    manifest: ExperimentManifest,
) -> CultureEvaluationSnapshot:
    config = manifest.evaluation
    opportunity = manifest.functional_opportunity
    if (
        not opportunity.enabled
        or opportunity.spec_id is None
        or opportunity.spec_sha256 is None
    ):
        raise ValueError(
            "functional opportunity is not fully configured"
        )

    artifacts: list[EvaluationArtifactInput] = []
    total_bytes = 0

    for agent_id, agent in sorted(world.agents.items()):
        submission = agent.functional_submission.current
        if submission is None:
            continue
        binding = {
            "repo_id": f"submission:{agent_id}",
            "repo_name": "current-generation-submissions",
            "artifact_id": submission.submission_id,
            "commit_id": submission.action_id,
            "path": submission.path,
            "content_sha256": submission.content_sha256,
            "content_bytes": submission.content_bytes,
            "creator_agent_id": submission.agent_id,
            "creator_generation": submission.generation,
            "parent_artifact_ids": [],
        }
        artifacts.append(
            EvaluationArtifactInput(
                **binding,
                content=submission.content,
            )
        )
        total_bytes += submission.content_bytes

    if len(artifacts) > config.max_snapshot_artifacts:
        raise EvaluationSnapshotLimitError(
            "evaluation snapshot artifact limit exceeded"
        )
    if total_bytes > config.max_snapshot_bytes:
        raise EvaluationSnapshotLimitError(
            "evaluation snapshot byte limit exceeded"
        )

    bindings = _artifact_bindings(artifacts)
    culture_snapshot_hash = state_hash(
        [binding.model_dump(mode="json") for binding in bindings]
    )

    return CultureEvaluationSnapshot(
        experiment_id=world.experiment_id,
        condition=manifest.experiment.condition,
        world_tick=world.tick,
        world_sequence=world.last_sequence_number,
        world_snapshot_hash=world_state_hash(world),
        culture_snapshot_hash=culture_snapshot_hash,
        opportunity_spec_id=opportunity.spec_id,
        opportunity_spec_hash=opportunity.spec_sha256,
        opportunity_aggregation=opportunity.aggregation,
        artifacts=artifacts,
    )


def build_hidden_evaluation_plan(
    snapshot: CultureEvaluationSnapshot,
    manifest: ExperimentManifest,
) -> HiddenEvaluationPlan:
    config = manifest.evaluation
    if not config.enabled or config.suite_id is None or config.suite_hash is None:
        raise ValueError("hidden evaluation is not fully configured")

    bindings = _artifact_bindings(snapshot.artifacts)
    identity_hash = state_hash(
        {
            "experiment_id": snapshot.experiment_id,
            "world_tick": snapshot.world_tick,
            "world_sequence": snapshot.world_sequence,
            "world_snapshot_hash": snapshot.world_snapshot_hash,
            "culture_snapshot_hash": snapshot.culture_snapshot_hash,
            "opportunity_spec_id": snapshot.opportunity_spec_id,
            "opportunity_spec_hash": snapshot.opportunity_spec_hash,
            "opportunity_aggregation": snapshot.opportunity_aggregation,
            "suite_id": config.suite_id,
            "suite_hash": config.suite_hash,
        }
    )

    return HiddenEvaluationPlan(
        evaluation_id=f"evaluation-{identity_hash[:48]}",
        experiment_id=snapshot.experiment_id,
        condition=snapshot.condition,
        world_tick=snapshot.world_tick,
        world_sequence=snapshot.world_sequence,
        world_snapshot_hash=snapshot.world_snapshot_hash,
        culture_snapshot_hash=snapshot.culture_snapshot_hash,
        opportunity_spec_id=snapshot.opportunity_spec_id,
        opportunity_spec_hash=snapshot.opportunity_spec_hash,
        opportunity_aggregation=snapshot.opportunity_aggregation,
        suite_id=config.suite_id,
        suite_hash=config.suite_hash,
        artifact_bindings=bindings,
    )


def hidden_evaluation_plan_hash(plan: HiddenEvaluationPlan) -> str:
    return state_hash(plan.model_dump(mode="json"))


def functional_evaluation_report_hash(
    report: FunctionalEvaluationReport,
) -> str:
    return state_hash(
        report.model_dump(mode="json", exclude={"result_hash"})
    )


async def evaluate_hidden_functional_culture(
    world: WorldState,
    manifest: ExperimentManifest,
    *,
    attestation: SandboxAttestation | None,
    runner: HiddenEvaluatorRunner,
) -> HiddenEvaluationOutcome:
    config = manifest.evaluation
    if not config.enabled:
        return HiddenEvaluationOutcome(
            completed=False,
            reason="evaluation_disabled",
        )

    before_world_hash = world_state_hash(world)

    try:
        snapshot = build_culture_evaluation_snapshot(world, manifest)
    except EvaluationSnapshotLimitError as error:
        return HiddenEvaluationOutcome(
            completed=False,
            reason="snapshot_limit_exceeded",
            rejection_reasons=[str(error)],
        )

    plan = build_hidden_evaluation_plan(snapshot, manifest)
    plan_hash = hidden_evaluation_plan_hash(plan)
    plan_id = f"{plan.evaluation_id}:plan"
    action_id = f"{plan.evaluation_id}:control-plane"

    admission = evaluate_sandbox_runtime_evidence(
        enabled=config.sandbox_enabled,
        policy=config.sandbox_policy,
        plan_id=plan_id,
        action_id=action_id,
        plan_hash=plan_hash,
        attestation=attestation,
    )
    if not admission.admitted:
        return HiddenEvaluationOutcome(
            completed=False,
            reason="admission_denied",
            evaluation_id=plan.evaluation_id,
            rejection_reasons=[
                admission.reason,
                *admission.failed_checks,
            ],
        )

    if attestation is None:
        raise AssertionError("admitted hidden evaluation requires attestation")

    request = HiddenEvaluationRequest(
        request_id=f"{plan.evaluation_id}:request",
        plan=plan,
        plan_hash=plan_hash,
        snapshot=snapshot,
        policy=config.sandbox_policy,
        policy_hash=admission.policy_hash,
        attestation_id=attestation.attestation_id,
        backend=attestation.backend,
        backend_version=attestation.backend_version,
    )

    result = await runner.evaluate(request)
    binding_errors = _result_binding_errors(request, result, runner)
    if binding_errors:
        return HiddenEvaluationOutcome(
            completed=False,
            reason="result_binding_mismatch",
            evaluation_id=plan.evaluation_id,
            rejection_reasons=binding_errors,
        )

    if result.total_cases > config.max_cases:
        return HiddenEvaluationOutcome(
            completed=False,
            reason="case_limit_exceeded",
            evaluation_id=plan.evaluation_id,
            rejection_reasons=[
                f"total_cases={result.total_cases}",
                f"limit={config.max_cases}",
            ],
        )

    duration_limit_ms = config.sandbox_policy.wall_timeout_seconds * 1000
    if result.duration_ms > duration_limit_ms:
        return HiddenEvaluationOutcome(
            completed=False,
            reason="duration_limit_exceeded",
            evaluation_id=plan.evaluation_id,
            rejection_reasons=[
                f"duration_ms={result.duration_ms}",
                f"limit_ms={duration_limit_ms}",
            ],
        )

    after_world_hash = world_state_hash(world)
    if after_world_hash != before_world_hash:
        return HiddenEvaluationOutcome(
            completed=False,
            reason="world_state_mutated",
            evaluation_id=plan.evaluation_id,
            rejection_reasons=[
                f"before={before_world_hash}",
                f"after={after_world_hash}",
            ],
        )

    report_material = {
        **plan.model_dump(mode="json"),
        "passed_cases": result.passed_cases,
        "failed_cases": result.failed_cases,
        "total_cases": result.total_cases,
        "functional_score": result.passed_cases / result.total_cases,
        "duration_ms": result.duration_ms,
        "policy_hash": request.policy_hash,
        "attestation_id": request.attestation_id,
        "attestation_evidence_kind": attestation.evidence_kind,
        "backend": request.backend,
        "backend_version": request.backend_version,
        "runner_kind": result.runner_kind,
        "runner_version": result.runner_version,
        "worker_build_sha256": result.worker_build_sha256,
        "runtime_build_sha256": result.runtime_build_sha256,
    }
    report = FunctionalEvaluationReport(
        **report_material,
        result_hash=state_hash(report_material),
    )
    return HiddenEvaluationOutcome(
        completed=True,
        reason="completed",
        evaluation_id=plan.evaluation_id,
        report=report,
    )


def _artifact_bindings(
    artifacts: list[EvaluationArtifactInput],
) -> list[EvaluationArtifactBinding]:
    return [
        EvaluationArtifactBinding.model_validate(
            artifact.model_dump(mode="json", exclude={"content"})
        )
        for artifact in artifacts
    ]


def _result_binding_errors(request, result, runner) -> list[str]:
    checks = (
        ("request_id", request.request_id, result.request_id),
        ("evaluation_id", request.plan.evaluation_id, result.evaluation_id),
        ("plan_hash", request.plan_hash, result.plan_hash),
        (
            "culture_snapshot_hash",
            request.plan.culture_snapshot_hash,
            result.culture_snapshot_hash,
        ),
        ("suite_hash", request.plan.suite_hash, result.suite_hash),
        ("policy_hash", request.policy_hash, result.policy_hash),
        ("attestation_id", request.attestation_id, result.attestation_id),
        ("backend", request.backend, result.backend),
        ("backend_version", request.backend_version, result.backend_version),
        ("runner_kind", runner.runner_kind, result.runner_kind),
        ("runner_version", runner.runner_version, result.runner_version),
        (
            "worker_build_sha256",
            runner.worker_build_sha256,
            result.worker_build_sha256,
        ),
        (
            "runtime_build_sha256",
            runner.runtime_build_sha256,
            result.runtime_build_sha256,
        ),
    )
    return [
        field
        for field, expected, actual in checks
        if actual != expected
    ]


def evaluation_policy_hash(manifest: ExperimentManifest) -> str:
    return sandbox_policy_hash_for(manifest.evaluation.sandbox_policy)
