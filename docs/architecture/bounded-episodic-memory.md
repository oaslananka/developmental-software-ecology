# M3 — Bounded Episodic Memory

M3 adds causal, replayable episodic memory without embeddings, vector databases, or model-based summarization.

## Design goal

Memory must change future cognition, remain bounded, and be reconstructable from the event stream.

A memory record contains:

- deterministic memory ID
- creation tick
- source event ID
- short content
- salience
- decision metadata
- optional focus

## Recording

After each `agent.cognition.decided` event, an enabled agent records one
`memory.episode.recorded` event.

The source event ID connects the memory to the cognition event that created it.

## Retrieval

Retrieval is deterministic and read-only.

Each episode receives a score based on:

1. lexical token overlap with the current query,
2. stored salience,
3. recency.

No embeddings or external models are used in M3.

The configured top-K memories are inserted into the next `ModelRequest.context`
under `episodic_memories`.

This makes memory causal: persisted past experience can alter a later model request.

## Bounded capacity

Each agent has a fixed memory capacity.

When a new record exceeds capacity, the runtime emits an explicit
`memory.episode.evicted` event.

Eviction is deterministic:

1. lowest salience,
2. oldest creation tick,
3. lexicographically smallest memory ID.

The reducer never silently drops state.

## Replay

Both record and eviction operations are reducer-applied events.

A replay from the same initial manifest and event stream must reconstruct the
same bounded memory state and world state hash.

Snapshots automatically include memory because it is part of `AgentState`.

## PostgreSQL

M3 does not add a separate memory table.

The canonical source remains the append-only world event stream. PostgreSQL
persistence and snapshot restore already cover the new memory events and state.

A specialized memory index can be introduced later as a derived acceleration
structure, but it must never become canonical history.

## Explicitly not in M3

- embeddings
- vector similarity
- semantic memory
- sleep consolidation
- model-generated summaries
- forgetting curves
- affect-dependent recall
- social memory
- cross-agent memory transfer

Those mechanisms should be introduced in later milestones as independent
experimental mechanics.
