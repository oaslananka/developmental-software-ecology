# M8 — Ephemeral Local Workspace

M8 replaces one simulated capability with one real but tightly scoped capability behind the existing Tool Broker.

The new executor can:

- inspect a dedicated temporary workspace,
- write bounded UTF-8 artifact files inside that workspace,
- perform static file validation.

It cannot:

- spawn a shell,
- launch subprocesses,
- execute generated code,
- access the network,
- call Forgejo or GitHub,
- escape its configured workspace root.

## Executor provenance

The experiment manifest pins the executor:

```yaml
agents:
  tool_broker:
    enabled: true
    executions_per_cycle: 2
    executor: "ephemeral-local-workspace"
```

The Tool Broker fails fast if the runtime executor name does not match the manifest.

This prevents a run from silently switching between simulated and real execution surfaces.

## Workspace root

The executor receives a dedicated workspace directory from the host runner.

The root is resolved once during executor construction.

All non-inspection targets are resolved relative to this root before budget consumption.

## Path containment

The existing broker rejects:

- absolute paths,
- network-style targets,
- `..` parent traversal.

The local executor adds a filesystem-aware containment check:

```text
(workspace_root / target).resolve()
        ↓
must remain under resolved workspace_root
```

Because existing symlink components are resolved, a path such as:

```text
workspace/link -> /outside
target = link/escape.md
```

is rejected as `workspace_escape` before execution budget is consumed.

The executor does not follow directory symlinks during workspace inspection.

## Resource bounds

The executor has two independent bounds:

- `max_file_bytes`: maximum UTF-8 bytes written or fully read for validation.
- `max_entries`: maximum workspace entries returned by inspection and maximum entry-count threshold for adding a new artifact.

This prevents a long-running agent from using the M8 workspace as unbounded storage.

## inspect_workspace

`inspect_workspace` performs a real recursive listing of the dedicated root.

The result is sorted, bounded, and structured.

Each entry contains:

- relative path,
- type,
- byte size for regular files.

Directory symlinks are reported as symlinks but never recursively traversed.

## draft_artifact

`draft_artifact` writes the proposed UTF-8 content to the resolved in-root target.

The result records:

- relative target,
- whether the file was newly created,
- byte count,
- SHA-256 content hash.

The result type is `artifact_written`.

No file outside the workspace root can be written through the executor.

## run_validation

`run_validation` is deliberately non-executing.

It checks only:

- target existence,
- regular-file status,
- size bound,
- UTF-8 decodability,
- non-empty content,
- SHA-256 content hash.

A Python, shell, or JavaScript file is treated as inert text.

No process is spawned and no code is imported or executed.

## Event and provenance semantics

Real workspace operations still flow through the M7 broker protocol:

```text
action intent
    ↓
policy checks
    ↓
resource.tool_execution.consumed
    ↓
tool.execution.started
    ↓
real scoped filesystem operation
    ↓
tool.execution.completed
```

The completion event records:

- executor name,
- result type,
- structured result data,
- deterministic result hash.

Later cognition receives recent execution records through `tool_results`.

## Replay boundary

Event replay reconstructs agent/control-plane state and execution provenance exactly.

M8 does **not** rematerialize ephemeral filesystem bytes during reducer replay.

The bytes remain recoverable for M8-written artifacts from the earlier `agent.action.proposed` event because `draft_content` is event-sourced, but automatic filesystem rematerialization is intentionally deferred.

This distinction is important:

```text
event/state replay
    !=
external workspace rematerialization
```

Persistent cultural artifacts belong to the later Git/Forgejo world, not to this ephemeral executor.

## Security boundary

M8 is an application-level scoped filesystem boundary, not a hardened OS sandbox.

It should not be used to execute untrusted generated code.

A later real execution milestone must introduce process isolation such as rootless containers plus gVisor or another hardened execution boundary before generated code is run.

## Scientific boundary

M8 establishes that an agent can cause a real, bounded external state change and receive objective evidence of that change.

It still does not establish executable software competence because generated artifacts are not executed.

The next scientific/engineering step should therefore be isolated validation/execution, not a broader internet or public-repository surface.
