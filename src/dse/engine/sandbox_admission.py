from dse.contracts.event import WorldEvent, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.contracts.sandbox import (
    SandboxAdmissionDecision,
    SandboxAttestation,
    SandboxExecutionPlan,
)
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.world import WorldState


REQUIRED_SANDBOX_CHECKS = (
    "network_disabled",
    "no_host_mounts",
    "no_secrets",
    "no_shell",
    "cpu_limit_enforced",
    "memory_limit_enforced",
    "pid_limit_enforced",
    "disk_limit_enforced",
    "output_limit_enforced",
    "wall_timeout_enforced",
    "ephemeral_filesystem",
)


def sandbox_policy_hash_for(policy) -> str:
    return state_hash(policy.model_dump(mode="json"))


def sandbox_policy_hash(manifest: ExperimentManifest) -> str:
    return sandbox_policy_hash_for(manifest.runtime.sandbox_policy)


def sandbox_plan_hash(plan: SandboxExecutionPlan) -> str:
    return state_hash(plan.model_dump(mode="json"))


def evaluate_sandbox_runtime_evidence(
    *,
    enabled: bool,
    policy,
    plan_id: str,
    action_id: str,
    plan_hash: str,
    attestation: SandboxAttestation | None,
) -> SandboxAdmissionDecision:
    policy_hash = sandbox_policy_hash_for(policy)
    base = {
        "admission_id": f"{plan_id}:admission",
        "plan_id": plan_id,
        "action_id": action_id,
        "policy_hash": policy_hash,
        "plan_hash": plan_hash,
    }

    if not enabled:
        return SandboxAdmissionDecision(
            **base,
            admitted=False,
            reason="sandbox_disabled",
        )

    if policy.backend == "none":
        return SandboxAdmissionDecision(
            **base,
            admitted=False,
            reason="backend_unconfigured",
        )

    if attestation is None:
        return SandboxAdmissionDecision(
            **base,
            admitted=False,
            reason="attestation_missing",
            backend=policy.backend,
        )

    attestation_fields = {
        "backend": attestation.backend,
        "backend_version": attestation.backend_version,
        "attestation_id": attestation.attestation_id,
    }

    if attestation.backend != policy.backend:
        return SandboxAdmissionDecision(
            **base,
            **attestation_fields,
            admitted=False,
            reason="backend_mismatch",
        )

    if attestation.policy_hash != policy_hash:
        return SandboxAdmissionDecision(
            **base,
            **attestation_fields,
            admitted=False,
            reason="policy_hash_mismatch",
        )

    checks = {check.name: check for check in attestation.checks}
    missing = [
        name
        for name in REQUIRED_SANDBOX_CHECKS
        if name not in checks
    ]
    if missing:
        return SandboxAdmissionDecision(
            **base,
            **attestation_fields,
            admitted=False,
            reason="missing_checks",
            failed_checks=missing,
        )

    failed = [
        name
        for name in REQUIRED_SANDBOX_CHECKS
        if not checks[name].passed
    ]
    if failed:
        return SandboxAdmissionDecision(
            **base,
            **attestation_fields,
            admitted=False,
            reason="failed_checks",
            failed_checks=failed,
        )

    return SandboxAdmissionDecision(
        **base,
        **attestation_fields,
        admitted=True,
        reason="admitted",
    )


def evaluate_sandbox_admission(
    manifest: ExperimentManifest,
    plan: SandboxExecutionPlan,
    attestation: SandboxAttestation | None,
) -> SandboxAdmissionDecision:
    return evaluate_sandbox_runtime_evidence(
        enabled=manifest.runtime.sandbox_enabled,
        policy=manifest.runtime.sandbox_policy,
        plan_id=plan.plan_id,
        action_id=plan.action_id,
        plan_hash=sandbox_plan_hash(plan),
        attestation=attestation,
    )


def evaluate_and_record_sandbox_admission(
    world: WorldState,
    manifest: ExperimentManifest,
    *,
    actor_agent_id: str,
    plan: SandboxExecutionPlan,
    attestation: SandboxAttestation | None,
) -> tuple[SandboxAdmissionDecision, WorldEvent]:
    if actor_agent_id not in world.agents:
        raise ValueError(f"Unknown agent: {actor_agent_id}")

    decision = evaluate_sandbox_admission(
        manifest,
        plan,
        attestation,
    )

    event = make_event(
        experiment_id=world.experiment_id,
        sequence_number=world.last_sequence_number + 1,
        world_tick=world.tick,
        event_type="sandbox.admission.evaluated",
        actor_agent_id=actor_agent_id,
        payload={
            "plan": plan.model_dump(mode="json"),
            "attestation": (
                attestation.model_dump(mode="json")
                if attestation is not None
                else None
            ),
            "decision": decision.model_dump(mode="json"),
        },
    )
    apply_event(world, event)
    return decision, event
