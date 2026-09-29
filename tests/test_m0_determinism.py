from copy import deepcopy
from pathlib import Path

from dse.contracts.experiment import load_manifest
from dse.engine.reducer import apply_event
from dse.engine.scheduler import advance_ticks
from dse.engine.snapshot import restore_world, serialize_world, world_state_hash
from dse.engine.world import create_world


MANIFEST = Path("experiments/v0_1/empty-world.yaml")


def test_identical_manifest_creates_identical_initial_world() -> None:
    manifest = load_manifest(MANIFEST)

    world_a = create_world(manifest)
    world_b = create_world(manifest)

    assert world_state_hash(world_a) == world_state_hash(world_b)
    assert list(world_a.agents) == [
        "agent-0001",
        "agent-0002",
        "agent-0003",
        "agent-0004",
        "agent-0005",
    ]


def test_snapshot_restore_preserves_state_hash_after_10000_ticks() -> None:
    manifest = load_manifest(MANIFEST)
    world = create_world(manifest)

    advance_ticks(world, 10_000)

    snapshot = serialize_world(world)
    restored = restore_world(snapshot)

    assert world.tick == 10_000
    assert world.last_sequence_number == 10_000
    assert world_state_hash(restored) == world_state_hash(world)


def test_replay_same_event_stream_reconstructs_identical_state() -> None:
    manifest = load_manifest(MANIFEST)

    initial = create_world(manifest)
    executed = deepcopy(initial)
    events = advance_ticks(executed, 10_000)

    replayed = deepcopy(initial)
    for event in events:
        apply_event(replayed, event)

    assert world_state_hash(replayed) == world_state_hash(executed)
