# M6 — Typed Action Intent / Tool Boundary

M6 introduces the first action-facing contract without executing any external tool.

Its purpose is to separate:

```text
thinking about an action
        ≠
executing an action
```

This boundary must exist before Forgejo writes, sandbox commands, web actions, or any other side effects are allowed.

## Action configuration

Action proposal generation is opt-in:

```yaml
agents:
  actions:
    enabled: true
    proposals_per_cycle: 2
```

The proposal budget is part of agent resource state and resets on wake.

## Allowed action kinds

M6 allows only three typed non-executing intents:

- `inspect_workspace`
- `draft_artifact`
- `run_validation`

There is deliberately no arbitrary shell-command field.

## Action proposal schema

Every proposal contains:

- kind
- summary
- target
- rationale
- expected_value
- estimated_cost
- optional draft_content

`draft_content` is required only for `draft_artifact` and forbidden for the other action kinds.

## CognitionDecision/v0.4

When actions are enabled, cognition uses `CognitionDecision/v0.4`.

It adds:

```text
decision = propose_action
action = ActionProposal
```

Older schemas remain unchanged:

- v0.1: no goals or actions
- v0.2: self-generated goal creation
- v0.3: goal lifecycle
- v0.4: typed action intent proposals

## Acceptance path

A valid proposal is accepted only when:

1. the agent has an active goal,
2. action proposal budget remains,
3. the proposal passes the strict schema.

Accepted intent:

```text
agent.cognition.decided
        ↓
resource.action_proposal.consumed
        ↓
agent.action.proposed
```

Rejected intent:

```text
agent.cognition.decided
        ↓
agent.action.rejected
```

Rejections do not consume proposal budget.

## Persistent action record

An accepted proposal becomes `ActionIntentRecord` with:

- deterministic action ID
- creation tick
- source cognition event
- active goal ID
- typed proposal fields
- status = proposed

It is stored in `AgentState.actions.proposals`.

This means action intents participate in:

- state hashes
- snapshots
- event replay
- PostgreSQL restore
- later provenance analysis

## No execution in M6

M6 emits no:

- action executed event
- tool call
- Forgejo request
- shell process
- sandbox job
- filesystem mutation
- network mutation

A proposed `draft_artifact` is only data inside an event/state record. It does not create a file.

## Deterministic fixture

The M6 fake-provider fixture uses:

- 5 agents
- cognition every 150 ticks
- 6 model calls per active cycle
- 2 action proposals per active cycle

Expected first 900 ticks per agent:

```text
150 → self-generate goal
300 → propose inspect_workspace
450 → propose draft_artifact
600 → goal progress 0.5
750 → goal progress 0.8
900 → complete goal
```

Across five agents this yields:

- 5 goal creation events
- 10 action proposal events
- 10 action-budget consumption events
- 10 goal progress events
- 5 goal completion events
- 0 tool/execution events

## Budget enforcement

If a provider ignores the remaining budget and continues proposing actions, each over-budget proposal produces:

```text
agent.action.rejected
reason = budget_exhausted
```

The budget cannot become negative.

## Scientific boundary

Action intent is not evidence of external competence.

Later claims about capability require an execution plane plus objective evidence such as:

- repository diffs
- executable artifacts
- test results
- hidden functional evaluation
- provenance DAGs

## Next boundary

The next milestone should introduce a **Tool Broker with a deterministic fake executor first**.

That layer should validate an accepted action intent and emit execution/result events while still avoiding real Forgejo or sandbox side effects.

Only after that boundary is replayable, budgeted, rejectable, and testable should real isolated tools be attached.
