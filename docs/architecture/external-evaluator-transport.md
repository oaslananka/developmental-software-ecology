# M17 — External Evaluator Transport

M17 connects the M16 hidden-evaluator control plane to a remote evaluator
service without weakening the research-readiness gate.

It does **not** implement or claim the remote sandbox itself.

## Boundary

The control plane now supports:

```text
manifest-pinned suite + sandbox policy
        ↓
HTTPS handshake
        ↓
remote SandboxAttestation
        ↓
existing fail-closed admission checks
        ↓
bound evaluator session
        ↓
hidden evaluation request
```

A remote endpoint is not considered hardened merely because it is reachable.

The handshake attestation still has to satisfy the exact M9/M10 sandbox policy
checks.

## Transport policy

`ExternalEvaluatorClient` requires:

- HTTPS by default,
- a non-empty bearer token supplied by the control plane,
- no credentials embedded in the URL,
- no query/fragment in the base URL,
- redirects disabled,
- bounded response bytes,
- bounded request timeout,
- JSON-object responses validated through strict Pydantic contracts.

Plain HTTP exists only behind an explicit test-only constructor override.

The bearer token is an operational secret. It is placed only in the HTTP
`Authorization` header and is not part of:

- experiment manifests,
- evaluator requests,
- world state,
- world events,
- functional evaluation reports.

## Handshake

The control plane POSTs `/v1/handshake` with:

- protocol version,
- deterministic handshake request ID,
- experiment ID,
- suite ID,
- suite hash,
- evaluation sandbox policy hash.

The service returns:

- matching protocol/request identity,
- service ID,
- matching suite ID/hash,
- `runner_kind = external-hardened`,
- exact runner version,
- a `SandboxAttestation`.

The control plane rejects suite or policy drift before opening a session.

The supplied attestation is then processed by the same fail-closed sandbox
admission logic used by M9/M10/M16.

## Evaluation request

An admitted session POSTs `/v1/evaluate` using the existing
`HiddenEvaluationRequest` contract.

The remote response must validate as `HiddenEvaluationRunnerResult`.

Runner kind and runner version are pinned to the handshake. A version change
inside one session fails closed.

M16 still performs the full request/result binding check for evaluation ID,
plan hash, culture snapshot hash, suite hash, policy hash, attestation,
backend and runner provenance.

## What the handshake proves

The handshake proves that the control plane talked to a service that returned
evidence matching the declared protocol and policy.

It does **not**, by itself, provide hardware remote attestation or independently
prove that gVisor, Firecracker, Kata, a VM or another isolation technology is
actually enforcing the claims.

Therefore M17 protocol tests use a mock HTTPS transport but are explicitly only
transport acceptance fixtures.

A real research run may count as `external-hardened` only when the deployed
worker is operationally trusted and its attestation is produced from actual
runtime isolation checks.

## Next gate

The next milestone should implement and deploy the worker-side runtime:

- private hidden-suite storage,
- artifact staging into an ephemeral sandbox,
- no network/host mounts/secrets/shell interpolation,
- hard CPU/RAM/PID/disk/output/wall-time limits,
- suite-hash verification before execution,
- sandbox checks generated from the actual worker environment,
- aggregate-only evaluation response,
- immutable worker/runtime build provenance.

Only after that deployment should the first replicated P/T/E/ES/RIL pilot be
treated as research-runtime-ready.
