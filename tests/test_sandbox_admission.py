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
from dse.engine.reducer import apply_event
from dse.engine.sandbox_admission import (
    REQUIRED_SANDBOX_CHECKS,
    evaluate_and_record_sandbox_admission,
    evaluate_sandbox_admission,
    sandbox_policy_hash,
)
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.persistence.database import create_database_engine, create_schema, drop_schema
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import register_experiment
from dse.persistence.replay import restore_persisted_world


MANIFEST = Path("experiments/v0_1/sandbox-admission.yaml")
M8_MANIFEST = Path("experiments/v0_1/ephemeral-local-workspace.yaml")


def _plan() -> SandboxExecutionPlan:
    return SandboxExecutionPlan(
        plan_id="plan-1",
        action_id="action-1",
        runtime="python",
        artifact_path="code/main.py",
        artifact_sha256="a" * 64,
        arguments=["--mode", "test"],
        stdin=None,
    )


def _attestation(
    manifest,
    *,
    backend: str = "external-hardened",
    policy_hash: str | None = None,
    omitted: set[str] | None = None,
    failed: set[str] | None = None,
) -> SandboxAttestation:
    omitted = omitted or set()
    failed = failed or set()

    checks = [
        SandboxCheck(
            name=name,
            passed=name not in failed,
            detail=(
                "verified"
                if name not in failed
                else "intentionally failed for test"
            ),
        )
        for name in REQUIRED_SANDBOX_CHECKS
        if name not in omitted
    ]

    return SandboxAttestation(
        attestation_id="attestation-1",
        backend=backend,
        backend_version="test-1",
        policy_hash=policy_hash or sandbox_policy_hash(manifest),
        checks=checks,
    )


def test_m8_keeps_sandbox_execution_disabled_by_default() -> None:
    manifest = load_manifest(M8_MANIFEST)

    assert manifest.runtime.sandbox_enabled is False
    assert manifest.runtime.sandbox_policy.backend == "none"
    assert manifest.runtime.sandbox_policy.network_enabled is False
    assert manifest.runtime.sandbox_policy.host_mounts_enabled is False
    assert manifest.runtime.sandbox_policy.secrets_enabled is False
    assert manifest.runtime.sandbox_policy.shell_enabled is False


def test_m9_pins_fail_closed_policy() -> None:
    manifest = load_manifest(MANIFEST)
    policy = manifest.runtime.sandbox_policy

    assert manifest.runtime.sandbox_enabled is True
    assert policy.backend == "external-hardened"
    assert policy.network_enabled is False
    assert policy.host_mounts_enabled is False
    assert policy.secrets_enabled is False
    assert policy.shell_enabled is False
    assert policy.cpu_seconds == 2
    assert policy.memory_mb == 256
    assert policy.pids_max == 32
    assert policy.disk_mb == 64
    assert policy.output_bytes == 65536
    assert policy.wall_timeout_seconds == 5


@pytest.mark.parametrize(
    "artifact_path",
    [
        "/etc/passwd",
        "../escape.py",
        "code/../../escape.py",
        "https://example.com/payload.py",
    ],
)
def test_execution_plan_rejects_unsafe_artifact_paths(
    artifact_path: str,
) -> None:
    with pytest.raises(ValidationError):
        SandboxExecutionPlan(
            plan_id="bad-plan",
            action_id="action-1",
            runtime="python",
            artifact_path=artifact_path,
            artifact_sha256="a" * 64,
        )


def test_admission_is_fail_closed_without_runtime_evidence() -> None:
    manifest = load_manifest(MANIFEST)
    plan = _plan()

    missing = evaluate_sandbox_admission(
        manifest,
        plan,
        attestation=None,
    )
    assert missing.admitted is False
    assert missing.reason == "attestation_missing"

    wrong_backend = evaluate_sandbox_admission(
        manifest,
        plan,
        _attestation(manifest, backend="different-backend"),
    )
    assert wrong_backend.admitted is False
    assert wrong_backend.reason == "backend_mismatch"

    wrong_hash = evaluate_sandbox_admission(
        manifest,
        plan,
        _attestation(manifest, policy_hash="b" * 64),
    )
    assert wrong_hash.admitted is False
    assert wrong_hash.reason == "policy_hash_mismatch"


def test_admission_rejects_missing_or_failed_required_checks() -> None:
    manifest = load_manifest(MANIFEST)
    plan = _plan()

    missing = evaluate_sandbox_admission(
        manifest,
        plan,
        _attestation(
            manifest,
            omitted={"network_disabled"},
        ),
    )
    assert missing.admitted is False
    assert missing.reason == "missing_checks"
    assert missing.failed_checks == ["network_disabled"]

    failed = evaluate_sandbox_admission(
        manifest,
        plan,
        _attestation(
            manifest,
            failed={"no_secrets", "pid_limit_enforced"},
        ),
    )
    assert failed.admitted is False
    assert failed.reason == "failed_checks"
    assert failed.failed_checks == [
        "no_secrets",
        "pid_limit_enforced",
    ]


def test_admission_requires_exact_policy_hash_and_all_checks() -> None:
    manifest = load_manifest(MANIFEST)
    plan = _plan()

    decision = evaluate_sandbox_admission(
        manifest,
        plan,
        _attestation(manifest),
    )

    assert decision.admitted is True
    assert decision.reason == "admitted"
    assert decision.policy_hash == sandbox_policy_hash(manifest)
    assert decision.plan_id == plan.plan_id
    assert decision.action_id == plan.action_id
    assert decision.backend == "external-hardened"
    assert decision.backend_version == "test-1"
    assert decision.attestation_id == "attestation-1"
    assert decision.failed_checks == []


def test_policy_hash_changes_when_resource_limits_change() -> None:
    manifest = load_manifest(MANIFEST)

    original_hash = sandbox_policy_hash(manifest)

    changed_policy = manifest.runtime.sandbox_policy.model_copy(
        update={"memory_mb": 512}
    )
    changed_runtime = manifest.runtime.model_copy(
        update={"sandbox_policy": changed_policy}
    )
    changed_manifest = manifest.model_copy(
        update={"runtime": changed_runtime}
    )

    assert sandbox_policy_hash(changed_manifest) != original_hash


def test_audit_event_replays_without_executing_code() -> None:
    manifest = load_manifest(MANIFEST)
    plan = _plan()
    attestation = _attestation(manifest)

    expected = create_world(manifest)
    decision, event = evaluate_and_record_sandbox_admission(
        expected,
        manifest,
        actor_agent_id="agent-0001",
        plan=plan,
        attestation=attestation,
    )

    assert decision.admitted is True
    assert event.event_type == "sandbox.admission.evaluated"
    assert event.payload["decision"]["reason"] == "admitted"
    assert event.payload["plan"]["artifact_path"] == "code/main.py"
    assert expected.last_sequence_number == 1
    assert expected.agents["agent-0001"].state_version == 0

    replayed = create_world(manifest)
    apply_event(replayed, event)

    assert world_state_hash(replayed) == world_state_hash(expected)
    assert replayed.agents["agent-0001"].state_version == 0


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


def test_sandbox_admission_event_roundtrips_through_postgres(
    postgres_engine: Engine,
) -> None:
    manifest = load_manifest(MANIFEST)
    register_experiment(postgres_engine, manifest)

    expected = create_world(manifest)
    _decision, event = evaluate_and_record_sandbox_admission(
        expected,
        manifest,
        actor_agent_id="agent-0001",
        plan=_plan(),
        attestation=_attestation(manifest),
    )

    PostgresEventStore(postgres_engine).append(event)
    restored = restore_persisted_world(
        postgres_engine,
        manifest.experiment.id,
    )

    assert world_state_hash(restored) == world_state_hash(expected)
    assert restored.last_sequence_number == 1


def test_disabled_sandbox_is_denied_even_with_valid_attestation() -> None:
    manifest = load_manifest(MANIFEST)
    disabled_runtime = manifest.runtime.model_copy(
        update={"sandbox_enabled": False}
    )
    disabled_manifest = manifest.model_copy(
        update={"runtime": disabled_runtime}
    )

    decision = evaluate_sandbox_admission(
        disabled_manifest,
        _plan(),
        _attestation(disabled_manifest),
    )

    assert decision.admitted is False
    assert decision.reason == "sandbox_disabled"
