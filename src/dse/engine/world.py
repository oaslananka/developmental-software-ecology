from dataclasses import dataclass, field

from dse.contracts.agent import AgentState, ResourceState, TraitState
from dse.contracts.experiment import ExperimentManifest


@dataclass
class WorldState:
    experiment_id: str
    tick: int = 0
    last_sequence_number: int = 0
    agents: dict[str, AgentState] = field(default_factory=dict)


def create_world(manifest: ExperimentManifest) -> WorldState:
    world = WorldState(experiment_id=manifest.experiment.id)
    traits = manifest.agents.initial_traits
    lifecycle = manifest.agents.lifecycle
    cognition = manifest.agents.cognition
    actions = manifest.agents.actions
    tool_broker = manifest.agents.tool_broker

    for index in range(manifest.world.agent_count):
        agent_id = f"agent-{index + 1:04d}"
        world.agents[agent_id] = AgentState(
            agent_id=agent_id,
            experiment_id=manifest.experiment.id,
            traits=TraitState(
                exploration_bias=traits.exploration_bias,
                persistence=traits.persistence,
                social_bias=traits.social_bias,
                risk_bias=traits.risk_bias,
            ),
            resources=ResourceState(
                activity_units_remaining=lifecycle.active_ticks_per_cycle,
                model_calls_remaining=cognition.model_calls_per_cycle,
                action_proposals_remaining=actions.proposals_per_cycle,
                tool_executions_remaining=tool_broker.executions_per_cycle,
            ),
        )

    return world
