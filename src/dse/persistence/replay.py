from sqlalchemy import Engine

from dse.engine.reducer import apply_event
from dse.engine.snapshot import restore_world, world_state_hash
from dse.engine.world import WorldState, create_world
from dse.persistence.event_store import PostgresEventStore
from dse.persistence.experiment_store import load_experiment_manifest
from dse.persistence.snapshot_store import PostgresSnapshotStore


class SnapshotIntegrityError(ValueError):
    """Raised when persisted snapshot bytes do not match their recorded hash."""


def restore_persisted_world(engine: Engine, experiment_id: str) -> WorldState:
    manifest = load_experiment_manifest(engine, experiment_id)
    snapshot = PostgresSnapshotStore(engine).load_latest(experiment_id)

    if snapshot is None:
        world = create_world(manifest)
        after_sequence = 0
    else:
        world = restore_world(snapshot.state)
        actual_hash = world_state_hash(world)
        if actual_hash != snapshot.state_hash:
            raise SnapshotIntegrityError(
                f"Snapshot hash mismatch: expected {snapshot.state_hash}, got {actual_hash}"
            )
        after_sequence = snapshot.last_sequence_number

    events = PostgresEventStore(engine).load_events(
        experiment_id,
        after_sequence=after_sequence,
    )
    for event in events:
        apply_event(world, event)

    return world
