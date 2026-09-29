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
    action_config = manifest.agents.actions

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
            context["goal_lifecycle_enabled"] = goal_config.lifecycle_enabled
            context["active_goal"] = _active_goal_context(agent)
        else:
            context["goal_generation_enabled"] = False
            context["goal_lifecycle_enabled"] = False

        context["action_generation_enabled"] = action_config.enabled
        context["action_proposals_remaining"] = (
            agent.resources.action_proposals_remaining
        )
        context["tool_broker_enabled"] = manifest.agents.tool_broker.enabled
        context["tool_executions_remaining"] = (
            agent.resources.tool_executions_remaining
        )

        if manifest.agents.tool_broker.enabled:
            context["tool_results"] = [
                {
                    "execution_id": execution.execution_id,
                    "action_id": execution.action_id,
                    "created_tick": execution.created_tick,
                    "executor": execution.executor,
                    "success": execution.success,
                    "result_type": execution.result_type,
                    "summary": execution.summary,
                    "result_data": execution.result_data,
                    "result_hash": execution.result_hash,
                }
                for execution in agent.actions.executions[-4:]
            ]

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
            response_schema=_response_schema(manifest),
        )
        response = await provider.generate(request)

        _validate_schema_decision(request.response_schema, response.decision.decision)

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

        if goal_config.enabled:
            events.extend(
                _emit_goal_events(
                    world,
                    agent_id=agent_id,
                    cognition_event=cognition_event,
                    decision=response.decision,
                )
            )

        if action_config.enabled and response.decision.decision == "propose_action":
            events.extend(
                _emit_action_events(
                    world,
                    agent_id=agent_id,
                    cognition_event=cognition_event,
                    decision=response.decision,
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


def _response_schema(manifest: ExperimentManifest) -> str:
    if manifest.agents.actions.enabled:
        return "CognitionDecision/v0.4"

    goals = manifest.agents.goals
    if not goals.enabled:
        return "CognitionDecision/v0.1"
    if goals.lifecycle_enabled:
        return "CognitionDecision/v0.3"
    return "CognitionDecision/v0.2"


def _validate_schema_decision(schema: str, decision: str) -> None:
    if schema == "CognitionDecision/v0.1" and decision in {
        "propose_goal",
        "update_goal",
        "complete_goal",
        "abandon_goal",
        "propose_action",
    }:
        raise ValueError("v0.1 cognition cannot emit goal/action decisions")

    if schema == "CognitionDecision/v0.2" and decision in {
        "update_goal",
        "complete_goal",
        "abandon_goal",
        "propose_action",
    }:
        raise ValueError("v0.2 cognition cannot emit lifecycle/action decisions")

    if schema == "CognitionDecision/v0.3" and decision == "propose_action":
        raise ValueError("v0.3 cognition cannot emit action proposals")


def _emit_goal_events(
    world: WorldState,
    *,
    agent_id: str,
    cognition_event: WorldEvent,
    decision,
) -> list[WorldEvent]:
    agent = world.agents[agent_id]
    events: list[WorldEvent] = []

    if decision.decision == "propose_goal":
        proposal = decision.goal
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
                        "progress": 0.0,
                        "last_progress_tick": None,
                        "last_progress_summary": None,
                        "completion_tick": None,
                        "completion_summary": None,
                        "abandonment_tick": None,
                        "abandonment_summary": None,
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

    elif decision.decision == "update_goal":
        update = decision.goal_update
        if update is None:
            raise ValueError("update_goal response missing goal_update")
        active_goal = _active_goal(agent)
        events.append(
            _emit(
                world,
                event_type="agent.goal.progressed",
                actor_agent_id=agent_id,
                payload={
                    "goal_id": active_goal.goal_id,
                    "old_progress": active_goal.progress,
                    "new_progress": update.progress,
                    "progress_summary": update.progress_summary,
                    "confidence": update.confidence,
                    "source_event_id": cognition_event.event_id,
                },
            )
        )

    elif decision.decision == "complete_goal":
        closure = decision.goal_closure
        if closure is None:
            raise ValueError("complete_goal response missing goal_closure")
        active_goal = _active_goal(agent)
        events.append(
            _emit(
                world,
                event_type="agent.goal.completed",
                actor_agent_id=agent_id,
                payload={
                    "goal_id": active_goal.goal_id,
                    "summary": closure.summary,
                    "confidence": closure.confidence,
                    "source_event_id": cognition_event.event_id,
                },
            )
        )

    elif decision.decision == "abandon_goal":
        closure = decision.goal_closure
        if closure is None:
            raise ValueError("abandon_goal response missing goal_closure")
        active_goal = _active_goal(agent)
        events.append(
            _emit(
                world,
                event_type="agent.goal.abandoned",
                actor_agent_id=agent_id,
                payload={
                    "goal_id": active_goal.goal_id,
                    "summary": closure.summary,
                    "confidence": closure.confidence,
                    "source_event_id": cognition_event.event_id,
                },
            )
        )

    return events


def _emit_action_events(
    world: WorldState,
    *,
    agent_id: str,
    cognition_event: WorldEvent,
    decision,
) -> list[WorldEvent]:
    agent = world.agents[agent_id]
    proposal = decision.action
    if proposal is None:
        raise ValueError("propose_action response missing action proposal")

    active_goal = _active_goal_context(agent)
    if active_goal is None:
        return [
            _emit(
                world,
                event_type="agent.action.rejected",
                actor_agent_id=agent_id,
                payload={
                    "reason": "no_active_goal",
                    "source_event_id": cognition_event.event_id,
                    "kind": proposal.kind,
                    "target": proposal.target,
                },
            )
        ]

    if agent.resources.action_proposals_remaining <= 0:
        return [
            _emit(
                world,
                event_type="agent.action.rejected",
                actor_agent_id=agent_id,
                payload={
                    "reason": "budget_exhausted",
                    "goal_id": active_goal["goal_id"],
                    "source_event_id": cognition_event.event_id,
                    "kind": proposal.kind,
                    "target": proposal.target,
                },
            )
        ]

    events = [
        _emit(
            world,
            event_type="resource.action_proposal.consumed",
            actor_agent_id=agent_id,
            payload={"amount": 1},
        )
    ]
    events.append(
        _emit(
            world,
            event_type="agent.action.proposed",
            actor_agent_id=agent_id,
            payload={
                "action_id": f"{agent_id}:{cognition_event.event_id}:action",
                "created_tick": world.tick,
                "source_event_id": cognition_event.event_id,
                "goal_id": str(active_goal["goal_id"]),
                "kind": proposal.kind,
                "summary": proposal.summary,
                "target": proposal.target,
                "rationale": proposal.rationale,
                "expected_value": proposal.expected_value,
                "estimated_cost": proposal.estimated_cost,
                "draft_content": proposal.draft_content,
                "status": "proposed",
            },
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
                "progress": goal.progress,
                "last_progress_tick": goal.last_progress_tick,
                "last_progress_summary": goal.last_progress_summary,
                "status": goal.status.value,
            }

    raise ValueError(f"Active goal not found: {active_goal_id}")


def _active_goal(agent):
    active_goal_id = agent.goals.active_goal_id
    if active_goal_id is None:
        raise ValueError("Goal lifecycle decision requires an active goal")

    for goal in agent.goals.goals:
        if goal.goal_id == active_goal_id:
            return goal

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
