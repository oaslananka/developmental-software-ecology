# M14 — Experiment Condition and Compute-Matching Harness

M14 turns the research matrix into an enforceable experiment contract.

It does not add a new agent capability.

Its job is to prevent a future result from being explained by silent configuration drift.

## Source matrix

The V0.1 research design defines:

| Condition | Persistent text | Executable culture | Social channel | Turnover |
|---|---|---|---|---|
| P — Personal | none | none | limited | yes |
| T — Text Culture | persistent | none | direct | yes |
| E — Executable Culture | docs-only allowance | required | none | yes |
| ES — Executable + Social | persistent | required | issues/PR/messages | yes |
| RIL | none | none | isolated | control topology |

The report also requires the cultural conditions to share:

- model and exact model/version provenance,
- system prompt,
- starting state,
- agent count,
- private-memory capacity,
- token/action budget,
- environmental opportunity,
- hidden evaluator,
- turnover schedule.

RIL is intentionally not forced into one topology. The research brief allows a
single isolated learner or independent isolated learners as long as total token
budget, duration, and artifact capacity are matched.

## Opt-in contract

Older milestone manifests remain valid.

Strict experimental enforcement activates only when:

```yaml
study:
  enforce_condition_contract: true
```

This prevents M14 from retroactively redefining development fixtures that used
condition labels before the experimental matrix was frozen.

## Treatment declaration

Strict manifests declare:

```yaml
study:
  text_culture: none|persistent|docs_only
  artifact_culture: none|executable
  social_channel: none|limited|direct|issues_pr_messages|isolated
  ril_control: true|false
  compute_match_group: v0_1-primary-compute
  ril_topology: not_applicable|single_isolated|independent_isolated
```

The condition code and treatment declaration must match exactly.

A P manifest cannot silently enable executable Forge culture.

An E manifest cannot silently disable the Forge cultural substrate.

## Runtime readiness is separate from design validity

A manifest can be scientifically well-specified while the current codebase still
lacks a required treatment implementation.

M14 reports this explicitly.

Current support status:

### P — prototype-ready

The M14 Personal condition operationalizes the report's "limited communication"
as **no direct agent-to-agent channel**.

This is a conservative isolation choice and is documented rather than hidden.

### RIL — prototype-ready

RIL is isolated and has no persistent shared culture.

The manifest must explicitly choose one allowed isolated topology.

Compute matching is tested separately.

### T — not research-runtime-ready

Missing:

- persistent text-culture runtime,
- direct social channel.

### E — not research-runtime-ready for the paper claim

The deterministic Forge substrate exists, but "executable culture" requires more
than storing code-like artifacts.

Still missing:

- a real hardened execution backend,
- separate hidden functional evaluator.

Therefore M12/M13 public-artifact behavior is an architecture prototype, not yet
the full E treatment claimed by the research question.

### ES — not research-runtime-ready

Still missing:

- persistent text-culture runtime,
- issues/PR/message social channel,
- hardened artifact execution,
- hidden functional evaluator.

This fail-closed status prevents anthropomorphic/demo behavior from being
misreported as a completed scientific treatment.

## Compute budget fingerprint

M14 computes a treatment-independent budget fingerprint from:

- agent count,
- world duration and tick duration,
- active/sleep cycle sizes,
- turnover schedule and population,
- model calls per cycle,
- cognition interval,
- goal lifecycle settings,
- action proposal budget,
- tool execution budget,
- private-memory capacity/retrieval limit,
- Forge operation and artifact capacity,
- model provider/model/base URL/timeout,
- sandbox and web availability.

It intentionally excludes the treatment itself:

- condition label,
- text-culture declaration,
- Forge enable/disable treatment switch,
- social treatment,
- RIL flag.

Five manifests can therefore differ in the independent variable while retaining
the same compute opportunity.

## Observed compute usage

Matching manifest budgets is not enough.

A treatment can still consume more actual model calls or tokens.

M14 derives observed usage from the immutable event stream:

- final world tick,
- model calls,
- input tokens,
- output tokens,
- total tokens,
- action proposals consumed,
- tool executions consumed,
- Forge operations consumed,
- turnovers,
- repositories created,
- artifacts committed.

The default observed-compute comparison matches:

- world duration,
- model-call count,
- input tokens,
- output tokens,
- total tokens,
- action proposals.

Forge operations remain visible but are not part of the default equality test
because they are treatment-specific behavior.

## Why both layers are required

```text
budget fingerprint
    ↓
equal opportunity before the run

observed compute usage
    ↓
equal realized model compute after the run
```

If either layer differs, "culture improved performance" is confounded.

## RIL operationalization

The source research brief permits:

```text
society:
5 agents × total token budget

RIL:
single isolated learner OR independent isolated learners
same total token budget
same duration
same artifact capacity
```

The M14 fixture chooses `independent_isolated` as an explicit pilot topology.

That is a pilot decision, not a claim that the report uniquely required it.

## M14 deterministic acceptance

The five strict manifests share one budget fingerprint.

P and RIL are then run through the current deterministic structured-cognition
runtime for 280 ticks.

Both must show exactly:

- 70 model calls,
- 2,240 input tokens,
- 840 output tokens,
- 3,080 total tokens,
- 40 action proposals,
- zero Forge operations.

This proves the harness can distinguish declared budget matching from observed
compute matching.

## Hidden evaluator boundary

M14 does not implement hidden functional evaluation.

The evaluator remains a required future plane and must not feed benchmark tasks
into daily agent cognition.

E and ES remain not research-ready until this boundary and hardened execution are
real.

## Next

The next implementation work should close treatment gaps, not add life-simulation
decor:

1. persistent text culture + bounded social communication for T/ES,
2. hardened executable evaluation plane for E/ES,
3. then multi-seed experiment orchestration and objective capability metrics.
