# M18.2 — gVisor Docker evaluator backend

M18.2 connects the M18 hardened evaluator worker contract to a concrete
Docker + gVisor `runsc` execution path.

The scientific boundary remains fail closed: a public fixture can prove the
backend architecture and measured isolation path, but it is not the private
V0.1 hidden suite and is not by itself a replicated cultural-evolution result.

## Runtime image

The evaluator uses a shell-less distroless Python runtime by default:

```text
gcr.io/distroless/python3-debian13:nonroot
```

The final container is also forced to UID/GID `65532:65532`.

The debug distroless variants are not acceptable for the hardened evaluator
because they intentionally add a shell.

Current upstream references:

- https://github.com/GoogleContainerTools/distroless/tree/main/python3
- https://github.com/GoogleContainerTools/distroless
- https://gvisor.dev/docs/user_guide/install/
- https://gvisor.dev/docs/user_guide/quick_start/docker/

## Concrete container policy

Every probe and evaluation container is created with:

- `--runtime=runsc`;
- `--network=none`;
- `--read-only`;
- `--cap-drop=ALL`;
- `--security-opt=no-new-privileges`;
- exact PID and memory ceilings from `SandboxPolicyConfig`;
- CPU quota derived from `cpu_seconds / wall_timeout_seconds`;
- a bounded `/tmp` tmpfs;
- non-root execution;
- no bind mounts;
- no Docker volumes;
- no Docker socket;
- Docker log storage disabled.

The CPU conversion is intentional. Docker expresses CPU restriction as a
fractional core quota, while the experiment policy expresses a maximum CPU
opportunity over a wall-time window. For example, a 2 CPU-second budget over a
5 second wall limit maps to `--cpus=0.4`.

## Runtime-measured attestation

`GVisorDockerBackend.attest` performs adversarial probes against the same
container policy used for evaluation.

It measures:

- network denial;
- host-secret-canary absence;
- shell absence;
- Docker-socket absence;
- no bind/volume mounts;
- non-root/read-only/capability/security configuration;
- CPU quota under concurrent busy-loop pressure;
- memory-ceiling termination;
- PID exhaustion;
- tmpfs disk exhaustion;
- output-byte termination;
- wall-time termination;
- cleanup after forced termination;
- filesystem ephemerality across fresh containers.

Only these concrete measurements produce
`evidence_kind = "runtime-measured"`.

The M18 admission contract remains unchanged and still rejects a missing or
failed required check.

## Runtime build provenance

The backend computes `runtime_build_sha256` from canonical runtime identity
data including:

- immutable Docker image ID;
- image repository digest metadata;
- `runsc --version`;
- Docker server version;
- SHA-256 of the resolved `runsc` binary;
- SHA-256 of the resolved `containerd-shim-runsc-v1` when present;
- SHA-256 of files in the adjacent `gvisor-bin/` directory when present.

The identity is re-measured before attestation/evaluation. Drift causes the
backend to fail closed.

For scientific runs, operators should prefer an explicit image digest rather
than relying only on a mutable tag. The recorded runtime build hash still binds
the exact image and runtime binaries used by a completed evaluation.

## Private-suite staging

M18.2 deliberately does not use a host bind mount, Docker volume, Docker
socket, temporary suite file, or temporary suite image layer.

The worker has already verified the exact private suite bytes against the
manifest-pinned suite hash. The backend then serializes an in-memory envelope:

```text
{
  suite_b64: <exact private suite bytes>,
  request: <validated HiddenEvaluationRequest>
}
```

That envelope is streamed over the attached container stdin. A fixed trusted
Python bootstrap decodes the suite in memory and executes it with one supplied
global:

```python
DSE_REQUEST
```

The suite must write exactly one JSON object to stdout:

```json
{
  "passed_cases": 1,
  "failed_cases": 0,
  "total_cases": 1
}
```

No case names, expected outputs, hidden source, stdout/stderr diagnostics, or
per-case results are accepted by the backend result parser.

The worker then performs its existing aggregate count and duration validation
before producing the public `HiddenEvaluationRunnerResult`.

## Output and wall-time enforcement

Untrusted output is read incrementally from the live Docker attach stream.
The backend does not use an unbounded `subprocess.run(..., capture_output=True)`
path for evaluated code.

When combined stdout/stderr exceeds `output_bytes`, the container is forcibly
removed. The same cleanup path is used when the host wall-time watchdog fires.

The container names are checked after cleanup; a surviving evaluator container
is itself a backend failure.

## Public CI fixture boundary

`tests/test_gvisor_docker_backend_integration.py` uses a deliberately public,
synthetic one-case suite. Its job is to prove that:

- the concrete backend can produce accepted `runtime-measured` evidence;
- the M18 worker can execute through that backend;
- suite/request data reach the runtime without a host mount;
- only aggregate output returns;
- temporary evaluator containers are removed.

The public fixture is not the scientific V0.1 hidden suite. The repository must
not manufacture replacement suite bytes merely to make the canonical suite
hash pass.

A real V0.1 end-to-end acceptance run therefore still requires the
operator-controlled private suite store whose exact bytes match the
manifest-pinned suite hash.
