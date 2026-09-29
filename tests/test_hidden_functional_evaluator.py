import asyncio
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


class RecordingHiddenEvaluator:
    runner_kind = "test-double"
    runner_version = "m16-acceptance-double-1"

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
    assert manifest.evaluation.suite_id == "v0_1-hidden-functional-suite"
    assert len(manifest.evaluation.suite_hash or "") == 64


def test_evaluator_snapshot_contains_only_current_public_forge_heads() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 100)

    snapshot = build_culture_evaluation_snapshot(world, manifest)

    expected_heads = sum(
        len(repository.path_heads)
        for repository in world.forge.repositories.values()
    )
    assert len(snapshot.artifacts) == expected_heads
    assert len(snapshot.artifacts) > 0
    assert snapshot.world_snapshot_hash == world_state_hash(world)

    for artifact in snapshot.artifacts:
        repository = world.forge.repositories[artifact.repo_id]
        assert repository.path_heads[artifact.path] == artifact.artifact_id


def test_hidden_evaluation_is_read_only_and_sanitizes_persistable_report() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 100)

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

    first_public_content = runner.calls[0].snapshot.artifacts[0].content
    request_dict = runner.calls[0].model_dump(mode="json")
    report_dict = outcome.report.model_dump(mode="json")
    assert request_dict["snapshot"]["artifacts"][0]["content"] == first_public_content
    assert "content" not in report_dict["artifact_bindings"][0]
    assert first_public_content not in report_payload


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


def test_same_hidden_evaluator_can_score_empty_personal_public_culture() -> None:
    manifest = load_manifest(P_MANIFEST)
    world = create_world(manifest)
    runner = RecordingHiddenEvaluator()

    outcome = _evaluate(world, manifest, runner)

    assert outcome.completed is True
    assert outcome.report is not None
    assert outcome.report.artifact_bindings == []
    assert outcome.report.passed_cases == 0
    assert outcome.report.failed_cases == 4
    assert outcome.report.functional_score == 0.0


def test_test_double_report_cannot_clear_e_research_runtime_gate() -> None:
    manifest = load_manifest(E_MANIFEST)
    world = create_world(manifest)
    _advance_e_world(world, manifest, 100)

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
    _advance_e_world(world, manifest, 100)

    report = _evaluate(
        world,
        manifest,
        RecordingHiddenEvaluator(),
    ).report
    assert report is not None

    hardened = report.model_copy(
        update={"runner_kind": "external-hardened"}
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


def test_production_evaluator_package_contains_transport_not_execution_backend() -> None:
    evaluator_files = sorted(
        path.name
        for path in Path("src/dse/evaluator").glob("*.py")
    )
    assert evaluator_files == ["__init__.py", "base.py", "http.py"]

    source_paths = [
        Path("src/dse/engine/hidden_evaluator.py"),
        *Path("src/dse/evaluator").glob("*.py"),
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in source_paths
    )
    forbidden = (
        "import subprocess",
        "from subprocess",
        "docker run",
        "containerd",
        "runsc",
        "firecracker",
    )
    assert all(token not in source for token in forbidden)


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
    _advance_e_world(world, manifest, 100)
    before_hash = world_state_hash(world)

    runner = RecordingHiddenEvaluator()
    report = _evaluate(world, manifest, runner).report
    assert report is not None

    store = PostgresEvaluationStore(postgres_engine)
    stored = store.save(report)
    restored = store.load_latest(manifest.experiment.id)

    assert restored == stored == report
    assert functional_evaluation_report_hash(restored) == restored.result_hash
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
