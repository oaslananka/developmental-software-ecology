# M11 — Persistent Forge World Contract

M11 introduces the first persistent cultural substrate for the experiment.

The key architectural separation is:

```text
private AgentState
        !=
public ForgeWorldState
```

Agent memory, goals, and cognition remain private individual state.

Repositories, commits, artifact contents, and artifact lineage live in global world state and therefore participate in:

- event replay,
- snapshots,
- world-state hashes,
- PostgreSQL restore,
- future agent turnover.

## Why deterministic memory first

M11 does not connect to GitHub or Forgejo.

It defines the provider boundary and uses `DeterministicMemoryForgeProvider` only to produce deterministic repository, commit, and artifact identifiers.

This provider:

- performs no network requests,
- invokes no Git command,
- writes no external repository,
- has no hidden persistent state.

The event-sourced world is canonical.

A later Forgejo adapter must preserve these same contracts.

## IDs are not Git SHAs

M11 uses:

- `repo_id`
- `commit_id`
- `artifact_id`

The deterministic `commit_id` is a system-level SHA-256-derived identifier.

It is **not** a Git object SHA and must not be presented as one.

A future real Git provider may add provider-native Git commit SHA provenance separately.

## Forge world state

`WorldState.forge` contains three maps:

```text
repositories
commits
artifacts
```

A repository tracks:

- repository ID and name,
- creator agent/generation,
- default branch,
- current head commit,
- commit count,
- artifact count,
- latest artifact ID for each path.

An artifact stores bounded public content directly in the event-sourced cultural state.

That makes M11 culture self-contained for replay and controlled experiments.

## Repository creation

A successful operation emits:

```text
forge.repository.created
```

Repository names are unique within the world.

Repository count is bounded by the experiment manifest.

## Artifact commits

A successful commit emits:

```text
forge.artifact.committed
```

Each M11 commit contains exactly one artifact revision.

The event records:

- provider,
- commit record,
- artifact record.

The reducer independently checks the event before mutating world state.

## Content integrity

For every artifact the reducer verifies:

- UTF-8 byte count,
- SHA-256 content hash,
- repository linkage,
- commit linkage,
- event provenance,
- actor identity,
- actor generation.

The manifest imposes a per-artifact byte limit.

## Linear repository history

M11 intentionally starts with a linear head model.

Each commit records `parent_commit_id`.

The reducer requires:

```text
commit.parent_commit_id == repository.head_commit_id
```

Callers may provide `expected_parent_commit_id`.

If the repository head changed, the operation emits an explicit rejection:

```text
forge.operation.rejected
reason = stale_repository_head
```

Branches, merges, PRs, and conflict resolution are later milestones.

## Revision lineage

Each path has a current artifact head.

When the same path is committed again:

```text
previous_artifact_id = old path head
```

The previous artifact is automatically included in `parent_artifact_ids`.

This provides explicit revision lineage independent of Git internals.

## Cultural reuse lineage

A new artifact may additionally cite any existing public artifact as a parent.

Parents may come from:

- another agent,
- another file,
- another repository.

That allows future metrics such as:

- cross-agent reuse,
- cross-generation reuse,
- recombination,
- lineage depth,
- artifact survival.

## Rejections are events

Semantic failures produce:

```text
forge.operation.rejected
```

Examples include:

- Forge disabled,
- provider mismatch,
- duplicate repository name,
- repository or artifact limits,
- stale repository head,
- unsafe artifact path,
- oversized content,
- unknown lineage parent.

Rejected operations do not mutate cultural objects, but the rejection remains auditable in the event stream.

## Inspection

M11 provides canonical reads over world state:

- list current repository files,
- read current file at a path,
- read a historical artifact by ID.

Historical artifact content remains accessible even after a later revision becomes the path head.

## Persistence and turnover boundary

Because Forge state is global and separate from AgentState, deleting or replacing private agent state does not inherently delete cultural artifacts.

Actual scheduled turnover is a later lifecycle milestone, but M11 establishes the state boundary required for that experiment.

## Security boundary

M11 has no autonomous public repository access.

It performs no:

- GitHub write,
- Forgejo write,
- external network request,
- Git subprocess,
- shell process.

## Scientific boundary

M11 gives the platform a measurable persistent cultural substrate.

It does not yet show agents autonomously choosing to publish or reuse artifacts.

The next milestone should connect typed agent action intents to these Forge operations while preserving provenance and compute/resource accounting.
