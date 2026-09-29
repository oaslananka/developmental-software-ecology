# M10 — Hardened Sandbox Runner Protocol

M10 defines the transport/control-plane protocol for a future hardened execution worker.

It does **not** implement a production execution backend.

The production path in M10 is:

```text
SandboxExecutionPlan
        ↓
M9 fail-closed admission
        ↓ admitted only
SandboxRunRequest
        ↓
SandboxRunner Protocol
        ↓
SandboxRunResult
        ↓
control-plane binding + limit revalidation
        ↓
completed OR result_rejected audit event
```

## No production runner implementation

The production package contains only:

```text
src/dse/sandbox/
  __init__.py
  base.py
```

`base.py` defines the asynchronous `SandboxRunner` Protocol.

There is no production Docker, containerd, gVisor, Firecracker, shell, subprocess, or Python execution adapter in M10.

Tests use an in-memory test double solely to verify the protocol.

## Admission is mandatory

`dispatch_sandbox_run` always evaluates M9 admission first.

If admission is denied:

- no run request is created,
- the runner is not called,
- only `sandbox.admission.evaluated` is emitted.

There is no bypass parameter.

## Run request

An admitted request carries:

- deterministic request ID,
- admission ID,
- full execution plan,
- plan hash,
- full manifest-pinned sandbox policy snapshot,
- policy hash,
- attestation ID,
- backend identity,
- backend version.

The full policy snapshot is included so a future external worker receives the concrete resource limits it must enforce, not merely a hash.

## Worker result

A `SandboxRunResult` must bind back to the request with:

- request ID,
- admission ID,
- plan ID,
- action ID,
- plan hash,
- policy hash,
- artifact SHA-256,
- attestation ID,
- backend,
- backend version.

It also carries:

- process status: succeeded / failed / timed_out,
- exit code,
- stdout,
- stderr,
- duration.

Exit semantics are strict:

- `succeeded` requires exit code 0,
- `failed` requires a non-zero exit code.

A process failure is not itself a protocol failure. A correctly bound `failed` result can still be a completed sandbox execution record.

## Control-plane revalidation

The control plane does not trust the worker result blindly.

After the worker returns, it rechecks:

1. every provenance/binding field,
2. combined stdout+stderr byte count against manifest `output_bytes`,
3. duration against manifest `wall_timeout_seconds`.

A mismatch emits:

```text
sandbox.execution.result_rejected
```

Rejected-result events store hashes, metadata, byte counts, and rejection reasons without persisting potentially oversized stdout/stderr.

## Audit events

A valid protocol flow emits:

```text
sandbox.admission.evaluated
sandbox.execution.dispatched
sandbox.execution.completed
```

A tampered or limit-violating result emits:

```text
sandbox.admission.evaluated
sandbox.execution.dispatched
sandbox.execution.result_rejected
```

These are audit-only reducer events. They advance event provenance without mutating agent state.

## Result hash

The control plane computes a canonical hash of the complete validated worker result.

The worker does not get to choose the control-plane result hash.

## Replay and persistence

All M10 audit events are replayable and persist through PostgreSQL.

Replay reconstructs the recorded control-plane history. It does not re-run the external worker.

## What M10 proves

M10 proves the system has a fail-closed protocol boundary where:

- admission precedes dispatch,
- policy travels with the request,
- worker output is provenance-bound,
- control-plane resource limits are rechecked,
- tampered results are rejected,
- execution evidence is event-sourced.

## What M10 does not prove

M10 does not prove any operating-system sandbox exists or is secure.

No claim of gVisor/Firecracker/container isolation is made until a real backend is attached and its attestation evidence is independently verified.

## M11 gate

A future real execution backend must implement `SandboxRunner` and must not weaken M9/M10 semantics.

Before merge, M11 should demonstrate in an environment capable of the chosen isolation technology:

- real network denial,
- real host-mount denial,
- secret absence,
- rootless/unprivileged process identity,
- CPU/RAM/PID/disk/output/wall-time enforcement,
- ephemeral filesystem behavior,
- runtime/image/version provenance,
- adversarial escape tests.

If the available CI environment cannot prove those properties, the backend must remain optional and the hardened integration gate must run in a dedicated environment.
