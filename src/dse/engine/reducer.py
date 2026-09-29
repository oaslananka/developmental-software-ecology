from dse.contracts.agent import LifecycleState
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

        case "agent.lifecycle.wake":
            agent = _agent_for_event(world, event)
            agent.lifecycle_state = LifecycleState.AWAKE
            agent.resources.activity_units_remaining = int(event.payload["activity_units"])
            agent.resources.sleep_ticks_remaining = 0
            agent.resources.cycles_completed += int(
                event.payload.get("cycles_completed_delta", 0)
            )
            agent.state_version += 1

        case "resource.activity.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Activity consumption must be positive")
            if agent.resources.activity_units_remaining < amount:
                raise ValueError("Activity budget cannot become negative")
            agent.resources.activity_units_remaining -= amount
            agent.state_version += 1

        case "agent.activity.idle":
            agent = _agent_for_event(world, event)
            agent.lifecycle_state = LifecycleState.IDLE
            agent.last_active_tick = event.world_tick
            agent.state_version += 1

        case "agent.lifecycle.sleep":
            agent = _agent_for_event(world, event)
            sleep_ticks = int(event.payload["sleep_ticks"])
            if sleep_ticks <= 0:
                raise ValueError("Sleep duration must be positive")
            agent.lifecycle_state = LifecycleState.SLEEPING
            agent.resources.sleep_ticks_remaining = sleep_ticks
            agent.state_version += 1

        case "resource.sleep.elapsed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if agent.lifecycle_state != LifecycleState.SLEEPING:
                raise ValueError("Sleep progress requires a sleeping agent")
            if amount <= 0:
                raise ValueError("Sleep progress must be positive")
            if agent.resources.sleep_ticks_remaining < amount:
                raise ValueError("Sleep ticks cannot become negative")
            agent.resources.sleep_ticks_remaining -= amount
            agent.state_version += 1

        case _:
            raise ValueError(f"Unknown event type: {event.event_type}")

    world.last_sequence_number = event.sequence_number


def _agent_for_event(world: WorldState, event: WorldEvent):
    if event.actor_agent_id is None:
        raise ValueError(f"{event.event_type} requires actor_agent_id")
    try:
        return world.agents[event.actor_agent_id]
    except KeyError as error:
        raise ValueError(f"Unknown agent: {event.actor_agent_id}") from error
