# M18 — Hardened Evaluator Worker Core

M18 implements the worker-side trust boundary behind the M16/M17 hidden
functional evaluator.

It adds a real worker core, private-suite loading, independent request
validation, runtime-attestation consumption, and immutable build provenance.

It deliberately does **not** claim that ordinary CI proves OS-level sandbox
isolation.

## Architecture

```text
control plane
  |
  | HTTPS handshake
  | suite ID/hash
  | full sandbox policy + policy hash
  v
HardenedEvaluatorWorker
  |
  +--> private HiddenSuiteStore
  |      suite.bundle
  |      manifest.json
  |
  +--> HardenedEvaluationBackend
         |
         +--> runtime attestation
         +--> isolated evaluation
         +--> aggregate counts only
```

The worker core does not implement subprocess, Docker, containerd, gVisor,
Firecracker, Kata, or another local launcher. The execution mechanism remains
behind `HardenedEvaluationBackend`.

That split is intentional: protocol acceptance in GitHub Actions must not be
misrepresented as proof of hardened execution.

## Full policy handshake

M17 originally sent only the sandbox policy hash during handshake.

That is insufficient for a real worker because a runtime cannot enforce or
measure CPU, memory, PID, disk, output, and wall-time ceilings without knowing
their concrete values.

M18 therefore sends both:

- the complete immutable `SandboxPolicyConfig`,
- its canonical policy hash.

The worker recomputes the hash and fails closed on mismatch.

## Private hidden-suite store

`FilesystemHiddenSuiteStore` loads an opaque package from an
operator-controlled directory:

```text
<private-root>/
  <suite-id>/
    suite.bundle
    manifest.json
```

The bundle is not part of the repository, experiment manifest, agent world, or
public Forge.

The store:

- restricts suite IDs to a safe single path component,
- prevents path escape and rejects suite-directory or leaf-file symlinks,
- reads both leaves relative to opened directories with `O_NOFOLLOW`,
- rejects nonregular entries (including FIFOs) before reading,
- reads the opaque bundle bytes,
- computes SHA-256 from the exact bytes,
- reads only aggregate metadata such as `total_cases`.

The worker compares that SHA-256 against the manifest-pinned suite hash before
opening a session. The private suite root and its parent directories must still
be operator-controlled and protected against unauthorized replacement; this
store hardening does not establish a qualified private gVisor host or M21
scientific acceptance.

## Independent worker-side validation

The worker does not trust control-plane request fields merely because transport
validation already happened.

Before backend execution it independently verifies:

- policy hash,
- evaluation-plan hash,
- suite ID and suite hash,
- plan/snapshot experiment and world identity,
- every public artifact content SHA-256,
- every artifact byte count,
- artifact bindings,
- aggregate culture-snapshot hash.

A mismatch fails before `HardenedEvaluationBackend.evaluate` is called.

This protects the worker from a compromised or buggy transport/control plane.

## Runtime attestation

The backend must generate a `SandboxAttestation` for the concrete policy.

The existing M9/M10 admission evaluator is reused unchanged for the core
isolation requirements:

- network disabled,
- no host mounts,
- no secrets,
- no shell,
- CPU limit enforced,
- memory limit enforced,
- PID limit enforced,
- disk limit enforced,
- output limit enforced,
- wall-time enforced,
- ephemeral filesystem.

The worker revalidates this evidence before handshake success and again before
evaluation.

The evaluation-time attestation must retain the same attestation ID, backend,
backend version, and policy hash as the admitted session. Silent runtime
switching fails closed.

## Evidence classification

M18 adds an explicit attestation classification:

```text
test-fixture
runtime-measured
```

All existing protocol fixtures default to `test-fixture`.

The research-readiness gate now requires
`attestation_evidence_kind = runtime-measured` in addition to:

- external-hardened runner kind,
- exact suite/policy binding,
- valid result hash,
- non-sentinel immutable build hashes.

Therefore a mock HTTP service or in-memory backend can exercise the complete
protocol without becoming scientific evidence merely because it uses the label
`external-hardened`.

`runtime-measured` is still an operational trust statement, not cryptographic
hardware remote attestation. A production deployment must define how those
measurements are obtained and audited.

## Immutable build provenance

M18 binds every evaluator result to:

- `runner_version`,
- `worker_build_sha256`,
- `runtime_build_sha256`.

Those hashes are returned at handshake, pinned for the session, checked again
on the evaluation response, incorporated into the functional report, included
in the report hash, and persisted in PostgreSQL.

This prevents the same human-readable runner version from silently referring to
different worker or runtime builds across experimental runs.

## Aggregate-only output

The worker-side public result contains only:

- request/evaluation provenance,
- suite/policy/attestation/build provenance,
- passed case count,
- failed case count,
- total case count,
- duration.

It does not return:

- hidden suite bytes,
- hidden case source,
- case names,
- expected outputs,
- stdout/stderr,
- per-case diagnostics.

The control plane remains responsible for calculating the final functional
score and persistent result hash.

## Database migration note

M18 adds non-null columns to `functional_evaluations`:

- `attestation_evidence_kind`,
- `worker_build_sha256`,
- `runtime_build_sha256`.

The test suite creates a fresh schema, so CI does not require a migration.

Any deployed database created before M18 requires an explicit schema migration
before running the new code.

## What M18 proves

M18 proves that:

- private suite bytes can remain worker-local,
- exact suite bytes are hash-bound,
- concrete sandbox policy reaches the worker,
- the worker independently validates control-plane inputs,
- runtime evidence is fail-closed through the existing admission contract,
- session runtime switching is rejected,
- outputs are aggregate-only,
- worker/runtime builds are scientifically attributable,
- fixture evidence cannot clear the research-readiness gate.

## What M18 does not prove

M18 does not prove that a specific OS isolation technology is secure or even
installed.

No claim is made yet that:

- gVisor blocks all escapes,
- Firecracker/Kata/VM isolation is configured correctly,
- kernel namespaces/cgroups are enforced,
- real network namespaces deny egress,
- real host mounts are absent,
- real PID/memory/disk ceilings survive adversarial code.

Those properties require an isolation-capable deployment environment and
adversarial tests against the actual worker image/runtime.

## Next gate — M18.1 real runtime backend

The next gate should attach one concrete backend in a dedicated environment,
for example rootless OCI + gVisor or another explicitly selected isolation
stack.

It must generate `runtime-measured` evidence from actual runtime checks and
demonstrate at minimum:

- real network denial,
- host-mount denial,
- secret absence,
- unprivileged identity,
- CPU/RAM/PID/disk/output/wall-time enforcement,
- ephemeral filesystem destruction,
- immutable runtime image/build provenance,
- adversarial escape tests.

Only after that gate should replicated P/T/E/ES/RIL pilot results be treated as
research-runtime-ready evidence.
