# M2.1 — OpenCode Inference Provider

Verified against OpenCode documentation and the live model catalog on 2026-09-29.

## Endpoint

The adapter targets the OpenAI-compatible Chat Completions surface:

```text
https://opencode.ai/inference/openai/v1/chat/completions
```

The experiment manifest stores only non-secret provider configuration:

- provider kind
- model ID
- base URL
- timeout

Credentials are never stored in the manifest.

## Authentication

`OPENCODE_API_KEY` is read from the process environment when present.

OpenCode currently documents free chat models as callable without an Authorization header. The adapter therefore treats the API key as optional.

Paid or authenticated models can use the same adapter by setting `OPENCODE_API_KEY`.

## Default smoke model

The M2.1 smoke manifest currently uses:

```text
space-bunny-free
```

This model ID was present in the OpenCode live model catalog when this integration was added.

Model availability is external and may change. Experiments intended for publication must record the exact model ID and run date in provenance.

## Strict response boundary

The model is prompted to emit one JSON object matching `CognitionDecision/v0.1`.

The adapter does not trust prompt compliance. The returned assistant content is parsed through the strict Pydantic schema before it can become an agent cognition event.

Invalid JSON, missing fields, unknown fields, or invalid values raise `ProviderResponseError`.

## Reproducibility boundary

Real provider inference is stochastic and externally versioned. Reproducibility therefore has two levels:

- replay: use the persisted event/model response history without calling OpenCode again;
- replication: perform new provider calls under the recorded manifest and compare outcomes statistically.

The deterministic fake provider remains the CI/reference provider.

## CI policy

Continuous integration must not depend on OpenCode availability, rate limits, or free-tier policy.

OpenCode HTTP behavior is tested with `httpx.MockTransport`.

A live smoke call is operational validation, not a required CI gate.
