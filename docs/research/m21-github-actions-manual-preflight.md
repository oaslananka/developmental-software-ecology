# M21 GitHub Actions — manual ephemeral gVisor/TLS preflight

## Usage and recurring cost

The workflow at .github/workflows/m21-manual-runtime.yml is **manual-only**
(workflow_dispatch). There is no cron schedule, push/PR trigger, persistent
worker, long-running evaluator service or new VPS. It creates a fresh
GitHub-hosted Ubuntu 24.04 runner only when an authorized owner starts it, and
the runner stops when the job finishes. The job has a 35-minute timeout,
read-only repository permissions, no OIDC authority and no secrets. Multiple
requests are serialized instead of cancelling a running proof.

After the workflow is merged into the protected default branch:

1. Open repository **Actions** → **M21 manual gVisor TLS public preflight**.
2. Choose **Run workflow** on **main**, signed in as GitHub numeric user ID
   285490571. Other actors or non-main refs fail closed.
3. Inspect public result and runtime build hashes. No artifact or submission
   source is uploaded.

A successful run shows that an ephemeral GitHub runner installed real gVisor
runsc and exercised this *actual* local chain:

ExternalEvaluatorClient → HTTPS/TLS EvaluatorTLSServer →
EvaluatorServiceApplication → HardenedEvaluatorWorker →
FilesystemHiddenSuiteStore → GVisorDockerBackend.

The test uses a PUBLIC synthetic, one-case fixture. It checks missing-bearer
rejection (401), real TLS trust, measured isolation checks, worker/runtime
provenance, sanitized aggregate results and unchanged in-memory world state.
The empty-world public fixture intentionally scores **0/1**; this is not a
scientific outcome.

## Non-negotiable boundary: this is not private M21 acceptance

The real frozen M21 suite contains 64 private cases under suite ID
v0_1-hidden-functional-suite-m20-v1 and SHA-256
a869623be48e27c4fa39563596c703f1308f0b1a9a9a41c12fdf4a36c28146b1.
This workflow **never reads, retrieves, injects, caches, runs, uploads or
references the private bytes**. Its temporary fixture has a different
identity and digest. It does not read private manifests, research submissions,
expected outputs, production TLS certificates or bearer tokens.

Do not add secret references, pull_request_target, public artifact uploads or
private-suite URL/token inputs to this public preflight simply to mark M21
accepted. Public Actions logs and repository artifacts must never contain
private cases or case-level results. The existing regular GitHub CI
gvisor-runtime-probe still runs on normal PR/push and remains required by
the repository's branch rules; this manual workflow is a separate
experiment-infrastructure capability check.

## Following phase — genuine private acceptance

Before claiming M21 science readiness, build a **separate owner-controlled
private workflow/repository** with a protected environment and required
reviewer approval. Use narrowly scoped time-limited OIDC credentials bound to
that exact private workflow/repository/ref/environment to retrieve already
frozen suite bytes from a selected private object store. The actual provider,
private object identity, OIDC trust policy and suite handoff are not yet
verified; do not invent any provider location, secret or key.

That future approved run must validate the local private bundle SHA-256,
metadata and exact M20 opportunity binding, and then exercise real hidden
bytes through the authenticated TLS/worker/gVisor chain. It must verify all
runtime-measured bounds, immutable worker/runtime identity, aggregate-only
output, unchanged world state and research-readiness clearance from the exact
frozen suite. Passing this public preflight does **not** clear that gate.

Track provider selection, protected private bundle handoff and scientific
acceptance separately in
[issue #40](https://github.com/oaslananka/developmental-software-ecology/issues/40).
Do not start P/T/E/ES/RIL research pilots before exact real private acceptance.
