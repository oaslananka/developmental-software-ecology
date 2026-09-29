from dse.contracts.event import WorldEvent
from dse.engine.world import WorldState


def apply_event(world: WorldState, event: WorldEvent) -> None:
    expected_sequence = world.last_sequence_number + 1
    if event.sequence_number != expected_sequence:
        raise ValueError(
            f"Non-contiguous event sequence: expected {expected_sequence}, "
            f"received {event.sequence_number}"
        )

    if event.experiment_id != world.experiment_id:
        raise ValueError("Event belongs to a different experiment")

    if event.world_tick < world.tick:
        raise ValueError("World tick cannot move backwards")

    match event.event_type:
        case "world.tick.advanced":
            expected_tick = world.tick + 1
            if event.world_tick != expected_tick:
                raise ValueError(
                    f"Tick event must advance exactly one step: expected {expected_tick}, "
                    f"received {event.world_tick}"
                )
            world.tick = event.world_tick

        case _:
            raise ValueError(f"Unknown event type: {event.event_type}")

    world.last_sequence_number = event.sequence_number
