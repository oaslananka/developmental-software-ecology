# M16 — Hidden Functional Evaluator and Hardened Execution Boundary

M16 implements the measurement plane required by the V0.1 research design.

The evaluator is deliberately separate from the agent world.

Its original M16 purpose was to answer what objectively executable capability
exists in public culture without revealing held-out cases.

M19 refines the causal measurement boundary: the same hidden evaluator now
scores a treatment-independent **current-generation functional submission**.
Persistent culture may help an agent construct that submission, but executable
Forge access is no longer itself the evaluator input.

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

## Public opportunity binding

M20 separates the public task contract from the private held-out cases.

Strict V0.1 runs use:

```text
spec_id     = dse-utility-kernel-v0.1
spec_sha256 = 8c5dbb384df0bb73e2b8db6dd9b8a97cf31ef904ecca8a5bf4411f5e8e696439
aggregation = population_any
```

The exact semantics are public in
`docs/research/v0.1-functional-opportunity.md`.

The evaluator handshake protocol is v0.2. The client request, worker-side
private suite manifest, culture snapshot, evaluation plan and persisted report
must all carry the same opportunity identity/hash/aggregation.

A mismatch is a protocol/evidence failure; it must not be interpreted as a low
functional score.

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

As of M19, the evaluator reads only current-generation functional submissions.

Each agent slot may expose one bounded submission through the same public
opportunity profile. The submission is deleted on turnover and must be
re-created by the replacement generation.

The existing evaluation artifact schema is retained for wire/storage
compatibility. A submission is mapped to a sanitized artifact binding with:

- synthetic submission repo ID/name;
- submission ID as artifact ID;
- source action ID as commit ID;
- canonical submission path;
- content SHA-256;
- content byte count;
- creator agent/generation;
- no cultural parent-artifact IDs.

The runner receives submission content because it must execute the current
candidate against private held-out cases. The persisted report contains only
the sanitized bindings above.

Persistent Forge/text/social state is **not** copied directly into this
evaluation payload. Those treatment-specific substrates can only affect the
score indirectly through what the current generation produces.

The historical field name `culture_snapshot_hash` remains in the M16-M18
wire contract for compatibility; after M19 it hashes the current evaluation
submission bindings. The separate world snapshot hash still binds evaluation
to one exact world state.

## Hidden suite privacy

The runner request contains suite ID/hash, the bounded current-generation
submission snapshot, sandbox policy/hash, and attestation/backend binding. It
does not contain hidden test source.

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

The persisted scalar remains:

```text
functional_score = passed_cases / total_cases
```

After M19, interpret this as current-generation executable capability under the
common submission contract. Cultural accumulation is inferred from
cross-treatment and across-turnover trajectories, not from the mere presence
of Forge artifacts.

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

After M19, **all five** strict P/T/E/ES/RIL conditions remain gated by:

```text
attested_hardened_evaluator_runtime
```

because the common functional outcome is required for every treatment.

A `test-double` result cannot clear this gate.

Only a matching `external-hardened` result bound to the configured suite and policy can do so.

## Scientific boundary

M16 proves the evaluation protocol, isolation contract and sanitized
persistence. M19 corrects the cross-condition measurement substrate.

Neither milestone proves that deterministic fixtures have useful executable
capability. The substantive public problem family and real private held-out
suite still have to be frozen and accepted before replicated experiment runs.
