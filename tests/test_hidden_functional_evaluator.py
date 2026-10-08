import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import Engine

from dse.contracts.evaluation import (
    HiddenEvaluationRequest,
    HiddenEvaluationRunnerResult,
)
from dse.contracts.experiment import load_manifest
from dse.contracts.sandbox import SandboxAttestation, SandboxCheck
from dse.engine.cultural_runtime import advance_cultural_runtime_ticks
from dse.engine.hidden_evaluator import (
    build_culture_evaluation_snapshot,
    evaluate_hidden_functional_culture,
    functional_evaluation_report_hash,
)
from dse.engine.sandbox_admission import (
    REQUIRED_SANDBOX_CHECKS,
    sandbox_policy_hash_for,
)
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.experiments.conditions import (
    assess_condition_runtime_readiness,
    assess_condition_support,
)
from dse.forge.deterministic import DeterministicMemoryForgeProvider
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.evaluation_store import PostgresEvaluationStore
from dse.persistence.experiment_store import register_experiment
from dse.providers.fake import DeterministicFakeProvider


E_MANIFEST = Path("experiments/v0_1/conditions/e-executable-culture.yaml")
P_MANIFEST = Path("experiments/v0_1/conditions/p-personal.yaml")


def _check(
    condition: bool,
    message: str = "test condition failed",
) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingHiddenEvaluator:
    runner_kind = "test-double"
    runner_version = "m16-acceptance-double-1"
    worker_build_sha256 = "0" * 64
    runtime_build_sha256 = "0" * 64

    def __init__(
        self,
        *,
        tamper_field: str | None = None,
        total_cases: int = 4,
        duration_ms: int = 12,
    ) -> None:
        self.tamper_field = tamper_field
        self.total_cases = total_cases
        self.duration_ms = duration_ms
        self.calls: list[HiddenEvaluationRequest] = []
        self.private_suite_source = (
            "PRIVATE_ACCEPTANCE_SUITE_SOURCE_MUST_NEVER_LEAVE_RUNNER"
        )

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
    ) -> HiddenEvaluationRunnerResult:
        self.calls.append(request)
        passed = min(len(request.snapshot.artifacts), self.total_cases)
        values = {
            "request_id": request.request_id,
            "evaluation_id": request.plan.evaluation_id,
            "plan_hash": request.plan_hash,
            "culture_snapshot_hash": request.plan.culture_snapshot_hash,
            "suite_hash": request.plan.suite_hash,
            "policy_hash": request.policy_hash,
            "attestation_id": request.attestation_id,
            "backend": request.backend,
            "backend_version": request.backend_version,
            "runner_kind": self.runner_kind,
            "runner_version": self.runner_version,
            "worker_build_sha256": self.worker_build_sha256,
            "runtime_build_sha256": self.runtime_build_sha256,
        }

        if self.tamper_field is not None:
            current = values[self.tamper_field]
            values[self.tamper_field] = (
                "b" * 64
                if self.tamper_field
                in {
                    "plan_hash",
                    "culture_snapshot_hash",
                    "suite_hash",
                    "policy_hash",
                    "worker_build_sha256",
                    "runtime_build_sha256",
                }
                else f"{current}-tampered"
            )

        return HiddenEvaluationRunnerResult(
            **values,
            passed_cases=passed,
            failed_cases=self.total_cases - passed,
            total_cases=self.total_cases,
            duration_ms=self.duration_ms,
        )


class RunnerKindMismatchEvaluator(RecordingHiddenEvaluator):
    runner_kind = "external-hardened"

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
    ) -> HiddenEvaluationRunnerResult:
        result = await super().evaluate(request)
        return result.model_copy(update={"runner_kind": "test-double"})


def _attestation(manifest) -> SandboxAttestation:
    policy = manifest.evaluation.sandbox_policy
    return SandboxAttestation(
        attestation_id="m16-evaluator-attestation",
        backend="external-hardened",
        backend_version="acceptance-worker-1",
        policy_hash=sandbox_policy_hash_for(policy),
        checks=[
            SandboxCheck(
                name=name,
                passed=True,
                detail="verified by M16 protocol fixture",
            )
            for name in REQUIRED_SANDBOX_CHECKS
        ],
    )


def _advance_e_world(world, manifest, count: int):
    return asyncio.run(
        advance_cultural_runtime_ticks(
            world,
            manifest,
            DeterministicFakeProvider(),
            count,
            DeterministicMemoryForgeProvider(),
        )
    )


def _evaluate(world, manifest, runner):
    return asyncio.run(
        evaluate_hidden_functional_culture(
            world,
            manifest,
            attestation=_attestation(manifest),
            runner=runner,
        )
    )


def test_primary_evaluator_config_is_separate_from_agent_runtime() -> None:
    manifest = load_manifest(E_MANIFEST)

    assert manifest.runtime.sandbox_enabled is False
    assert manifest.evaluation.enabled is True
    assert manifest.evaluation.sandbox_enabled is True
    assert manifest.evaluation.sandbox_policy.backend == "external-hardened"
    assert manifest.evaluation.suite_id == "v0_1-hidden-functional-suite-m20-v1"
    assert (
        manifest.evaluation.suite_hash
        == "a869623be48e27c4fa39563596c703f1308f0b1a9a9a41c12fdf4a36c28146b1"
    )
    opportunity = manifest.functional_opportunity
    _check(opportunity.spec_id == "dse-utility-kernel-v0.1")
    _check(
        opportunity.spec_sha256
        == "8c5dbb384df0bb73e2b8db6dd9b8a97cf31ef904ecca8a5bf4411f5e8e696439"
    )
    _check(opportunity.aggregation == "population_any")
    _check(
        hashlib.sha256(opportunity.public_brief.encode("utf-8")).hexdigest()
        == opportunity.spec_sha256
    )


def test_evaluator_snapshot_contains_only_current_generation_submissions() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 120)

    snapshot = build_culture_evaluation_snapshot(world, manifest)

    expected_submissions = sum(
        agent.functional_submission.current is not None
        for agent in world.agents.values()
    )
    _check(expected_submissions == 5)
    _check(len(snapshot.artifacts) == expected_submissions)
    _check(snapshot.world_snapshot_hash == world_state_hash(world))
    opportunity = manifest.functional_opportunity
    _check(snapshot.opportunity_spec_id == opportunity.spec_id)
    _check(snapshot.opportunity_spec_hash == opportunity.spec_sha256)
    _check(snapshot.opportunity_aggregation == opportunity.aggregation)

    for artifact in snapshot.artifacts:
        agent = world.agents[artifact.creator_agent_id]
        submission = agent.functional_submission.current
        _check(submission is not None)
        _check(artifact.repo_id == f"submission:{agent.agent_id}")
        _check(artifact.artifact_id == submission.submission_id)
        _check(artifact.path == submission.path)
        _check(artifact.content == submission.content)


def test_hidden_evaluation_is_read_only_and_sanitizes_persistable_report() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 120)

    before_hash = world_state_hash(world)
    before_sequence = world.last_sequence_number
    runner = RecordingHiddenEvaluator()
    outcome = _evaluate(world, manifest, runner)

    assert outcome.completed is True
    assert outcome.reason == "completed"
    assert outcome.report is not None
    assert outcome.report.functional_score == 1.0
    assert outcome.report.passed_cases == 4
    assert outcome.report.failed_cases == 0
    assert outcome.report.artifact_bindings
    opportunity = manifest.functional_opportunity
    _check(outcome.report.opportunity_spec_id == opportunity.spec_id)
    _check(outcome.report.opportunity_spec_hash == opportunity.spec_sha256)
    _check(
        outcome.report.opportunity_aggregation
        == opportunity.aggregation
    )

    assert world_state_hash(world) == before_hash
    assert world.last_sequence_number == before_sequence

    assert len(runner.calls) == 1
    request_payload = json.dumps(
        runner.calls[0].model_dump(mode="json"),
        sort_keys=True,
    )
    report_payload = json.dumps(
        outcome.report.model_dump(mode="json"),
        sort_keys=True,
    )

    assert runner.private_suite_source not in request_payload
    assert runner.private_suite_source not in report_payload
    assert "stdout" not in report_payload
    assert "stderr" not in report_payload
    assert "case_results" not in report_payload
    assert not hasattr(outcome.report.artifact_bindings[0], "content")

    first_submission_content = runner.calls[0].snapshot.artifacts[0].content
    request_dict = runner.calls[0].model_dump(mode="json")
    report_dict = outcome.report.model_dump(mode="json")
    _check(
        request_dict["snapshot"]["artifacts"][0]["content"]
        == first_submission_content
    )
    _check("content" not in report_dict["artifact_bindings"][0])
    _check(first_submission_content not in report_payload)


def test_missing_attestation_fails_closed_without_calling_runner() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 60)
    runner = RecordingHiddenEvaluator()
    before_hash = world_state_hash(world)

    outcome = asyncio.run(
        evaluate_hidden_functional_culture(
            world,
            manifest,
            attestation=None,
            runner=runner,
        )
    )

    assert outcome.completed is False
    assert outcome.reason == "admission_denied"
    assert "attestation_missing" in outcome.rejection_reasons
    assert runner.calls == []
    assert world_state_hash(world) == before_hash


@pytest.mark.parametrize(
    "tamper_field",
    [
        "plan_hash",
        "culture_snapshot_hash",
        "suite_hash",
        "policy_hash",
        "attestation_id",
        "backend",
        "backend_version",
        "runner_version",
        "worker_build_sha256",
        "runtime_build_sha256",
    ],
)
def test_tampered_evaluator_binding_is_rejected(tamper_field: str) -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 60)

    runner = RecordingHiddenEvaluator(tamper_field=tamper_field)
    outcome = _evaluate(world, manifest, runner)

    assert outcome.completed is False
    assert outcome.reason == "result_binding_mismatch"
    assert tamper_field in outcome.rejection_reasons


def test_runner_kind_mismatch_is_rejected() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 60)

    outcome = _evaluate(world, manifest, RunnerKindMismatchEvaluator())

    assert outcome.completed is False
    assert outcome.reason == "result_binding_mismatch"
    assert "runner_kind" in outcome.rejection_reasons


def test_same_hidden_evaluator_can_score_personal_current_submission() -> None:
    manifest = load_manifest(P_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 120)
    runner = RecordingHiddenEvaluator()

    outcome = _evaluate(world, manifest, runner)

    assert outcome.completed is True
    assert outcome.report is not None
    _check(len(outcome.report.artifact_bindings) == 5)
    _check(outcome.report.passed_cases == 4)
    _check(outcome.report.failed_cases == 0)
    _check(outcome.report.functional_score == 1.0)


def test_test_double_report_cannot_clear_e_research_runtime_gate() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 120)

    report = _evaluate(
        world,
        manifest,
        RecordingHiddenEvaluator(),
    ).report
    assert report is not None

    static = assess_condition_support(manifest)
    runtime = assess_condition_runtime_readiness(manifest, report)

    assert static.missing_surfaces == [
        "attested_hardened_evaluator_runtime",
    ]
    assert runtime.research_runtime_ready is False
    assert runtime.missing_surfaces == [
        "attested_hardened_evaluator_runtime",
    ]


def test_external_hardened_report_evidence_can_clear_runtime_gate() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 120)

    report = _evaluate(
        world,
        manifest,
        RecordingHiddenEvaluator(),
    ).report
    assert report is not None

    hardened = report.model_copy(
        update={
            "runner_kind": "external-hardened",
            "attestation_evidence_kind": "runtime-measured",
            "worker_build_sha256": "1" * 64,
            "runtime_build_sha256": "2" * 64,
        }
    )
    hardened = hardened.model_copy(
        update={
            "result_hash": functional_evaluation_report_hash(hardened),
        }
    )

    readiness = assess_condition_runtime_readiness(
        manifest,
        hardened,
    )
    assert readiness.research_runtime_ready is True
    assert readiness.missing_surfaces == []


def test_hardened_report_with_opportunity_spec_drift_cannot_clear_gate() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 120)

    report = _evaluate(
        world,
        manifest,
        RecordingHiddenEvaluator(),
    ).report
    if report is None:
        raise AssertionError("fixture evaluation did not produce a report")

    drifted = report.model_copy(
        update={
            "runner_kind": "external-hardened",
            "attestation_evidence_kind": "runtime-measured",
            "worker_build_sha256": "1" * 64,
            "runtime_build_sha256": "2" * 64,
            "opportunity_spec_hash": "b" * 64,
        }
    )
    drifted = drifted.model_copy(
        update={
            "result_hash": functional_evaluation_report_hash(drifted),
        }
    )

    readiness = assess_condition_runtime_readiness(
        manifest,
        drifted,
    )
    _check(readiness.research_runtime_ready is False)
    _check(
        readiness.missing_surfaces
        == ["attested_hardened_evaluator_runtime"]
    )


def test_production_evaluator_package_has_only_approved_execution_backend() -> None:
    evaluator_files = sorted(
        path.name
        for path in Path("src/dse/evaluator").glob("*.py")
    )
    assert evaluator_files == [
        "__init__.py",
        "base.py",
        "gvisor_docker.py",
        "http.py",
        "service.py",
        "suite_store.py",
        "worker.py",
    ]

    control_plane_paths = [
        Path("src/dse/engine/hidden_evaluator.py"),
        Path("src/dse/evaluator/base.py"),
        Path("src/dse/evaluator/http.py"),
        Path("src/dse/evaluator/service.py"),
        Path("src/dse/evaluator/suite_store.py"),
        Path("src/dse/evaluator/worker.py"),
    ]
    control_plane_source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in control_plane_paths
    )
    forbidden = (
        "import subprocess",
        "from subprocess",
        "docker run",
        "containerd",
        "runsc",
        "firecracker",
    )
    assert all(
        token not in control_plane_source
        for token in forbidden
    )

    backend_source = Path(
        "src/dse/evaluator/gvisor_docker.py"
    ).read_text(encoding="utf-8").lower()
    assert "import subprocess" in backend_source
    assert '"--runtime=runsc"' in backend_source
    assert '"--network=none"' in backend_source


@pytest.fixture
def postgres_engine() -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    engine = create_database_engine(database_url)
    drop_schema(engine)
    create_schema(engine)
    yield engine
    drop_schema(engine)
    engine.dispose()


def test_sanitized_evaluation_report_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(E_MANIFEST)
    register_experiment(postgres_engine, manifest)

    world = create_world(manifest)
    _advance_e_world(world, manifest, 120)
    before_hash = world_state_hash(world)

    runner = RecordingHiddenEvaluator()
    report = _evaluate(world, manifest, runner).report
    assert report is not None

    store = PostgresEvaluationStore(postgres_engine)
    stored = store.save(report)
    restored = store.load_latest(manifest.experiment.id)

    assert restored == stored == report
    assert functional_evaluation_report_hash(restored) == restored.result_hash
    opportunity = manifest.functional_opportunity
    _check(restored.opportunity_spec_id == opportunity.spec_id)
    _check(restored.opportunity_spec_hash == opportunity.spec_sha256)
    _check(restored.opportunity_aggregation == opportunity.aggregation)
    assert world_state_hash(world) == before_hash

    serialized = json.dumps(
        restored.model_dump(mode="json"),
        sort_keys=True,
    )
    assert runner.private_suite_source not in serialized
    assert "stdout" not in serialized
    assert "stderr" not in serialized
    assert all(
        not hasattr(binding, "content")
        for binding in restored.artifact_bindings
    )
