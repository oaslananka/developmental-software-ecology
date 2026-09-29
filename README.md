# Developmental Software Ecology

An experimental platform for studying **executable cultural evolution in persistent agent ecologies**.

The project asks whether initially identical, self-directed LLM agents with bounded private memory can accumulate measurable capability across agent turnover through persistent, executable Git artifacts.

## Current status

**V0.1 — M0: Deterministic Empty World**

The first milestone intentionally contains no LLM, Forgejo, sandbox, embeddings, or dashboard. M0 establishes the deterministic experimental core:

- immutable experiment manifests
- identical initial agents
- monotonic event ordering
- reducer-based state transitions
- snapshots
- replay
- canonical state hashing

### M0 gate

```text
manifest
  -> world creation
  -> 5 identical agents
  -> deterministic event stream
  -> snapshot
  -> shutdown
  -> restore
  -> replay
  -> identical state hash
```

## Research framing

This is not a generic "AI town" or human-life simulator. V0.1 tests a narrower falsifiable question: whether persistent executable artifacts can function as cumulative culture across individual agent turnover without inheriting private episodic memory.

## Development workflow

Implementation work proceeds on feature branches and is merged through pull requests after deterministic replay tests pass.
