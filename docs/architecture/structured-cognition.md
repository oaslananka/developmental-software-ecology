# M2 — Provider-neutral Structured Cognition

M2 adds the model boundary without choosing a production model provider.

The first implementation uses a deterministic fake provider so the orchestration contract can be tested independently of model quality, network availability, pricing, or vendor behavior.

## Design constraints

A model call is not a world tick.

Cognition only occurs when all of the following are true:

1. cognition is enabled by the experiment manifest,
2. the runtime model flag is enabled,
3. the agent is not sleeping,
4. the configured cognition interval is reached,
5. the agent still has model-call budget.

The M2 fixture allows four calls per active cycle, triggered every 200 ticks.

## Structured output

The accepted response is `CognitionDecision/v0.1`:

```text
decision: idle | observe
reason_summary: short observable explanation
confidence: 0..1
focus: optional short label
```

Unknown fields are rejected. The system stores only this bounded structured decision record, not unrestricted internal reasoning.

## Provider contract

`ModelProvider.generate(ModelRequest) -> ModelResponse` is asynchronous and provider-neutral.

Every completed call records:

- provider
- model
- model version
- request hash
- response hash
- input/output token usage
- response schema

These values enter the event stream through `model.call.completed`.

## Budget

The model-call budget is part of agent resource state and resets on wake. Calls consume `resource.model_call.consumed` events.

During sleep there are no model calls.

## Fake provider

`DeterministicFakeProvider` exists only to verify:

- contract validity,
- deterministic orchestration,
- budget enforcement,
- provenance,
- event replay,
- PostgreSQL persistence.

It is not an intelligence benchmark and is not intended to approximate a real LLM.

## Next boundary

A real provider adapter should be a separate integration layer. It must not change agent identity, lifecycle rules, event semantics, or experiment manifests beyond provider/model selection and operational limits.
