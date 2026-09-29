# M0.1 Persistence Architecture

M0.1 makes the deterministic simulation substrate durable without changing agent behavior.

## Canonical state

The event stream is the canonical history. Snapshots are acceleration artifacts only.

```text
immutable experiment manifest
        +
append-only world events
        +
optional snapshot
        =
reconstructable world state
```

## PostgreSQL tables

### experiments

Stores the immutable experiment manifest and its canonical SHA-256 hash.

Re-registering an experiment ID with the same manifest is idempotent. Reusing the same ID with a different manifest is rejected.

### world_events

Stores one ordered event stream per experiment.

The primary key is:

```text
(experiment_id, sequence_number)
```

Append operations lock the corresponding `experiments` row, read the current maximum sequence, validate that the incoming batch is exactly contiguous, and append it in one transaction.

This prevents gaps and conflicting concurrent appenders from silently creating divergent histories.

### world_snapshots

Stores serialized derived world state plus its state hash.

Snapshots are not authoritative. During restore, their hash is verified and all events after the snapshot sequence are replayed.

## Restore algorithm

```text
load immutable manifest
        ↓
latest snapshot exists?
   ├─ no → create initial world
   └─ yes → restore + verify snapshot hash
        ↓
load events after snapshot sequence
        ↓
apply reducer in sequence order
        ↓
current world state
```

## M0.1 acceptance gate

- real PostgreSQL integration tests run in CI
- 10,000 events persist durably
- a snapshot is taken at event 5,000
- process-independent restore reads snapshot + events 5,001–10,000
- restored state hash equals the expected in-memory state hash
- event sequence gaps are rejected
- experiment manifests are immutable by experiment ID

No LLM, memory retrieval, Forgejo, sandbox, physiology, social graph, or turnover behavior is introduced in M0.1.
