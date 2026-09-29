# M1 — Cognitionless Agent Runtime

M1 introduces lifecycle and bounded activity without connecting an LLM.

The objective is to encode the first hard constraint behind the project:

> Agents are not continuously thinking processes.

## Lifecycle

Each agent starts in `born` and follows a deterministic cycle:

```text
born
  ↓
wake
  ↓
idle + bounded activity consumption
  ↓
sleep
  ↓
sleep progress
  ↓
wake
  ↓
...
```

The V0.1 baseline uses:

- 960 active ticks per cycle
- 480 sleep ticks per cycle
- one simulated minute per world tick

This gives a 1,440-tick cycle, analogous to a 24-hour schedule without claiming biological equivalence.

## Resource state

M1 intentionally avoids hormone/emotion variables.

Each agent has only:

```text
activity_units_remaining
sleep_ticks_remaining
cycles_completed
```

These variables are causal runtime state, not prompt decoration.

When activity reaches zero, the runtime emits a sleep transition. During sleep, no active/idle event is produced. After the configured sleep duration, the activity budget is reset by a wake event.

## Event model

New events:

- `agent.lifecycle.wake`
- `agent.activity.idle`
- `agent.lifecycle.sleep`
- `resource.activity.consumed`
- `resource.sleep.elapsed`

Every lifecycle and resource mutation passes through the reducer, so the full lifecycle can be replayed from the event stream.

## Determinism gate

For five agents and one 1,440-tick lifecycle cycle:

- each agent wakes twice
- each agent enters sleep once
- each agent consumes exactly 960 activity units
- each agent records exactly 480 sleep ticks
- each agent completes exactly one lifecycle cycle
- replaying the emitted event stream reconstructs the identical state hash
- the same event stream persists and restores through PostgreSQL

## Explicitly not in M1

- LLM cognition
- goal generation
- memory retrieval
- curiosity/stress/affect
- variable sleep needs
- disease
- aging
- mortality
- social relationships
- Forgejo
- sandbox execution

Those remain separate mechanics so they can later be ablated independently.
