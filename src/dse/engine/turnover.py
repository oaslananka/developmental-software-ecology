from dse.contracts.event import WorldEvent, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.world import WorldState


def process_scheduled_turnovers(
    world: WorldState,
    manifest: ExperimentManifest,
) -> list[WorldEvent]:
    config = manifest.agents.turnover
    if not config.enabled or world.tick not in config.ticks:
        return []

    events: list[WorldEvent] = []
    forge_hash = state_hash(world.forge.model_dump(mode="json"))

    for agent_id in sorted(config.agent_ids):
        try:
            agent = world.agents[agent_id]
        except KeyError as error:
            raise ValueError(
                f"Turnover schedule references unknown agent: {agent_id}"
            ) from error

        previous_generation = agent.generation
        next_generation = previous_generation + 1
        counts = {
            "cognition_calls": agent.cognition.calls_completed,
            "goals": len(agent.goals.goals),
            "actions": len(agent.actions.proposals),
            "tool_executions": len(agent.actions.executions),
            "forge_results": len(agent.actions.forge_results),
            "episodic_memories": len(agent.memory.episodes),
        }

        events.append(
            _emit(
                world,
                event_type="agent.lifecycle.turned_over",
                actor_agent_id=agent_id,
                payload={
                    "previous_generation": previous_generation,
                    "next_generation": next_generation,
                    "reason": "scheduled_turnover",
                    "private_state_counts": counts,
                    "public_forge_hash": forge_hash,
                },
            )
        )
        events.append(
            _emit(
                world,
                event_type="agent.lifecycle.replaced",
                actor_agent_id=agent_id,
                payload={
                    "previous_generation": previous_generation,
                    "new_generation": next_generation,
                    "birth_tick": world.tick,
                    "traits_preserved": True,
                    "public_forge_hash": forge_hash,
                },
            )
        )

    return events


def _emit(
    world: WorldState,
    *,
    event_type: str,
    actor_agent_id: str,
    payload: dict,
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
