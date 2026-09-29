# M13 — Controlled Turnover and Private-State Deletion

M13 introduces the causal boundary required by the executable-culture research question:

> individual private state can disappear while public culture survives.

This is not biological reproduction.

It is a controlled experimental replacement operator.

## Identity model

The logical population slot remains stable:

```text
agent-0001
```

while the individual generation changes:

```text
agent-0001 generation 0
        ↓ turnover
agent-0001 generation 1
```

Keeping the slot ID stable makes longitudinal measurement simple while `generation` identifies the individual incarnation.

## Manifest schedule

Turnover is explicit and deterministic:

```yaml
agents:
  turnover:
    enabled: true
    ticks: [150]
    agent_ids:
      - agent-0001
      - agent-0002
```

The schedule requires:

- positive sorted unique ticks,
- unique `agent-XXXX` IDs,
- non-empty ticks and agents when enabled.

No random mortality is introduced in M13.

## Event sequence

At a scheduled tick, each selected agent emits:

```text
agent.lifecycle.turned_over
agent.lifecycle.replaced
agent.lifecycle.wake
```

Turnover occurs after the world tick advances and before the new individual can perform cognition or actions.

## Turned-over audit event

`agent.lifecycle.turned_over` records:

- previous generation,
- next generation,
- scheduled-turnover reason,
- counts of private state being discarded,
- canonical public Forge-state hash.

It does **not** embed private memory contents, goal text, or action contents.

The reducer validates the counts against live state before accepting the event.

## Replacement event

`agent.lifecycle.replaced` requires the agent to be in `turned_over` state.

It then:

- increments generation exactly once,
- sets the new birth tick,
- preserves traits,
- resets resources,
- resets cognition,
- deletes goals,
- deletes action intents/executions/Forge results,
- deletes episodic memory,
- resets state version,
- returns lifecycle state to `born`.

The normal wake event then grants the new individual's per-cycle budgets.

## Public culture is not inherited through private state

Forge state is global `WorldState.forge`, not part of `AgentState`.

Therefore replacement does not mutate:

- repositories,
- commits,
- artifact contents,
- artifact lineage,
- creator provenance.

The turnover and replacement events both carry the same public Forge hash, and the reducer verifies it against current world state.

## Deterministic M13 fixture

The fast five-agent fixture uses:

```text
20   generation-0 goal
40   create repository
60   publish initial artifact
80   inspect public culture
100  publish derived artifact
120  progress goal
140  complete goal

150  turnover all five agents

160  generation-1 goal
180  create generation-1 repository
200  publish initial artifact
220  inspect generation-0 public repository
240  publish derived artifact citing generation-0 parent
260  progress goal
280  complete goal
```

Episodic memory is enabled specifically so deletion is observable.

Immediately after tick 150, each replacement has:

- generation = 1,
- cognition calls = 0,
- zero goals,
- zero actions,
- zero Forge action results,
- zero episodic memories,
- fresh budgets.

At the same moment the generation-0 Forge artifacts remain unchanged.

## Cultural inheritance test

For generation 1, the deterministic provider prefers an older repository created by the same population slot.

The replacement therefore learns only through the public Forge view:

```text
generation-0 public artifact
        ↓ inspect
generation-1 cognition
        ↓ explicit parent_artifact_id
generation-1 derived artifact
```

The derived artifact records:

- creator generation 1,
- parent artifact creator generation 0,
- parent repository from the pre-turnover culture.

This is cultural inheritance through externalized artifacts, not episodic-memory inheritance.

## Historical provenance

Old public artifacts retain `source_action_id` values pointing to the action that created them.

Those old private action records disappear from the replacement AgentState.

This is intentional:

- scientific event history preserves causal provenance,
- the replacement individual does not inherit private action state.

## Replay

Replay applies the original generation-0 events, then the turnover/replacement reset, then generation-1 events.

It does not reconstruct deleted private state into the current replacement.

The final world hash must match the original run exactly.

## PostgreSQL persistence

The same turnover stream is persisted through the append-only event store.

Restore must reproduce:

- current generation numbers,
- empty/deleted old private state,
- current replacement private state,
- complete generation-0 and generation-1 Forge culture.

## Scope boundary

M13 does not add:

- biological death causes,
- random accidents,
- reproduction,
- parents/children,
- mutation,
- inherited episodic memory,
- personality mutation,
- marriage or attachment mechanics.

Those mechanisms would confound the first cultural-inheritance experiment.

## Next

The next milestone should build the experiment-condition harness required by the research brief:

- P — Personal only,
- T — persistent text culture,
- E — executable/public artifact culture,
- ES — executable + social,
- compute-matched RIL control.

Turnover should remain the same causal operator across conditions.
