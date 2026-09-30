# M19 — Fair Cross-Condition Functional Measurement

M19 separates the **thing being measured** from the **persistent cultural
substrate being manipulated**.

Before M19, the hidden functional evaluator received the current Forge
path-head artifacts directly. That was useful for checking executable public
culture, but it was not a fair primary outcome across P/T/E/ES/RIL because
Forge is enabled only for E and ES.

## Causal boundary

The corrected measurement path is:

```text
same public functional opportunity
        |
        v
current agent / generation
        |
        v
bounded current-generation submission
        |
        v
same hidden evaluator
        |
        v
functional score
```

Condition-specific persistent state may influence how the current generation
constructs that submission:

```text
P   private/current state only
T   persistent text + bounded direct social channel
E   persistent executable Forge culture
ES  persistent executable + text + public social coordination
RIL isolated repeated individual learning
```

The evaluator does not substitute Forge artifacts directly for the common
submission channel.

## Public opportunity profile

Strict V0.1 conditions use:

```text
functional_opportunity_profile = v0_1-functional-submission
```

The canonical profile exposes only a public packaging contract:

- submission path: `submission.py`;
- entrypoint: `solve`;
- maximum UTF-8 submission size: 4000 bytes;
- the submission is a pure-Python capability module;
- the entrypoint accepts a payload and returns JSON-compatible data;
- hidden evaluator cases and expected outputs are not disclosed.

This profile is deliberately **not** the hidden suite. It is safe to place in
agent cognition because it contains no held-out case material.

A later experiment-design step must still freeze the substantive public
environment/problem family that gives `solve(payload)` scientific meaning.
M19 does not manufacture that missing task semantics.

## Submission state

`functional_submit` is a typed action proposal.

An accepted submission records:

- deterministic submission ID;
- source action ID;
- world tick;
- agent slot;
- current generation;
- canonical path;
- UTF-8 source content;
- byte count;
- SHA-256 content hash.

Only one current submission is retained per agent slot. A later submission in
the same generation replaces the current one in state; provenance remains in
the event stream.

Submission publication consumes the same ordinary action-proposal budget as
other agent actions. The strict V0.1 manifests raise that shared budget from
four to five proposals per active cycle so every treatment receives the same
additional opportunity.

## Turnover

A functional submission is a current-generation phenotype/output, not
inherited culture.

At scheduled turnover:

- the previous current submission is counted as private/current state;
- replacement resets the functional-submission state to empty;
- public Forge/text/social state is preserved according to the treatment;
- the next generation must construct a new submission.

This is the key causal invariant. Any post-turnover E/ES advantage should have
to pass through the new generation's own common submission channel.

## Evaluator snapshot

The M16 snapshot schema is retained for compatibility, but its
`artifacts` entries now represent current functional submissions rather than
Forge path heads.

Each current submission is mapped into the existing evaluation artifact shape:

- `repo_id = submission:<agent-id>`;
- `repo_name = current-generation-submissions`;
- artifact ID = submission ID;
- commit ID = source action ID;
- creator agent/generation = submission owner;
- parent artifact IDs = empty.

This keeps the existing M17/M18/M18.2/M18.3 transport and hardened-worker
bindings intact while changing the scientific object that is scored.

The evaluator still receives no private memories, goals, hidden suite source,
hidden expected outputs, or case diagnostics.

## Compute matching

The compute budget fingerprint now includes:

- functional opportunity profile;
- submission path;
- maximum submission bytes.

All strict P/T/E/ES/RIL manifests use the same values.

Observed usage also counts `functional.submission.published` events so pilot
reports can detect a treatment that failed to produce submissions.

## Readiness gate

Because the common functional score is now intended for every treatment,
runtime readiness for **all** P/T/E/ES/RIL conditions requires matching
runtime-measured hardened evaluator evidence.

P/T/RIL are no longer considered research-runtime ready merely because they do
not use executable Forge culture.

## What M19 proves

M19 proves the architecture can compare all treatment conditions through the
same generation-scoped executable output channel without giving E/ES a
measurement-only shortcut.

It does not yet prove:

- the substantive public environmental problem family is scientifically
  appropriate;
- the canonical private V0.1 suite exists;
- the currently pinned suite hash has recovered provenance;
- the private suite has passed real TLS -> worker -> gVisor acceptance;
- any treatment has produced cumulative culture in a real model pilot.

Those remain subsequent gates.
