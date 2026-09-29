# M12 — Agent-Driven Forge Actions

M12 connects typed model-generated action intents to the persistent Forge culture introduced in M11.

The control path is:

```text
LLM cognition v0.5
        ↓
typed forge_* ActionProposal
        ↓
agent.action.proposed
        ↓
Forge Action Broker
        ↓
Forge operation budget
        ↓
M11 Forge world operation
        ↓
forge.action.completed / rejected
        ↓
bounded forge_results in later cognition
```

## Why a separate Forge Action Broker

Forge intents do not pass through the general Tool Broker.

The Tool Broker explicitly ignores action kinds beginning with `forge_`.

This preserves a clear boundary:

- local workspace/tool execution is one capability surface,
- persistent cultural mutation is a different capability surface.

The two surfaces have independent budgets and provenance.

## CognitionDecision/v0.5

When agent actions and Forge culture are both enabled, cognition uses `CognitionDecision/v0.5`.

It retains all previous goal and action decisions and adds three typed Forge action kinds:

- `forge_create_repository`
- `forge_inspect_repository`
- `forge_publish_artifact`

### forge_create_repository

`target` is the repository name.

It carries no content, repository ID, lineage parent, or expected head.

### forge_inspect_repository

`target` is the public `repo_id`.

The result contains a bounded public repository view.

### forge_publish_artifact

Required fields:

- `repo_id`
- relative artifact path in `target`
- `draft_content`

Optional provenance controls:

- `parent_artifact_ids`
- `expected_parent_commit_id`

These map directly to the M11 cultural lineage and stale-head contracts.

## Two independent budgets

M12 keeps the generic action proposal budget:

```text
action_proposals_remaining
```

and adds:

```text
forge_operations_remaining
```

A Forge action therefore requires two separate resources:

1. one model-created action proposal,
2. one Forge-world operation.

The Forge operation budget resets on wake.

If a valid proposed Forge action reaches the broker with no Forge operation budget, it terminates as:

```text
forge.action.rejected
reason = forge_operation_budget_exhausted
```

No cultural object is created.

## Terminal Forge action results

Every processed Forge action produces a persistent `ForgeActionResultRecord`.

An accepted operation emits:

```text
forge.action.completed
```

and marks the action `executed`.

A rejected operation emits:

```text
forge.action.rejected
```

and marks the action `rejected`.

Result records contain:

- action ID,
- operation name,
- completed/rejected status,
- reason,
- structured result data,
- canonical result hash.

## Bounded inspection

Repository inspection is read-only and bounded by the manifest:

- maximum number of returned artifacts,
- maximum content preview characters per artifact.

The inspection result exposes provenance needed for cultural learning:

- artifact ID,
- path,
- bounded content preview,
- content hash,
- creator agent,
- creator generation,
- previous revision,
- lineage parents.

No private agent memory is exposed.

## Cognition context

When Forge culture is enabled, later cognition receives a bounded view:

```text
forge_world:
  repositories:
    - repo_id
    - name
    - head_commit_id
    - artifact_count
    - path_heads

forge_results:
  last four results for this agent
```

This lets an agent condition future decisions on public culture and on objective results of its own Forge actions.

## Deterministic M12 fixture

The deterministic five-agent fixture follows:

```text
tick 150  → self-generate goal
tick 300  → create own public repository
tick 450  → publish initial hypothesis
tick 600  → inspect another repository
tick 750  → publish derived artifact with inspected artifact as parent
tick 900  → goal progress 0.8
tick 1050 → complete goal
```

Each agent has:

- 4 action proposals,
- 4 Forge operations,
- 1 repository,
- 2 published artifacts.

Across five agents:

- 5 repositories,
- 10 artifacts,
- 20 Forge action intents,
- 20 Forge operation consumptions,
- 20 terminal completed Forge actions.

## Cross-agent cultural reuse

The deterministic provider deliberately selects a public repository other than its own whenever another populated repository exists.

After inspection, the agent creates:

```text
notes/derived.md
```

with the inspected public artifact ID in `parent_artifact_ids`.

The derived artifact is written into the agent's own repository but explicitly cites another agent's public artifact.

This creates a directly measurable cross-agent cultural edge:

```text
Agent A artifact
      ↓ lineage parent
Agent B derived artifact
```

M12 acceptance tests require that this parent was created by a different agent and resides in a different repository.

## Source-action provenance

Published artifacts receive:

```text
source_action_id
```

from the originating `forge_publish_artifact` intent.

This gives the causal chain:

```text
goal
  ↓
cognition event
  ↓
typed Forge action intent
  ↓
Forge operation
  ↓
commit/artifact
  ↓
explicit cultural lineage
```

## Security boundary

M12 still uses `DeterministicMemoryForgeProvider`.

It performs no:

- Git subprocess,
- Forgejo request,
- GitHub request,
- public repository write,
- external network mutation.

The canonical world is the event-sourced M11 Forge state.

## Scientific boundary

M12 is the first milestone where agent-generated decisions can create and reuse persistent public cultural artifacts.

It is still a deterministic fixture, not evidence that a real LLM population spontaneously develops cumulative culture.

That claim requires the later experimental harness:

- free/self-directed model runs,
- turnover,
- controls and compute matching,
- hidden functional evaluation,
- multiple seeds,
- objective reuse and capability metrics.
