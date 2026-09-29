# M7 — Deterministic Tool Broker and Fake Executor

M7 introduces an execution boundary while still avoiding real external side effects.

The design separates three layers:

```text
LLM cognition
    ↓
typed action intent
    ↓
Tool Broker policy/budget validation
    ↓
deterministic fake executor
    ↓
event-sourced result
```

## Why a fake executor first

Before connecting Forgejo, a sandbox, a filesystem, or the web, the execution protocol itself must be:

- typed,
- budgeted,
- rejectable,
- deterministic,
- replayable,
- persisted,
- provenance-linked.

M7 validates those properties without trusting an operating-system or network boundary.

## Tool Broker configuration

```yaml
agents:
  tool_broker:
    enabled: true
    executions_per_cycle: 2
```

The execution budget is part of agent resource state and resets on wake.

## Pending-intent processing

The broker only considers action intents whose status is:

```text
proposed
```

Each proposal becomes terminal after one broker decision:

- `executed`, or
- `rejected`.

The broker never executes an action twice.

## Policy validation

Before execution, the broker rejects unsafe targets.

Current deterministic policy rejects:

- absolute paths,
- network-style targets containing `://`,
- parent-directory traversal with `..`,
- non-`workspace` targets for `inspect_workspace`.

Policy rejection does not consume tool execution budget.

## Execution budget

A safe pending intent with no remaining execution budget is rejected with:

```text
tool.execution.rejected
reason = execution_budget_exhausted
```

The budget cannot become negative.

## Execution events

A successful fake execution emits:

```text
resource.tool_execution.consumed
tool.execution.started
tool.execution.completed
```

The completion event contains a strict `ToolExecutionRecord`.

## Deterministic fake executor

The fake executor supports the same three intent kinds as M6.

### inspect_workspace

Returns a fixed simulated directory listing.

It does not inspect the real repository or host filesystem.

### draft_artifact

Returns a preview result containing:

- target,
- content hash,
- byte length.

It does not create or modify a file.

### run_validation

Returns a fixed simulated validation report.

It does not spawn a process or run a real test command.

All fake results contain:

```text
simulated = true
```

## Result provenance

Each execution record stores:

- execution ID,
- action ID,
- world tick,
- executor name,
- success flag,
- result type,
- summary,
- structured result data,
- deterministic result hash.

The action intent itself also retains its source cognition event and goal ID.

This gives the causal chain:

```text
goal
  ↓
cognition event
  ↓
action intent
  ↓
execution event
  ↓
result hash
```

## Cognition feedback

When the broker is enabled, up to four recent execution results are included in later model context as `tool_results`.

This allows a later real provider to condition its cognition on tool evidence without letting the provider mutate tool state directly.

## M7 deterministic fixture

For five agents over the first 900 ticks:

- 5 self-generated goals,
- 10 action intents,
- 10 tool-execution budget consumption events,
- 10 execution-started events,
- 10 execution-completed events,
- 10 goal progress events,
- 5 goal completions.

Every execution is produced by `deterministic-fake`.

## No real external side effects

M7 still performs no:

- Forgejo API request,
- Git operation,
- shell process,
- sandbox process,
- host filesystem write,
- external network request,
- web action.

The only new state is event-sourced simulated execution evidence.

## Scientific boundary

A successful M7 fake execution is not evidence of real software capability.

It validates the control plane and provenance architecture only.

Real capability claims require later milestones with isolated real execution plus objective evaluation.

## Next boundary

The next milestone should replace **one** fake capability with an isolated real adapter while preserving the exact broker interface.

The safest next target is a local, controlled workspace/sandbox capability—not unrestricted internet or public GitHub writes.
