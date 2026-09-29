from collections.abc import Iterable

from sqlalchemy import Engine, func, insert, select

from dse.contracts.event import WorldEvent
from dse.persistence.schema import experiments, world_events


class EventSequenceError(ValueError):
    """Raised when an append would make the event sequence non-contiguous."""


class PostgresEventStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, event: WorldEvent) -> None:
        self.append_many([event])

    def append_many(self, events: Iterable[WorldEvent]) -> None:
        batch = list(events)
        if not batch:
            return

        experiment_id = batch[0].experiment_id
        for event in batch:
            if event.experiment_id != experiment_id:
                raise EventSequenceError("A batch cannot mix experiment IDs")

        with self._engine.begin() as connection:
            registered = connection.execute(
                select(experiments.c.experiment_id)
                .where(experiments.c.experiment_id == experiment_id)
                .with_for_update()
            ).scalar_one_or_none()
            if registered is None:
                raise KeyError(f"Unknown experiment: {experiment_id}")

            last_sequence = connection.execute(
                select(func.coalesce(func.max(world_events.c.sequence_number), 0)).where(
                    world_events.c.experiment_id == experiment_id
                )
            ).scalar_one()

            expected = int(last_sequence) + 1
            for offset, event in enumerate(batch):
                required = expected + offset
                if event.sequence_number != required:
                    raise EventSequenceError(
                        f"Expected sequence {required}, received {event.sequence_number}"
                    )

            connection.execute(
                insert(world_events),
                [
                    {
                        "experiment_id": event.experiment_id,
                        "sequence_number": event.sequence_number,
                        "event_id": event.event_id,
                        "world_tick": event.world_tick,
                        "event_type": event.event_type,
                        "actor_agent_id": event.actor_agent_id,
                        "causal_parent_event_id": event.causal_parent_event_id,
                        "correlation_id": event.correlation_id,
                        "payload": event.payload,
                        "created_at_wall_clock": event.created_at_wall_clock,
                    }
                    for event in batch
                ],
            )

    def load_events(
        self,
        experiment_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[WorldEvent]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(world_events)
                    .where(
                        world_events.c.experiment_id == experiment_id,
                        world_events.c.sequence_number > after_sequence,
                    )
                    .order_by(world_events.c.sequence_number)
                )
                .mappings()
                .all()
            )

        return [
            WorldEvent(
                schema_version="0.1",
                event_id=row["event_id"],
                experiment_id=row["experiment_id"],
                sequence_number=row["sequence_number"],
                world_tick=row["world_tick"],
                event_type=row["event_type"],
                actor_agent_id=row["actor_agent_id"],
                causal_parent_event_id=row["causal_parent_event_id"],
                correlation_id=row["correlation_id"],
                payload=row["payload"],
                created_at_wall_clock=row["created_at_wall_clock"],
            )
            for row in rows
        ]

    def count_events(self, experiment_id: str) -> int:
        with self._engine.connect() as connection:
            count = connection.execute(
                select(func.count())
                .select_from(world_events)
                .where(world_events.c.experiment_id == experiment_id)
            ).scalar_one()
        return int(count)
