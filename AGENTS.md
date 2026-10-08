# Developmental Software Ecology Agent Instructions

These instructions apply repository-wide.

## Repository purpose

Developmental Software Ecology is a research platform for studying executable cultural evolution in persistent agent ecologies.

This repository is not a generic agent product, hosted control plane, life simulator, or production sandbox. Its primary contract is scientific: experiments must remain reproducible, treatment differences explicit, execution bounded, and evidence classified honestly.

A nested `AGENTS.md` is not currently needed. The important invariants cross `contracts`, `engine`, `evaluator`, `experiments`, `forge`, `persistence`, `providers`, `sandbox`, and `tools`. Add a nested boundary only if a future subtree acquires materially different authority or operational risk.

## Read first

Before changing behavior, read:

- `README.md`;
- the relevant document under `docs/architecture/**`;
- `docs/research/v0.1-scope.md`;
- the affected experiment manifest under `experiments/**`;
- the focused tests for the subsystem.

Architecture documents are experiment/runtime contracts, not disposable implementation notes. If behavior intentionally changes a documented contract, update the document and regression evidence together.

## Research truth

Do not turn architecture prototypes, mocks, fixtures, CI protocol tests, or synthetic runs into stronger research claims than they support.

- Deterministic fixture evidence is not physical/runtime isolation evidence.
- `test-fixture` attestation is not `runtime-measured` evidence.
- A protocol-compatible mock evaluator is not proof of hardened sandbox execution.
- A well-specified experiment condition may still be not research-runtime-ready.
- Do not describe unimplemented T/E/ES treatment mechanics as completed experimental capability.
- Do not invent benchmark, model, token, sandbox, evaluator, provenance, or replication results.

Use the explicit readiness/evidence classifications already defined by the architecture.

## Determinism and event history

The event stream is canonical history. Snapshots are acceleration artifacts.

Preserve these invariants:

- experiment manifests are immutable by experiment ID;
- event sequence numbers are contiguous and monotonic;
- state changes flow through deterministic reducers;
- equivalent manifests/events reconstruct the same canonical state hash;
- snapshots are hash-verified before use;
- restore replays post-snapshot events in sequence;
- a snapshot never becomes more authoritative than the event stream.

Do not add wall-clock, random, provider, database, filesystem, or process behavior to deterministic reducers unless that nondeterminism is explicitly modeled as input evidence.

## Persistence

PostgreSQL persistence is part of the experiment provenance contract.

- Re-registering the same experiment ID with different manifest content fails.
- Event appends remain transactional and reject gaps/conflicting appenders.
- Schema changes that affect an existing deployed database require an explicit migration; a fresh-schema CI fixture is not evidence that migration is unnecessary.
- Do not rewrite or delete historical events merely to make restored state match current code.
- Persistence tests must use disposable test databases, never production/shared research state.

## Experimental conditions and compute matching

When `study.enforce_condition_contract: true`:

- treatment declarations must match the condition exactly;
- treatment-specific capability must not leak into control conditions;
- the compute-match fingerprint must exclude the independent treatment while retaining shared opportunity/budget variables;
- observed model-call/token/action usage remains separate from configured budget equality;
- do not modify expected compute/evidence numbers just to hide treatment drift.

P, T, E, ES, and RIL are experimental conditions, not product tiers.

## Sandbox and generated-code execution

Sandbox admission is fail-closed.

- A `SandboxExecutionPlan` is data, not permission to execute.
- Absolute paths, traversal and network-style artifact targets remain rejected.
- Policy hash must bind the complete resource/capability policy.
- Missing attestation, backend mismatch, policy-hash mismatch, missing isolation checks, or failed checks deny admission.
- Do not add heuristic fallback admission.
- Network, host mounts, secrets, and shell execution remain denied where the manifest requires them.
- Resource ceilings stay bounded: CPU, memory, PIDs, disk, output, and wall time.

A real execution backend must not silently bypass the admission evaluator.

## Hardened evaluator boundary

The worker independently validates control-plane input.

Preserve:

- exact suite identity/hash;
- full sandbox policy and canonical policy hash;
- evaluation-plan hash;
- experiment/world identity;
- artifact digests and byte counts;
- culture-snapshot hash;
- admitted runtime identity and build provenance.

Hidden-suite bytes, case source, case names, expected outputs, raw stdout/stderr, and per-case diagnostics do not become public result payloads. Public results remain aggregate/provenance-oriented.

Do not relabel fixture evidence as runtime-measured to clear research-readiness gates.

## Providers and model evidence

External providers are nondeterministic boundaries.

- Keep provider/model/version provenance explicit.
- Do not rely on model memory for current provider API behavior when an exact external contract is required.
- Model retries, token accounting, timeouts, and response parsing must remain bounded and testable.
- Fake providers/fixtures prove deterministic orchestration, not live model quality.

## Forge, tools, and social/cultural mechanisms

Persistent text, executable artifacts, Forge actions, social channels, goals, memory, and turnover are experimental mechanics. Keep each mechanism separable enough to ablate.

Do not introduce hidden cross-condition state, inherited private episodic memory, unrestricted public writes, or broad internet/tool authority unless the research design is explicitly changed and documented.

## Security and secrets

- Never commit provider credentials, database credentials, private hidden-suite material, signing keys, tokens, real private source, or production research data.
- Fixtures must be synthetic or explicitly safe.
- Keep secret canaries and private-suite paths out of public experiment artifacts.
- Do not log private hidden evaluator contents or unbounded external-process output.
- Preserve path confinement and exact digest checks when loading private or staged artifacts.

## Toolchain and validation

Python support is `>=3.12`.

CI installs the hash-locked `requirements-ci.txt`, runs Ruff and pytest against PostgreSQL, and separately exercises the measured gVisor integration path.

Use the narrowest relevant tests first, then the repository gate:

```bash
python -m pip install --only-binary=:all: --require-hashes --requirement requirements-ci.txt
ruff check .
pytest -q
```

For persistence changes, run the PostgreSQL-backed tests.

For sandbox/gVisor changes, run the relevant integration tests only in an environment that actually provides the required runtime. Do not claim that a skipped/unavailable runtime test passed.

## CI and dependency discipline

- Keep dependency changes narrow and reproducible.
- Do not remove hashes or relax CI installation just to make dependency resolution easier.
- Do not weaken Ruff/tests, PostgreSQL evidence, sandbox probes, runtime attestation, or readiness gates for convenience.
- GitHub workflow permissions should remain least-privilege; third-party Actions should move toward immutable reviewed revisions when workflow hardening is in scope.

## Change discipline

- Keep one research/mechanism objective per change.
- Add regression tests for semantic, persistence, sandbox, evaluator, provider, condition, or provenance fixes.
- Prefer explicit fail-closed states to best-effort guessing.
- Preserve old development fixtures unless an intentional contract migration explains the update.
- Do not expand V0.1 with unrelated simulation features unless required by the stated hypotheses or safe/reproducible runtime.

## Definition of done

A change is ready when:

1. the intended experimental/runtime contract is explicit;
2. focused regression tests cover the changed invariant;
3. relevant architecture/research docs agree with implementation;
4. determinism, persistence, treatment separation, evidence classification, and security boundaries remain intact;
5. exact-head CI is green, or any environment-dependent evidence that could not run is reported precisely;
6. no stronger scientific or isolation claim is made than the executed evidence supports.
