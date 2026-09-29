from dse.contracts.event import WorldEvent, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.contracts.sandbox import SandboxAttestation, SandboxExecutionPlan
from dse.contracts.sandbox_runner import (
    SandboxDispatchOutcome,
    SandboxRunRequest,
    SandboxRunResult,
)
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.sandbox_admission import evaluate_and_record_sandbox_admission
from dse.engine.world import WorldState
from dse.sandbox.base import SandboxRunner


async def dispatch_sandbox_run(
    world: WorldState,
    manifest: ExperimentManifest,
    *,
    actor_agent_id: str,
    plan: SandboxExecutionPlan,
    attestation: SandboxAttestation | None,
    runner: SandboxRunner,
) -> tuple[SandboxDispatchOutcome, list[WorldEvent]]:
    admission, admission_event = evaluate_and_record_sandbox_admission(
        world,
        manifest,
        actor_agent_id=actor_agent_id,
        plan=plan,
        attestation=attestation,
    )
    events = [admission_event]

    if not admission.admitted:
        return (
            SandboxDispatchOutcome(
                admission=admission,
                dispatched=False,
                completed=False,
                reason="admission_denied",
            ),
            events,
        )

    if attestation is None:
        raise AssertionError("admitted execution must have attestation")

    request = SandboxRunRequest(
        request_id=f"{admission.admission_id}:run",
        admission_id=admission.admission_id,
        plan=plan,
        plan_hash=admission.plan_hash,
        policy=manifest.runtime.sandbox_policy,
        policy_hash=admission.policy_hash,
        attestation_id=attestation.attestation_id,
        backend=attestation.backend,
        backend_version=attestation.backend_version,
    )

    events.append(
        _emit(
            world,
            event_type="sandbox.execution.dispatched",
            actor_agent_id=actor_agent_id,
            payload={"request": request.model_dump(mode="json")},
        )
    )

    result = await runner.run(request)
    result_hash = state_hash(result.model_dump(mode="json"))

    binding_errors = _binding_errors(request, result)
    if binding_errors:
        events.append(
            _emit_result_rejected(
                world,
                actor_agent_id=actor_agent_id,
                request=request,
                result=result,
                result_hash=result_hash,
                reason="result_binding_mismatch",
                rejection_reasons=binding_errors,
            )
        )
        return (
            SandboxDispatchOutcome(
                admission=admission,
                dispatched=True,
                completed=False,
                reason="result_binding_mismatch",
                request_id=request.request_id,
                result_hash=result_hash,
                rejection_reasons=binding_errors,
            ),
            events,
        )

    output_bytes = len(result.stdout.encode("utf-8")) + len(
        result.stderr.encode("utf-8")
    )
    if output_bytes > manifest.runtime.sandbox_policy.output_bytes:
        rejection_reasons = [
            f"output_bytes={output_bytes}",
            (
                "limit="
                f"{manifest.runtime.sandbox_policy.output_bytes}"
            ),
        ]
        events.append(
            _emit_result_rejected(
                world,
                actor_agent_id=actor_agent_id,
                request=request,
                result=result,
                result_hash=result_hash,
                reason="result_output_limit_exceeded",
                rejection_reasons=rejection_reasons,
            )
        )
        return (
            SandboxDispatchOutcome(
                admission=admission,
                dispatched=True,
                completed=False,
                reason="result_output_limit_exceeded",
                request_id=request.request_id,
                result_hash=result_hash,
                rejection_reasons=rejection_reasons,
            ),
            events,
        )

    duration_limit_ms = (
        manifest.runtime.sandbox_policy.wall_timeout_seconds * 1000
    )
    if result.duration_ms > duration_limit_ms:
        rejection_reasons = [
            f"duration_ms={result.duration_ms}",
            f"limit_ms={duration_limit_ms}",
        ]
        events.append(
            _emit_result_rejected(
                world,
                actor_agent_id=actor_agent_id,
                request=request,
                result=result,
                result_hash=result_hash,
                reason="result_duration_limit_exceeded",
                rejection_reasons=rejection_reasons,
            )
        )
        return (
            SandboxDispatchOutcome(
                admission=admission,
                dispatched=True,
                completed=False,
                reason="result_duration_limit_exceeded",
                request_id=request.request_id,
                result_hash=result_hash,
                rejection_reasons=rejection_reasons,
            ),
            events,
        )

    events.append(
        _emit(
            world,
            event_type="sandbox.execution.completed",
            actor_agent_id=actor_agent_id,
            payload={
                "request_id": request.request_id,
                "result_hash": result_hash,
                "result": result.model_dump(mode="json"),
            },
        )
    )
    return (
        SandboxDispatchOutcome(
            admission=admission,
            dispatched=True,
            completed=True,
            reason="completed",
            request_id=request.request_id,
            result_hash=result_hash,
        ),
        events,
    )


def _binding_errors(
    request: SandboxRunRequest,
    result: SandboxRunResult,
) -> list[str]:
    expected = {
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
    actual = {
        "request_id": result.request_id,
        "admission_id": result.admission_id,
        "plan_id": result.plan_id,
        "action_id": result.action_id,
        "plan_hash": result.plan_hash,
        "policy_hash": result.policy_hash,
        "artifact_sha256": result.artifact_sha256,
        "attestation_id": result.attestation_id,
        "backend": result.backend,
        "backend_version": result.backend_version,
    }

    return [
        field
        for field, expected_value in expected.items()
        if actual[field] != expected_value
    ]


def _emit_result_rejected(
    world: WorldState,
    *,
    actor_agent_id: str,
    request: SandboxRunRequest,
    result: SandboxRunResult,
    result_hash: str,
    reason: str,
    rejection_reasons: list[str],
) -> WorldEvent:
    return _emit(
        world,
        event_type="sandbox.execution.result_rejected",
        actor_agent_id=actor_agent_id,
        payload={
            "request_id": request.request_id,
            "result_hash": result_hash,
            "reason": reason,
            "rejection_reasons": rejection_reasons,
            "result_metadata": {
                "request_id": result.request_id,
                "admission_id": result.admission_id,
                "plan_id": result.plan_id,
                "action_id": result.action_id,
                "plan_hash": result.plan_hash,
                "policy_hash": result.policy_hash,
                "artifact_sha256": result.artifact_sha256,
                "attestation_id": result.attestation_id,
                "backend": result.backend,
                "backend_version": result.backend_version,
                "status": result.status,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "stdout_bytes": len(result.stdout.encode("utf-8")),
                "stderr_bytes": len(result.stderr.encode("utf-8")),
            },
        },
    )


def _emit(
    world: WorldState,
    *,
    event_type: str,
    actor_agent_id: str,
    payload: dict,
) -> WorldEvent:
    event = make_event(
        experiment_id=world.experiment_id,
        sequence_number=world.last_sequence_number + 1,
        world_tick=world.tick,
        event_type=event_type,
        actor_agent_id=actor_agent_id,
        payload=payload,
    )
    apply_event(world, event)
    return event
