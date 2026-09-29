from dse.contracts.agent import LifecycleState
from dse.contracts.event import WorldEvent, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.contracts.model import ModelRequest
from dse.engine.cognitionless import advance_cognitionless_tick
from dse.engine.memory import (
    memory_context,
    retrieve_episodes,
    select_eviction_candidate,
)
from dse.engine.reducer import apply_event
from dse.engine.world import WorldState
from dse.providers.base import ModelProvider


async def advance_structured_cognition_tick(
    world: WorldState,
    manifest: ExperimentManifest,
    provider: ModelProvider,
) -> list[WorldEvent]:
    events = advance_cognitionless_tick(world, manifest)

    cognition = manifest.agents.cognition
    if not manifest.runtime.llm_enabled or not cognition.enabled:
        return events

    if world.tick % cognition.interval_ticks != 0:
        return events

    memory_config = manifest.agents.memory
    goal_config = manifest.agents.goals

    for agent_id in sorted(world.agents):
        agent = world.agents[agent_id]

        if agent.lifecycle_state == LifecycleState.SLEEPING:
            continue
        if agent.resources.model_calls_remaining <= 0:
            continue

        context = {
            "lifecycle_state": agent.lifecycle_state.value,
            "activity_units_remaining": agent.resources.activity_units_remaining,
            "model_calls_remaining": agent.resources.model_calls_remaining,
            "cycles_completed": agent.resources.cycles_completed,
        }

        if goal_config.enabled:
            context["goal_generation_enabled"] = True
            context["active_goal"] = _active_goal_context(agent)
        else:
            context["goal_generation_enabled"] = False

        if memory_config.enabled:
            query_text = agent.cognition.last_reason_summary or "environment"
            retrieved = retrieve_episodes(
                agent,
                query_text=query_text,
                current_tick=world.tick,
                limit=memory_config.retrieval_limit,
            )
            context["episodic_memories"] = [
                memory_context(episode)
                for episode in retrieved
            ]

        call_id = (
            f"{world.experiment_id}:{agent_id}:{world.tick}:"
            f"{agent.cognition.calls_completed + 1}"
        )
        request = ModelRequest(
            call_id=call_id,
            experiment_id=world.experiment_id,
            agent_id=agent_id,
            world_tick=world.tick,
            context=context,
            response_schema=(
                "CognitionDecision/v0.2"
                if goal_config.enabled
                else "CognitionDecision/v0.1"
            ),
        )
        response = await provider.generate(request)

        if (
            request.response_schema == "CognitionDecision/v0.1"
            and response.decision.decision == "propose_goal"
        ):
            raise ValueError("v0.1 cognition cannot propose goals")

        events.append(
            _emit(
                world,
                event_type="resource.model_call.consumed",
                actor_agent_id=agent_id,
                payload={"amount": 1},
            )
        )
        events.append(
            _emit(
                world,
                event_type="model.call.completed",
                actor_agent_id=agent_id,
                payload={
                    "call_id": response.call_id,
                    "provider": response.provider,
                    "model": response.model,
                    "model_version": response.model_version,
                    "request_hash": response.request_hash,
                    "response_hash": response.response_hash,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "response_schema": request.response_schema,
                },
            )
        )

        cognition_event = _emit(
            world,
            event_type="agent.cognition.decided",
            actor_agent_id=agent_id,
            payload=response.decision.model_dump(mode="json"),
        )
        events.append(cognition_event)

        if goal_config.enabled and response.decision.decision == "propose_goal":
            proposal = response.decision.goal
            if proposal is None:
                raise ValueError("propose_goal response missing goal proposal")

            if agent.goals.active_goal_id is None:
                events.append(
                    _emit(
                        world,
                        event_type="agent.goal.created",
                        actor_agent_id=agent_id,
                        payload={
                            "goal_id": f"{agent_id}:{cognition_event.event_id}:goal",
                            "created_tick": world.tick,
                            "source_event_id": cognition_event.event_id,
                            "source": "self_generated",
                            "title": proposal.title,
                            "description": proposal.description,
                            "motivation_summary": proposal.motivation_summary,
                            "expected_value": proposal.expected_value,
                            "estimated_cost": proposal.estimated_cost,
                            "confidence": proposal.confidence,
                            "status": "active",
                        },
                    )
                )
            else:
                events.append(
                    _emit(
                        world,
                        event_type="agent.goal.proposal_rejected",
                        actor_agent_id=agent_id,
                        payload={
                            "reason": "active_goal_exists",
                            "active_goal_id": agent.goals.active_goal_id,
                            "proposed_title": proposal.title,
                            "source_event_id": cognition_event.event_id,
                        },
                    )
                )

        if memory_config.enabled:
            memory_event = _emit(
                world,
                event_type="memory.episode.recorded",
                actor_agent_id=agent_id,
                payload={
                    "memory_id": f"{agent_id}:{cognition_event.event_id}",
                    "created_tick": world.tick,
                    "source_event_id": cognition_event.event_id,
                    "content": response.decision.reason_summary,
                    "salience": response.decision.confidence,
                    "decision": response.decision.decision,
                    "focus": response.decision.focus,
                },
            )
            events.append(memory_event)

            while len(agent.memory.episodes) > memory_config.capacity:
                candidate = select_eviction_candidate(agent)
                events.append(
                    _emit(
                        world,
                        event_type="memory.episode.evicted",
                        actor_agent_id=agent_id,
                        payload={"memory_id": candidate.memory_id},
                    )
                )

    return events


async def advance_structured_cognition_ticks(
    world: WorldState,
    manifest: ExperimentManifest,
    provider: ModelProvider,
    count: int,
) -> list[WorldEvent]:
    if count < 0:
        raise ValueError("count must be non-negative")

    events: list[WorldEvent] = []
    for _ in range(count):
        events.extend(
            await advance_structured_cognition_tick(
                world,
                manifest,
                provider,
            )
        )
    return events


def _active_goal_context(agent) -> dict[str, object] | None:
    active_goal_id = agent.goals.active_goal_id
    if active_goal_id is None:
        return None

    for goal in agent.goals.goals:
        if goal.goal_id == active_goal_id:
            return {
                "goal_id": goal.goal_id,
                "created_tick": goal.created_tick,
                "title": goal.title,
                "description": goal.description,
                "motivation_summary": goal.motivation_summary,
                "expected_value": goal.expected_value,
                "estimated_cost": goal.estimated_cost,
                "confidence": goal.confidence,
                "status": goal.status.value,
            }

    raise ValueError(f"Active goal not found: {active_goal_id}")


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
