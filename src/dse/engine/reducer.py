from dse.contracts.agent import EpisodicMemory, GoalRecord, GoalStatus, LifecycleState
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
            agent.resources.model_calls_remaining = int(event.payload.get("model_calls", 0))
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

        case "resource.model_call.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Model-call consumption must be positive")
            if agent.resources.model_calls_remaining < amount:
                raise ValueError("Model-call budget cannot become negative")
            agent.resources.model_calls_remaining -= amount
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

        case "model.call.completed":
            pass

        case "agent.cognition.decided":
            agent = _agent_for_event(world, event)
            agent.cognition.calls_completed += 1
            agent.cognition.last_cognition_tick = event.world_tick
            agent.cognition.last_decision = str(event.payload["decision"])
            agent.cognition.last_reason_summary = str(event.payload["reason_summary"])
            agent.cognition.last_confidence = float(event.payload["confidence"])
            agent.state_version += 1

        case "agent.goal.created":
            agent = _agent_for_event(world, event)
            goal = GoalRecord.model_validate(event.payload)

            if goal.status != GoalStatus.ACTIVE:
                raise ValueError("New goals must start active")
            if agent.goals.active_goal_id is not None:
                raise ValueError("Agent already has an active goal")
            if any(existing.goal_id == goal.goal_id for existing in agent.goals.goals):
                raise ValueError(f"Duplicate goal ID: {goal.goal_id}")

            agent.goals.goals.append(goal)
            agent.goals.active_goal_id = goal.goal_id
            agent.state_version += 1

        case "agent.goal.proposal_rejected":
            pass

        case "memory.episode.recorded":
            agent = _agent_for_event(world, event)
            memory = EpisodicMemory.model_validate(event.payload)
            if any(
                episode.memory_id == memory.memory_id
                for episode in agent.memory.episodes
            ):
                raise ValueError(f"Duplicate memory ID: {memory.memory_id}")
            agent.memory.episodes.append(memory)
            agent.state_version += 1

        case "memory.episode.evicted":
            agent = _agent_for_event(world, event)
            _remove_memory(agent, str(event.payload["memory_id"]))
            agent.state_version += 1

        case "memory.episode.consolidated":
            agent = _agent_for_event(world, event)
            memory_id = str(event.payload["memory_id"])
            episode = _find_memory(agent, memory_id)

            old_salience = float(event.payload["old_salience"])
            new_salience = float(event.payload["new_salience"])
            if abs(episode.salience - old_salience) > 1e-9:
                raise ValueError(
                    f"Consolidation old_salience mismatch for {memory_id}: "
                    f"state={episode.salience}, event={old_salience}"
                )
            if not 0.0 <= new_salience <= 1.0:
                raise ValueError("Consolidated salience must remain within 0..1")

            episode.salience = new_salience
            agent.state_version += 1

        case "memory.episode.forgotten":
            agent = _agent_for_event(world, event)
            _remove_memory(agent, str(event.payload["memory_id"]))
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


def _find_memory(agent, memory_id: str) -> EpisodicMemory:
    for episode in agent.memory.episodes:
        if episode.memory_id == memory_id:
            return episode
    raise ValueError(f"Unknown memory ID: {memory_id}")


def _remove_memory(agent, memory_id: str) -> None:
    remaining = [
        episode
        for episode in agent.memory.episodes
        if episode.memory_id != memory_id
    ]
    if len(remaining) == len(agent.memory.episodes):
        raise ValueError(f"Unknown memory ID: {memory_id}")
    agent.memory.episodes = remaining
