from typing import Any

from dse.contracts.agent import AgentState
from dse.contracts.forge import ForgeWorldState
from dse.engine.hashing import state_hash
from dse.engine.world import WorldState


def serialize_world(world: WorldState) -> dict[str, Any]:
    return {
        "experiment_id": world.experiment_id,
        "tick": world.tick,
        "last_sequence_number": world.last_sequence_number,
        "agents": {
            agent_id: agent.model_dump(mode="json")
            for agent_id, agent in sorted(world.agents.items())
        },
        "forge": world.forge.model_dump(mode="json"),
    }


def restore_world(snapshot: dict[str, Any]) -> WorldState:
    return WorldState(
        experiment_id=snapshot["experiment_id"],
        tick=snapshot["tick"],
        last_sequence_number=snapshot["last_sequence_number"],
        agents={
            agent_id: AgentState.model_validate(agent)
            for agent_id, agent in snapshot["agents"].items()
        },
        forge=ForgeWorldState.model_validate(
            snapshot.get("forge", {})
        ),
    )


def world_state_hash(world: WorldState) -> str:
    return state_hash(serialize_world(world))
