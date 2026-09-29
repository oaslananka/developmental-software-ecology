# M4 — Self-generated Goals and Goal Persistence

M4 introduces the first persistent intentional state owned by the agent itself.

The milestone intentionally does **not** add Forgejo, sandbox execution, tool plans, or goal completion. Its purpose is to isolate one question:

> Can an agent generate a durable objective from its own model output and carry that objective across later cognition cycles and sleep/wake transitions?

## Goal generation

Goal generation is enabled per experiment through:

```yaml
agents:
  goals:
    enabled: true
    max_active_goals: 1
```

When goals are disabled, the runtime continues to use `CognitionDecision/v0.1`.

When goals are enabled, the runtime requests `CognitionDecision/v0.2`.

## CognitionDecision/v0.2

The response still supports:

- `idle`
- `observe`

and adds:

- `propose_goal`

A `propose_goal` decision must include a strict `GoalProposal`:

- title
- description
- motivation_summary
- expected_value
- estimated_cost
- confidence

All unknown fields are rejected.

A goal object is forbidden for `idle` and `observe`.

## Self-generated only

Accepted goals are stored with:

```text
source = self_generated
```

M4 exposes no user-assigned-goal field and no externally injected task contract.

The experiment harness may enable or disable goal generation, but it does not supply the goal content.

## Goal creation

When an agent has no active goal and emits a valid `propose_goal`, the runtime creates one:

```text
agent.cognition.decided
        ↓
agent.goal.created
        ↓
AgentState.goals.active_goal_id
```

The goal ID is deterministic and derived from the cognition event ID.

The goal stores its source cognition event for provenance.

## Persistence

The active goal becomes part of later model context:

```text
active_goal:
  goal_id
  created_tick
  title
  description
  motivation_summary
  expected_value
  estimated_cost
  confidence
  status
```

Because goal state is inside `AgentState`, it automatically participates in:

- state hashing
- snapshots
- event replay
- PostgreSQL restore
- sleep/wake persistence

## Active-goal invariant

M4 supports exactly one active goal per agent.

If a provider proposes another goal while an active goal already exists, the runtime does not silently ignore it. It emits:

```text
agent.goal.proposal_rejected
```

with reason `active_goal_exists`.

This event changes no goal state but preserves observability.

## Provider compatibility

The deterministic fake provider proposes a goal only when all of the following hold:

1. response schema is `CognitionDecision/v0.2`,
2. goal generation is enabled,
3. no active goal exists.

Otherwise it keeps the previous deterministic idle/observe behavior.

The OpenCode adapter uses the same v0.2 contract and validates the returned JSON before it can affect world state.

## Explicitly not in M4

- goal completion
- goal abandonment
- subgoals
- plans
- action selection
- Forgejo actions
- sandbox execution
- web actions
- social goal negotiation
- multiple simultaneous goals
- externally assigned tasks

Those remain later milestones so intentional state can be tested independently from external capabilities.
