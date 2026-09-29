from dse.contracts.event import WorldEvent, make_tick_event
from dse.engine.reducer import apply_event
from dse.engine.world import WorldState


def advance_one_tick(world: WorldState) -> WorldEvent:
    event = make_tick_event(
        experiment_id=world.experiment_id,
        sequence_number=world.last_sequence_number + 1,
        world_tick=world.tick + 1,
    )
    apply_event(world, event)
    return event


def advance_ticks(world: WorldState, count: int) -> list[WorldEvent]:
    if count < 0:
        raise ValueError("count must be non-negative")

    events: list[WorldEvent] = []
    for _ in range(count):
        events.append(advance_one_tick(world))
    return events
