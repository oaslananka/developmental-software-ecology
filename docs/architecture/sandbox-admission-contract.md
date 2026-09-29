# M9 — Sandbox Policy and Admission Contract

M9 defines the contract that any future generated-code execution backend must satisfy before execution is allowed.

It intentionally performs **no generated-code execution**.

The architecture is:

```text
execution plan
    +
manifest-pinned sandbox policy
    +
runtime attestation
        ↓
fail-closed admission evaluator
        ↓
sandbox.admission.evaluated
```

An `admitted=true` result means only that the supplied runtime evidence matches the policy contract. M9 does not launch a process.

## Manifest policy

The policy is part of the experiment manifest and therefore part of experiment provenance.

Required hard-deny capabilities are represented as literal false values:

- network access,
- host mounts,
- secrets,
- shell execution/interpolation.

Resource ceilings are also pinned:

- CPU seconds,
- memory MB,
- PID count,
- disk MB,
- output bytes,
- wall-clock timeout.

The M9 fixture uses:

```yaml
runtime:
  sandbox_enabled: true
  sandbox_policy:
    backend: external-hardened
    network_enabled: false
    host_mounts_enabled: false
    secrets_enabled: false
    shell_enabled: false
    cpu_seconds: 2
    memory_mb: 256
    pids_max: 32
    disk_mb: 64
    output_bytes: 65536
    wall_timeout_seconds: 5
```

The `external-hardened` backend name is a contract placeholder. It does not claim that a runtime has already been attached.

## Execution plan

A `SandboxExecutionPlan` is data only.

It includes:

- plan ID,
- originating action ID,
- runtime type,
- relative artifact path,
- expected SHA-256,
- bounded argument list,
- bounded stdin.

M9 supports only a `python` runtime identifier because broad multi-runtime execution would expand scope before isolation is proven.

Artifact paths reject:

- absolute paths,
- `..` traversal,
- network-style targets containing `://`.

## Policy hash

The complete sandbox policy is canonicalized and hashed.

Any change to a resource limit or capability setting changes the policy hash.

A runtime attestation must contain the exact policy hash for the admission evaluator to accept it.

This prevents a run from silently using a weaker sandbox configuration than the manifest records.

## Runtime attestation

A `SandboxAttestation` records:

- attestation ID,
- backend identity,
- backend version,
- exact policy hash,
- named isolation checks.

Required checks are:

1. `network_disabled`
2. `no_host_mounts`
3. `no_secrets`
4. `no_shell`
5. `cpu_limit_enforced`
6. `memory_limit_enforced`
7. `pid_limit_enforced`
8. `disk_limit_enforced`
9. `output_limit_enforced`
10. `wall_timeout_enforced`
11. `ephemeral_filesystem`

Check names must be unique.

## Fail-closed admission

Admission is denied for any of these conditions:

- sandbox disabled,
- backend unconfigured,
- attestation absent,
- backend mismatch,
- policy-hash mismatch,
- missing required checks,
- one or more failed checks.

Only exact backend + policy hash + complete passing check set yields:

```text
admitted = true
reason = admitted
```

No heuristic fallback exists.

## Audit event

Every evaluated admission can be emitted as:

```text
sandbox.admission.evaluated
```

The event contains:

- execution plan,
- attestation or null,
- admission decision.

The reducer treats this as audit-only state: it advances event sequence provenance but does not mutate agent state.

This makes sandbox admission replayable and persistable without pretending that code execution occurred.

## What M9 does not do

M9 does not:

- call Docker,
- call containerd,
- call gVisor,
- call Firecracker,
- spawn subprocesses,
- run Python,
- execute generated source,
- mount workspaces,
- contact a network,
- expose secrets.

It is an execution admission contract only.

## M10 gate

A real sandbox backend may be introduced only if it can supply trustworthy runtime attestation for this contract.

The first real backend should preserve these constraints:

- rootless/unprivileged execution,
- no host Docker socket,
- no host filesystem mounts except an intentionally staged ephemeral input,
- no secrets,
- no outbound or inbound network,
- immutable or minimal base image,
- strict CPU/RAM/PID/disk/output/wall-time quotas,
- ephemeral writable filesystem,
- captured exit code/stdout/stderr,
- deterministic provenance for image/runtime version.

The Tool Broker must still refuse execution when attestation is absent or mismatched.

## Scientific boundary

M9 does not increase agent capability.

It increases confidence that a future real execution result can be attributed to a bounded, declared execution environment rather than an uncontrolled host process.
