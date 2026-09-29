# M15 — Persistent Text Culture and Bounded Social Communication

M15 closes the non-executable cultural treatment gap introduced by the V0.1
condition matrix.

The implementation deliberately separates three concepts:

- persistent public text culture,
- generation-scoped direct communication,
- persistent public issue/PR/message threads.

These are world-state treatments, not personality or relationship simulation.

## Treatment mapping

### P — Personal

- persistent text: disabled
- direct social channel: disabled
- public social threads: disabled

The report's "limited communication" is conservatively operationalized as no
direct agent-to-agent channel.

### T — Text Culture

- persistent text: enabled
- direct social messages: enabled
- executable Forge culture: disabled

T is research-runtime-ready after M15.

### E — Executable Culture

- persistent text treatment: disabled
- social channel: disabled
- Forge substrate: enabled

E remains not research-runtime-ready until hardened execution and the hidden
functional evaluator exist.

### ES — Executable + Social

- persistent text: enabled
- public issue/PR/message channel: enabled
- Forge substrate: enabled

M15 closes the text/social surface gap. ES remains not research-runtime-ready
only because hardened executable evaluation is still missing.

### RIL

- persistent text: disabled
- social channel: disabled

## World-state separation

Persistent text and social state live outside AgentState:

```text
WorldState
├── agents            private individual state
├── text_culture      persistent public text entries
├── social            direct-message audit log + public threads
└── forge             persistent executable-artifact substrate
```

Turnover resets the selected AgentState but does not mutate these global world
stores.

## Persistent text records

Each text entry records:

- entry ID,
- world tick,
- creator agent ID,
- creator generation,
- source event ID,
- source action ID,
- title,
- bounded content,
- SHA-256,
- byte count,
- explicit parent text entry IDs.

Parent IDs create an auditable text-cultural lineage independent of private
episodic memory.

## Direct-message inheritance boundary

Direct messages are persistent in the immutable/auditable world history, but
visibility is generation-scoped.

A message records both:

```text
sender_agent_id
sender_generation
recipient_agent_id
recipient_generation
```

After turnover:

```text
agent-0001 generation 0 inbox
        ↓ replacement
agent-0001 generation 1
```

generation 1 cannot see generation-0 direct messages through cognition context.

This prevents the direct channel from becoming accidental episodic-memory
inheritance.

## Public issue/PR/message threads

ES uses public threads rather than direct inboxes.

Public thread content survives turnover and remains visible to later generations.

This is intentional cultural inheritance:

```text
generation 0 public issue
        ↓ persists
generation 1 observes same issue
```

## Budgets

Every agent receives bounded per-cycle operation budgets:

- text operations,
- social operations.

The five strict experiment manifests use identical budget and capacity values.
Only the treatment enable/mode switches differ.

This keeps the treatment independent variable separate from resource capacity.

## Cognition contract

CognitionDecision/v0.6 adds treatment actions:

- text_publish
- social_send_message
- social_open_issue
- social_open_pr
- social_post_message

The cognition context exposes only treatment-permitted world evidence.

P/E/RIL do not receive text/social treatment context.

T receives persistent text plus generation-scoped direct messages.

ES receives persistent text plus public issue/PR/message threads.

## Runtime ordering

One M15 cultural runtime tick is:

```text
world/lifecycle tick
↓
structured cognition
↓
Forge action broker when enabled
↓
text/social action broker
```

All resulting mutations are event-sourced and replayable.

## Deterministic acceptance fixtures

The fake provider is only an architectural acceptance fixture.

T uses:

```text
goal
text publish
direct message
lineage-linked text publish
second direct message
goal progress
goal completion
```

ES uses:

```text
goal
create repository
publish artifact
text publish
open public issue
goal progress
goal completion
```

The deterministic sequence is not evidence of spontaneous culture.

## Turnover invariants

Turnover events carry hashes for:

- public Forge state,
- text culture state,
- complete social world audit state.

The reducer verifies those hashes before replacement.

Private action/cognition/memory state is deleted.

Persistent text/public social culture remains.

## Scientific status after M15

- P: prototype-ready
- T: research-runtime-ready for the declared text/direct-social treatment
- E: missing hardened executable evaluation
- ES: text/social treatment implemented; still missing hardened executable evaluation
- RIL: prototype-ready

The next milestone should implement the separate hidden functional evaluator and
hardened artifact execution boundary for E/ES rather than adding more social or
biological mechanics.
