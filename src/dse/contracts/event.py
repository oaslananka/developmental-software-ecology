import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


EVENT_NAMESPACE = uuid.UUID("7a512f95-2ec4-4d43-a02d-d25cbb8b93da")


class WorldEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "0.1"
    event_id: str
    experiment_id: str
    sequence_number: int = Field(ge=1)
    world_tick: int = Field(ge=0)
    event_type: str
    actor_agent_id: str | None = None
    causal_parent_event_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at_wall_clock: datetime


def deterministic_event_id(experiment_id: str, sequence_number: int) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{experiment_id}:{sequence_number}"))


def make_tick_event(
    *,
    experiment_id: str,
    sequence_number: int,
    world_tick: int,
) -> WorldEvent:
    return WorldEvent(
        event_id=deterministic_event_id(experiment_id, sequence_number),
        experiment_id=experiment_id,
        sequence_number=sequence_number,
        world_tick=world_tick,
        event_type="world.tick.advanced",
        created_at_wall_clock=datetime.now(UTC),
    )
