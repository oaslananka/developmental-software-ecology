# M5 — Goal Progress and Closure Lifecycle

M5 adds an explicit lifecycle to self-generated goals without introducing external tools.

The purpose is to distinguish:

- having a durable goal,
- making progress,
- completing it,
- abandoning it,
- and later starting a new goal.

## Backward compatibility

M4 behavior remains unchanged.

```yaml
agents:
  goals:
    enabled: true
    lifecycle_enabled: false
```

uses `CognitionDecision/v0.2`.

M5 enables:

```yaml
agents:
  goals:
    enabled: true
    lifecycle_enabled: true
```

which uses `CognitionDecision/v0.3`.

## v0.3 decisions

The model may emit:

- `idle`
- `observe`
- `propose_goal`
- `update_goal`
- `complete_goal`
- `abandon_goal`

Payload rules are strict:

- `propose_goal` requires `GoalProposal`
- `update_goal` requires `GoalProgressUpdate`
- `complete_goal` requires `GoalClosure`
- `abandon_goal` requires `GoalClosure`
- `idle` / `observe` allow none of those payloads

## Canonical events

Goal lifecycle state changes occur only through events:

```text
agent.goal.created
agent.goal.progressed
agent.goal.completed
agent.goal.abandoned
```

The model output never mutates `AgentState` directly.

## Progress invariants

`agent.goal.progressed` must:

1. target the currently active goal,
2. match the persisted old progress,
3. strictly increase progress,
4. remain below 1.0.

Completion is represented separately and sets progress to exactly 1.0.

## Goal history

Closing a goal does not delete it.

Completed and abandoned goals remain in:

```text
AgentState.goals.goals
```

Only `active_goal_id` is cleared.

This lets later analysis measure:

- goal lifetime,
- completion rate,
- abandonment rate,
- progress velocity,
- number of goals per agent,
- persistence across sleep and turnover.

## New goals after closure

Once the active goal is completed or abandoned, a later cognition cycle may generate another self-directed goal.

This creates a repeatable intentional loop:

```text
no active goal
    ↓
propose
    ↓
active goal
    ↓
progress
    ↓
complete / abandon
    ↓
history retained
    ↓
no active goal
    ↓
new self-generated goal
```

## Deterministic M5 fixture

The deterministic fake provider follows:

```text
tick 200 → propose goal
tick 400 → progress 0.4
tick 600 → progress 0.8
tick 800 → complete
```

For five agents this yields:

- 5 goal creation events
- 10 progress events
- 5 completion events
- 0 active goals after tick 800

At tick 1600, after the sleep/wake cycle, each agent may create a new goal while the completed goal remains in history.

## Scientific boundary

A completed internal goal is **not** yet evidence that an external-world task succeeded.

M5 measures intentional-state dynamics only.

External task success must later be grounded by executable artifacts, hidden tests, Forgejo state, sandbox results, or other evaluator evidence.

## Explicitly not in M5

- subgoals
- planning trees
- tool calls
- Forgejo
- sandbox execution
- web actions
- hidden functional evaluation
- social goal negotiation
- multi-goal scheduling
- external task assignment

Those remain later milestones.
