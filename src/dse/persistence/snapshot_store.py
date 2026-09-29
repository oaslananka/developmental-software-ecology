from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, insert, select

from dse.engine.snapshot import serialize_world, world_state_hash
from dse.engine.world import WorldState
from dse.persistence.schema import world_snapshots


@dataclass(frozen=True)
class SnapshotRecord:
    experiment_id: str
    last_sequence_number: int
    world_tick: int
    state: dict[str, Any]
    state_hash: str


class PostgresSnapshotStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, world: WorldState) -> SnapshotRecord:
        state = serialize_world(world)
        digest = world_state_hash(world)

        with self._engine.begin() as connection:
            connection.execute(
                insert(world_snapshots).values(
                    experiment_id=world.experiment_id,
                    last_sequence_number=world.last_sequence_number,
                    world_tick=world.tick,
                    state=state,
                    state_hash=digest,
                )
            )

        return SnapshotRecord(
            experiment_id=world.experiment_id,
            last_sequence_number=world.last_sequence_number,
            world_tick=world.tick,
            state=state,
            state_hash=digest,
        )

    def load_latest(self, experiment_id: str) -> SnapshotRecord | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(world_snapshots)
                    .where(world_snapshots.c.experiment_id == experiment_id)
                    .order_by(world_snapshots.c.last_sequence_number.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )

        if row is None:
            return None

        return SnapshotRecord(
            experiment_id=row["experiment_id"],
            last_sequence_number=row["last_sequence_number"],
            world_tick=row["world_tick"],
            state=row["state"],
            state_hash=row["state_hash"],
        )
