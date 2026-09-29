from dse.contracts.agent import LifecycleState
from dse.contracts.event import WorldEvent, make_event, make_tick_event
from dse.contracts.experiment import ExperimentManifest, SleepMemoryConfig
from dse.engine.memory import (
    boosted_salience,
    select_consolidation_candidates,
    select_forgetting_candidates,
)
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

                if manifest.agents.memory.enabled and manifest.agents.sleep_memory.enabled:
                    events.extend(
                        _emit_sleep_memory_events(
                            world,
                            actor_agent_id=agent_id,
                            config=manifest.agents.sleep_memory,
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


def _emit_sleep_memory_events(
    world: WorldState,
    *,
    actor_agent_id: str,
    config: SleepMemoryConfig,
) -> list[WorldEvent]:
    agent = world.agents[actor_agent_id]
    events: list[WorldEvent] = []

    consolidation_candidates = select_consolidation_candidates(
        agent,
        limit=config.strengthen_top_k,
    )
    consolidated_ids: set[str] = set()

    for episode in consolidation_candidates:
        old_salience = episode.salience
        new_salience = boosted_salience(old_salience, config.salience_boost)
        if new_salience == old_salience:
            continue

        events.append(
            _emit(
                world,
                event_type="memory.episode.consolidated",
                actor_agent_id=actor_agent_id,
                payload={
                    "memory_id": episode.memory_id,
                    "old_salience": old_salience,
                    "new_salience": new_salience,
                    "reason": "sleep_consolidation",
                },
            )
        )
        consolidated_ids.add(episode.memory_id)

    forgetting_candidates = select_forgetting_candidates(
        agent,
        current_tick=world.tick,
        below_salience=config.forget_below_salience,
        older_than_ticks=config.forget_older_than_ticks,
        limit=config.max_forget_per_sleep,
        excluded_memory_ids=consolidated_ids,
    )

    for episode in forgetting_candidates:
        events.append(
            _emit(
                world,
                event_type="memory.episode.forgotten",
                actor_agent_id=actor_agent_id,
                payload={
                    "memory_id": episode.memory_id,
                    "salience": episode.salience,
                    "age_ticks": world.tick - episode.created_tick,
                    "reason": "sleep_forgetting",
                },
            )
        )

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
