import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from dse.contracts.experiment import load_manifest
from dse.contracts.sandbox import (
    SandboxAttestation,
    SandboxCheck,
    SandboxExecutionPlan,
)
from dse.contracts.sandbox_runner import (
    SandboxRunRequest,
    SandboxRunResult,
)
from dse.engine.reducer import apply_event
from dse.engine.sandbox_admission import (
    REQUIRED_SANDBOX_CHECKS,
    sandbox_plan_hash,
    sandbox_policy_hash,
)
from dse.engine.sandbox_dispatch import dispatch_sandbox_run
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world


MANIFEST = Path("experiments/v0_1/sandbox-runner-protocol.yaml")


def _plan() -> SandboxExecutionPlan:
    return SandboxExecutionPlan(
        plan_id="plan-1",
        action_id="action-1",
        runtime="python",
        artifact_path="code/main.py",
        artifact_sha256="a" * 64,
        arguments=["--mode", "test"],
    )


def _attestation(manifest) -> SandboxAttestation:
    return SandboxAttestation(
        attestation_id="attestation-1",
        backend="external-hardened",
        backend_version="worker-test-1",
        policy_hash=sandbox_policy_hash(manifest),
        checks=[
            SandboxCheck(
                name=name,
                passed=True,
                detail="verified by test worker",
            )
            for name in REQUIRED_SANDBOX_CHECKS
        ],
    )


class RecordingRunner:
    def __init__(
        self,
        *,
        tamper_field: str | None = None,
        stdout: str = "ok\n",
        stderr: str = "",
        duration_ms: int = 10,
        status: str = "succeeded",
        exit_code: int | None = 0,
    ) -> None:
        self.tamper_field = tamper_field
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.status = status
        self.exit_code = exit_code
        self.calls: list[SandboxRunRequest] = []

    async def run(self, request: SandboxRunRequest) -> SandboxRunResult:
        self.calls.append(request)

        values = {
            "request_id": request.request_id,
            "admission_id": request.admission_id,
            "plan_id": request.plan.plan_id,
            "action_id": request.plan.action_id,
            "plan_hash": request.plan_hash,
            "policy_hash": request.policy_hash,
            "artifact_sha256": request.plan.artifact_sha256,
            "attestation_id": request.attestation_id,
            "backend": request.backend,
            "backend_version": request.backend_version,
        }

        if self.tamper_field is not None:
            if self.tamper_field not in values:
                raise ValueError(f"unsupported tamper field: {self.tamper_field}")
            current = values[self.tamper_field]
            values[self.tamper_field] = (
                "b" * 64
                if self.tamper_field
                in {"plan_hash", "policy_hash", "artifact_sha256"}
                else f"{current}-tampered"
            )

        return SandboxRunResult(
            **values,
            status=self.status,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_ms=self.duration_ms,
        )


def test_m10_production_contains_protocol_not_execution_backend() -> None:
    sandbox_files = sorted(
        path.name
        for path in Path("src/dse/sandbox").glob("*.py")
    )
    assert sandbox_files == ["__init__.py", "base.py"]

    source = Path("src/dse/engine/sandbox_dispatch.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "import subprocess",
        "from subprocess",
        "docker run",
        "containerd",
        "runsc",
        "firecracker",
    )
    assert all(token not in source.lower() for token in forbidden)


def test_admission_denial_prevents_dispatch() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)
    runner = RecordingRunner()

    outcome, events = asyncio.run(
        dispatch_sandbox_run(
            world,
            manifest,
            actor_agent_id="agent-0001",
            plan=_plan(),
            attestation=None,
            runner=runner,
        )
    )

    assert outcome.dispatched is False
    assert outcome.completed is False
    assert outcome.reason == "admission_denied"
    assert runner.calls == []
    assert [event.event_type for event in events] == [
        "sandbox.admission.evaluated"
    ]


def test_valid_admission_dispatches_bound_request_and_accepts_result() -> None:
    manifest = load_manifest(MANIFEST)
    plan = _plan()
    attestation = _attestation(manifest)
    world = create_world(manifest)
    runner = RecordingRunner()

    outcome, events = asyncio.run(
        dispatch_sandbox_run(
            world,
            manifest,
            actor_agent_id="agent-0001",
            plan=plan,
            attestation=attestation,
            runner=runner,
        )
    )

    assert outcome.dispatched is True
    assert outcome.completed is True
    assert outcome.reason == "completed"
    assert len(runner.calls) == 1

    request = runner.calls[0]
    assert request.plan == plan
    assert request.plan_hash == sandbox_plan_hash(plan)
    assert request.policy == manifest.runtime.sandbox_policy
    assert request.policy_hash == sandbox_policy_hash(manifest)
    assert request.attestation_id == attestation.attestation_id
    assert request.backend == attestation.backend
    assert request.backend_version == attestation.backend_version

    assert [event.event_type for event in events] == [
        "sandbox.admission.evaluated",
        "sandbox.execution.dispatched",
        "sandbox.execution.completed",
    ]
    assert events[-1].payload["result_hash"] == outcome.result_hash


@pytest.mark.parametrize(
    "tamper_field",
    [
        "request_id",
        "admission_id",
        "plan_id",
        "action_id",
        "plan_hash",
        "policy_hash",
        "artifact_sha256",
        "attestation_id",
        "backend",
        "backend_version",
    ],
)
def test_tampered_worker_binding_is_rejected(tamper_field: str) -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)
    runner = RecordingRunner(tamper_field=tamper_field)

    outcome, events = asyncio.run(
        dispatch_sandbox_run(
            world,
            manifest,
            actor_agent_id="agent-0001",
            plan=_plan(),
            attestation=_attestation(manifest),
            runner=runner,
        )
    )

    assert outcome.dispatched is True
    assert outcome.completed is False
    assert outcome.reason == "result_binding_mismatch"
    assert tamper_field in outcome.rejection_reasons
    assert events[-1].event_type == "sandbox.execution.result_rejected"
    assert "stdout" not in events[-1].payload["result_metadata"]
    assert "stderr" not in events[-1].payload["result_metadata"]


def test_control_plane_rechecks_output_limit() -> None:
    manifest = load_manifest(MANIFEST)
    limit = manifest.runtime.sandbox_policy.output_bytes
    world = create_world(manifest)
    runner = RecordingRunner(stdout="x" * (limit + 1))

    outcome, events = asyncio.run(
        dispatch_sandbox_run(
            world,
            manifest,
            actor_agent_id="agent-0001",
            plan=_plan(),
            attestation=_attestation(manifest),
            runner=runner,
        )
    )

    assert outcome.completed is False
    assert outcome.reason == "result_output_limit_exceeded"
    assert events[-1].event_type == "sandbox.execution.result_rejected"


def test_control_plane_rechecks_duration_limit() -> None:
    manifest = load_manifest(MANIFEST)
    duration_ms = (
        manifest.runtime.sandbox_policy.wall_timeout_seconds * 1000 + 1
    )
    world = create_world(manifest)
    runner = RecordingRunner(duration_ms=duration_ms)

    outcome, events = asyncio.run(
        dispatch_sandbox_run(
            world,
            manifest,
            actor_agent_id="agent-0001",
            plan=_plan(),
            attestation=_attestation(manifest),
            runner=runner,
        )
    )

    assert outcome.completed is False
    assert outcome.reason == "result_duration_limit_exceeded"
    assert events[-1].event_type == "sandbox.execution.result_rejected"


def test_failed_process_result_can_be_protocol_complete() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)
    runner = RecordingRunner(
        status="failed",
        exit_code=7,
        stderr="test process failed\n",
    )

    outcome, events = asyncio.run(
        dispatch_sandbox_run(
            world,
            manifest,
            actor_agent_id="agent-0001",
            plan=_plan(),
            attestation=_attestation(manifest),
            runner=runner,
        )
    )

    assert outcome.completed is True
    assert outcome.reason == "completed"
    assert events[-1].payload["result"]["status"] == "failed"
    assert events[-1].payload["result"]["exit_code"] == 7


def test_result_contract_rejects_invalid_exit_semantics() -> None:
    manifest = load_manifest(MANIFEST)
    plan = _plan()
    attestation = _attestation(manifest)
    request = SandboxRunRequest(
        request_id="request-1",
        admission_id="admission-1",
        plan=plan,
        plan_hash=sandbox_plan_hash(plan),
        policy=manifest.runtime.sandbox_policy,
        policy_hash=sandbox_policy_hash(manifest),
        attestation_id=attestation.attestation_id,
        backend=attestation.backend,
        backend_version=attestation.backend_version,
    )

    with pytest.raises(ValidationError):
        SandboxRunResult(
            request_id=request.request_id,
            admission_id=request.admission_id,
            plan_id=plan.plan_id,
            action_id=plan.action_id,
            plan_hash=request.plan_hash,
            policy_hash=request.policy_hash,
            artifact_sha256=plan.artifact_sha256,
            attestation_id=request.attestation_id,
            backend=request.backend,
            backend_version=request.backend_version,
            status="succeeded",
            exit_code=1,
            duration_ms=1,
        )


def test_dispatch_event_stream_replays_exactly() -> None:
    manifest = load_manifest(MANIFEST)
    expected = create_world(manifest)

    _outcome, events = asyncio.run(
        dispatch_sandbox_run(
            expected,
            manifest,
            actor_agent_id="agent-0001",
            plan=_plan(),
            attestation=_attestation(manifest),
            runner=RecordingRunner(),
        )
    )

    replayed = create_world(manifest)
    for event in events:
        apply_event(replayed, event)

    assert world_state_hash(replayed) == world_state_hash(expected)
    assert expected.agents["agent-0001"].state_version == 0


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


def test_dispatch_events_roundtrip_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    expected = create_world(manifest)
    _outcome, events = asyncio.run(
        dispatch_sandbox_run(
            expected,
            manifest,
            actor_agent_id="agent-0001",
            plan=_plan(),
            attestation=_attestation(manifest),
            runner=RecordingRunner(),
        )
    )

    PostgresEventStore(postgres_engine).append_many(events)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert restored.last_sequence_number == 3
