from dse.contracts.agent import LifecycleState
from dse.contracts.event import WorldEvent, make_event, make_tick_event
from dse.contracts.experiment import ExperimentManifest
from dse.engine.reducer import apply_event
from dse.engine.world import WorldState


def advance_cognitionless_tick(
    world: WorldState,
    manifest: ExperimentManifest,
) -> list[WorldEvent]:
    events: list[WorldEvent] = []

    tick_event = make_tick_event(
        experiment_id=world.experiment_id,
        sequence_number=world.last_sequence_number + 1,
        world_tick=world.tick + 1,
    )
    apply_event(world, tick_event)
    events.append(tick_event)

    lifecycle = manifest.agents.lifecycle
    cognition = manifest.agents.cognition

    for agent_id in sorted(world.agents):
        agent = world.agents[agent_id]

        if agent.lifecycle_state == LifecycleState.BORN:
            events.append(
                _emit(
                    world,
                    event_type="agent.lifecycle.wake",
                    actor_agent_id=agent_id,
                    payload={
                        "activity_units": lifecycle.active_ticks_per_cycle,
                        "model_calls": cognition.model_calls_per_cycle,
                        "cycles_completed_delta": 0,
                    },
                )
            )

        if agent.lifecycle_state in {LifecycleState.AWAKE, LifecycleState.IDLE}:
            if agent.resources.activity_units_remaining > 0:
                events.append(
                    _emit(
                        world,
                        event_type="resource.activity.consumed",
                        actor_agent_id=agent_id,
                        payload={"amount": 1},
                    )
                )
                events.append(
                    _emit(
                        world,
                        event_type="agent.activity.idle",
                        actor_agent_id=agent_id,
                    )
                )

            if agent.resources.activity_units_remaining == 0:
                events.append(
                    _emit(
                        world,
                        event_type="agent.lifecycle.sleep",
                        actor_agent_id=agent_id,
                        payload={"sleep_ticks": lifecycle.sleep_ticks_per_cycle},
                    )
                )

        elif agent.lifecycle_state == LifecycleState.SLEEPING:
            events.append(
                _emit(
                    world,
                    event_type="resource.sleep.elapsed",
                    actor_agent_id=agent_id,
                    payload={"amount": 1},
                )
            )
            if agent.resources.sleep_ticks_remaining == 0:
                events.append(
                    _emit(
                        world,
                        event_type="agent.lifecycle.wake",
                        actor_agent_id=agent_id,
                        payload={
                            "activity_units": lifecycle.active_ticks_per_cycle,
                            "model_calls": cognition.model_calls_per_cycle,
                            "cycles_completed_delta": 1,
                        },
                    )
                )

    return events


def advance_cognitionless_ticks(
    world: WorldState,
    manifest: ExperimentManifest,
    count: int,
) -> list[WorldEvent]:
    if count < 0:
        raise ValueError("count must be non-negative")

    events: list[WorldEvent] = []
    for _ in range(count):
        events.extend(advance_cognitionless_tick(world, manifest))
    return events


def _emit(
    world: WorldState,
    *,
    event_type: str,
    actor_agent_id: str,
    payload: dict | None = None,
) -> WorldEvent:
    event = make_event(
        experiment_id=world.experiment_id,
        sequence_number=world.last_sequence_number + 1,
        world_tick=world.tick,
        event_type=event_type,
        actor_agent_id=actor_agent_id,
        payload=payload,
    )
    apply_event(world, event)
    return event
