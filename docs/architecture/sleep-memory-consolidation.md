# M3.1 — Sleep-time Memory Consolidation and Forgetting

M3.1 adds a deterministic sleep-time memory mechanic on top of bounded episodic memory.

The mechanic is intentionally separate from M3 so it can be enabled or disabled independently in experiments.

## Trigger

The mechanic runs exactly once when an agent transitions into `sleeping`.

It does not run on every sleep tick.

## Consolidation

At sleep entry, the runtime selects up to `strengthen_top_k` episodic memories using a deterministic ordering:

1. highest salience,
2. most recent creation tick,
3. lexicographically smallest memory ID.

Selected memories receive an explicit `memory.episode.consolidated` event.

The event records:

- memory ID
- old salience
- new salience
- reason

Salience is capped at 1.0.

## Forgetting

After consolidation, the runtime selects forgetting candidates among memories that were not consolidated in the same sleep entry.

A memory is eligible when:

- its salience is below `forget_below_salience`, and
- its age is at least `forget_older_than_ticks`.

Eligible memories are ordered deterministically:

1. lowest salience,
2. oldest creation tick,
3. lexicographically smallest memory ID.

At most `max_forget_per_sleep` memories are removed.

Each removal is represented by `memory.episode.forgotten`.

## Separation from capacity eviction

`memory.episode.evicted` remains a capacity-control mechanism.

`memory.episode.forgotten` is a sleep-time life mechanic.

Keeping them distinct matters experimentally: capacity pressure and sleep-dependent forgetting can be ablated independently.

## V0.1 fixture

The M3.1 test experiment uses:

- memory capacity: 3
- 4 cognition calls per active cycle
- strengthen top 2 memories
- +0.1 salience boost
- forget memories below 0.8 salience
- only if at least 500 ticks old
- forget at most 1 memory per sleep entry

For five identical agents, the expected first sleep transition produces:

- 20 recorded memories
- 5 capacity evictions
- 10 consolidation events
- 5 forgetting events
- 2 retained memories per agent

## Replay and persistence

Consolidation and forgetting are reducer-applied events.

Therefore:

- replay reconstructs the same memory state,
- snapshots include the changed salience values,
- PostgreSQL restore produces the same world hash.

## Explicitly not in M3.1

- semantic memory
- memory summarization
- embeddings
- vector search
- stochastic forgetting
- affect-dependent recall
- dream generation
- cross-agent memory transfer
- model calls during sleep

These remain separate future mechanics.
