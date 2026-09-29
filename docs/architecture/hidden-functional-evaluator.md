# M16 — Hidden Functional Evaluator and Hardened Execution Boundary

M16 implements the measurement plane required by the V0.1 research design.

The evaluator is deliberately separate from the agent world.

Its purpose is to answer:

> What objectively executable capability exists in the public culture at this snapshot?

without telling agents which capabilities are being tested.

## Non-negotiable boundary

Hidden test source, prompts, expected outputs, fixtures and case-level diagnostics must never enter:

- agent cognition context,
- agent private memory,
- public text culture,
- social messages,
- Forge,
- WorldState,
- world events,
- persisted world snapshots.

Only a suite identity and cryptographic hash are manifest-visible.

## Separate evaluation config

The evaluator is top-level experiment configuration rather than daily agent runtime.

The five strict P/T/E/ES/RIL YAML manifests select one immutable versioned profile:

```yaml
evaluation_profile: v0_1-hidden-functional-suite
```

That profile resolves to the full pinned suite identity, limits and
`external-hardened` sandbox policy before manifest validation. The canonical
manifest persisted for provenance contains both the profile identifier and the
fully resolved evaluation config. Re-loading that canonical form is accepted
only when the expanded config exactly matches the selected profile; drift fails
closed.

This removes duplicated condition YAML while preserving a complete immutable
manifest hash and keeps evaluator configuration identical without changing the
agent compute budget by condition.

## Evaluation snapshot

The evaluator reads a copy of the public Forge state.

Only current repository path heads enter the evaluation snapshot.

The runner receives public artifact content because it must stage the current software culture, but the persisted report contains only sanitized bindings:

- repo ID/name,
- artifact ID,
- commit ID,
- path,
- content SHA-256,
- content byte count,
- creator agent/generation,
- parent artifact IDs.

The culture snapshot hash is computed from those bindings. The separate world snapshot hash binds the evaluation to one exact world state.

## Hidden suite privacy

The runner request contains suite ID/hash, the public culture snapshot, sandbox policy/hash, and attestation/backend binding. It does not contain hidden test source.

A real external evaluator backend is expected to hold the hidden suite privately and verify that its local suite bytes match the pinned suite hash.

M16's CI runner double is only an acceptance fixture and cannot satisfy the research-runtime gate.

## Reuse of M9/M10 sandbox protocol

M16 does not add a local subprocess, Docker, gVisor or Firecracker launcher.

It reuses the existing fail-closed sandbox evidence model:

- exact backend match,
- exact policy hash,
- network disabled,
- no host mounts,
- no secrets,
- no shell,
- CPU limit,
- memory limit,
- PID limit,
- disk limit,
- output limit,
- wall-time limit,
- ephemeral filesystem.

The generic attestation verifier is shared by normal sandbox admission and the hidden evaluator.

## Runner protocol

Production code contains only a `HiddenEvaluatorRunner` Protocol with explicit runner kinds:

```text
test-double
external-hardened
```

The control plane validates that the result is bound to request ID, evaluation ID, plan hash, culture snapshot hash, suite hash, sandbox policy hash, attestation ID, backend/version, and runner kind/version.

A mismatch fails closed.

## Objective metric

The worker returns only aggregate counts:

- passed cases,
- failed cases,
- total cases,
- duration.

The primary score is:

```text
Functional Culture Score = passed_cases / total_cases
```

No LLM judge is required.

Case source, case names, stdout/stderr and hidden diagnostics are not part of the persistent report contract.

## Read-only world guarantee

Evaluation does not emit world events and does not mutate WorldState.

The evaluator records the world-state hash before and after evaluation and fails if they differ.

```text
agent world evolution
        ↓ snapshot copy
hidden evaluator
        ↓
separate scientific result

world_state_hash before == world_state_hash after
```

## Separate PostgreSQL store

Sanitized results are persisted in `functional_evaluations`, not `world_events`.

The row stores:

- experiment/evaluation identity,
- world tick/sequence,
- world and culture snapshot hashes,
- suite ID/hash,
- sanitized artifact bindings,
- aggregate pass/fail counts and score,
- duration,
- sandbox policy hash,
- attestation/backend/version,
- runner kind/version,
- result hash.

This keeps scientific measurement durable without making it part of agent-visible or replayed world state.

## Research-runtime readiness

M16 separates two questions:

1. Is the evaluator contract implemented/configured?
2. Did this run actually produce evidence from an attested external-hardened evaluator?

After M16, E/ES static support should no longer report that the hidden evaluator is absent. Instead they remain gated by:

```text
attested_hardened_evaluator_runtime
```

A `test-double` result cannot clear this gate.

Only a matching `external-hardened` result bound to the configured suite and policy can do so.

## Scientific boundary

M16 proves the evaluation protocol, isolation contract, sanitized persistence and readiness gate.

It does not prove that deterministic M12-M15 fixtures have useful executable capability.

That requires actual hidden functional tests against real agent-produced software in replicated experiment runs.
