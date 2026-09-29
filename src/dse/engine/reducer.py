from dse.contracts.agent import (
    ActionIntentRecord,
    ActionState,
    ActionStatus,
    CognitionState,
    CultureActionResultRecord,
    EpisodicMemory,
    ForgeActionResultRecord,
    GoalRecord,
    GoalState,
    GoalStatus,
    LifecycleState,
    MemoryState,
    ResourceState,
    ToolExecutionRecord,
)
from dse.contracts.culture import (
    SocialMessageRecord,
    SocialThreadRecord,
    TextEntryRecord,
)
from dse.contracts.event import WorldEvent
from dse.contracts.forge import (
    ForgeArtifactRecord,
    ForgeCommitRecord,
    ForgeRepositoryRecord,
)
from dse.engine.hashing import state_hash
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

        case "agent.lifecycle.turned_over":
            agent = _agent_for_event(world, event)
            previous_generation = int(event.payload["previous_generation"])
            next_generation = int(event.payload["next_generation"])
            if agent.generation != previous_generation:
                raise ValueError("Turnover previous_generation mismatch")
            if next_generation != previous_generation + 1:
                raise ValueError("Turnover must increment generation exactly once")
            if agent.lifecycle_state in {
                LifecycleState.TURNED_OVER,
                LifecycleState.TERMINATED,
            }:
                raise ValueError("Agent cannot be turned over from terminal state")

            expected_counts = {
                "cognition_calls": agent.cognition.calls_completed,
                "goals": len(agent.goals.goals),
                "actions": len(agent.actions.proposals),
                "tool_executions": len(agent.actions.executions),
                "forge_results": len(agent.actions.forge_results),
                "culture_results": len(agent.actions.culture_results),
                "episodic_memories": len(agent.memory.episodes),
            }
            if event.payload["private_state_counts"] != expected_counts:
                raise ValueError("Turnover private-state counts mismatch")

            forge_hash = state_hash(world.forge.model_dump(mode="json"))
            if event.payload["public_forge_hash"] != forge_hash:
                raise ValueError("Turnover public Forge hash mismatch")
            text_hash = state_hash(world.text_culture.model_dump(mode="json"))
            if event.payload["text_culture_hash"] != text_hash:
                raise ValueError("Turnover text culture hash mismatch")
            social_hash = state_hash(world.social.model_dump(mode="json"))
            if event.payload["social_world_hash"] != social_hash:
                raise ValueError("Turnover social world hash mismatch")

            agent.lifecycle_state = LifecycleState.TURNED_OVER
            agent.state_version += 1

        case "agent.lifecycle.replaced":
            agent = _agent_for_event(world, event)
            previous_generation = int(event.payload["previous_generation"])
            new_generation = int(event.payload["new_generation"])
            birth_tick = int(event.payload["birth_tick"])

            if agent.lifecycle_state != LifecycleState.TURNED_OVER:
                raise ValueError("Replacement requires turned_over lifecycle state")
            if agent.generation != previous_generation:
                raise ValueError("Replacement previous_generation mismatch")
            if new_generation != previous_generation + 1:
                raise ValueError("Replacement generation must increment exactly once")
            if birth_tick != event.world_tick:
                raise ValueError("Replacement birth_tick must equal event world_tick")
            if event.payload.get("traits_preserved") is not True:
                raise ValueError("M13 replacements must preserve initial traits")

            forge_hash = state_hash(world.forge.model_dump(mode="json"))
            if event.payload["public_forge_hash"] != forge_hash:
                raise ValueError("Replacement public Forge hash mismatch")
            text_hash = state_hash(world.text_culture.model_dump(mode="json"))
            if event.payload["text_culture_hash"] != text_hash:
                raise ValueError("Replacement text culture hash mismatch")
            social_hash = state_hash(world.social.model_dump(mode="json"))
            if event.payload["social_world_hash"] != social_hash:
                raise ValueError("Replacement social world hash mismatch")

            agent.generation = new_generation
            agent.birth_tick = birth_tick
            agent.lifecycle_state = LifecycleState.BORN
            agent.resources = ResourceState(activity_units_remaining=0)
            agent.cognition = CognitionState()
            agent.goals = GoalState()
            agent.actions = ActionState()
            agent.memory = MemoryState()
            agent.last_active_tick = birth_tick
            agent.state_version = 0

        case "agent.lifecycle.wake":
            agent = _agent_for_event(world, event)
            agent.lifecycle_state = LifecycleState.AWAKE
            agent.resources.activity_units_remaining = int(event.payload["activity_units"])
            agent.resources.sleep_ticks_remaining = 0
            agent.resources.model_calls_remaining = int(event.payload.get("model_calls", 0))
            agent.resources.action_proposals_remaining = int(
                event.payload.get("action_proposals", 0)
            )
            agent.resources.tool_executions_remaining = int(
                event.payload.get("tool_executions", 0)
            )
            agent.resources.forge_operations_remaining = int(
                event.payload.get("forge_operations", 0)
            )
            agent.resources.text_operations_remaining = int(
                event.payload.get("text_operations", 0)
            )
            agent.resources.social_operations_remaining = int(
                event.payload.get("social_operations", 0)
            )
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

        case "resource.action_proposal.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Action-proposal consumption must be positive")
            if agent.resources.action_proposals_remaining < amount:
                raise ValueError("Action-proposal budget cannot become negative")
            agent.resources.action_proposals_remaining -= amount
            agent.state_version += 1

        case "resource.tool_execution.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Tool-execution consumption must be positive")
            if agent.resources.tool_executions_remaining < amount:
                raise ValueError("Tool-execution budget cannot become negative")
            agent.resources.tool_executions_remaining -= amount
            agent.state_version += 1

        case "resource.forge_operation.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Forge-operation consumption must be positive")
            if agent.resources.forge_operations_remaining < amount:
                raise ValueError("Forge-operation budget cannot become negative")
            agent.resources.forge_operations_remaining -= amount
            agent.state_version += 1

        case "resource.text_operation.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Text-operation consumption must be positive")
            if agent.resources.text_operations_remaining < amount:
                raise ValueError("Text-operation budget cannot become negative")
            agent.resources.text_operations_remaining -= amount
            agent.state_version += 1

        case "resource.social_operation.consumed":
            agent = _agent_for_event(world, event)
            amount = int(event.payload.get("amount", 1))
            if amount <= 0:
                raise ValueError("Social-operation consumption must be positive")
            if agent.resources.social_operations_remaining < amount:
                raise ValueError("Social-operation budget cannot become negative")
            agent.resources.social_operations_remaining -= amount
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

        case "agent.goal.progressed":
            agent = _agent_for_event(world, event)
            goal = _active_goal(agent, str(event.payload["goal_id"]))

            old_progress = float(event.payload["old_progress"])
            new_progress = float(event.payload["new_progress"])
            if abs(goal.progress - old_progress) > 1e-9:
                raise ValueError(
                    f"Goal progress mismatch for {goal.goal_id}: "
                    f"state={goal.progress}, event={old_progress}"
                )
            if not old_progress < new_progress < 1.0:
                raise ValueError(
                    "Progress event must increase progress and remain below completion"
                )

            goal.progress = new_progress
            goal.last_progress_tick = event.world_tick
            goal.last_progress_summary = str(event.payload["progress_summary"])
            agent.state_version += 1

        case "agent.goal.completed":
            agent = _agent_for_event(world, event)
            goal = _active_goal(agent, str(event.payload["goal_id"]))

            goal.progress = 1.0
            goal.last_progress_tick = event.world_tick
            goal.last_progress_summary = str(event.payload["summary"])
            goal.completion_tick = event.world_tick
            goal.completion_summary = str(event.payload["summary"])
            goal.status = GoalStatus.COMPLETED
            agent.goals.active_goal_id = None
            agent.state_version += 1

        case "agent.goal.abandoned":
            agent = _agent_for_event(world, event)
            goal = _active_goal(agent, str(event.payload["goal_id"]))

            goal.abandonment_tick = event.world_tick
            goal.abandonment_summary = str(event.payload["summary"])
            goal.status = GoalStatus.ABANDONED
            agent.goals.active_goal_id = None
            agent.state_version += 1

        case "agent.goal.proposal_rejected":
            pass

        case "agent.action.proposed":
            agent = _agent_for_event(world, event)
            action = ActionIntentRecord.model_validate(event.payload)

            if action.status != ActionStatus.PROPOSED:
                raise ValueError("New action intents must start proposed")
            if agent.goals.active_goal_id != action.goal_id:
                raise ValueError(
                    "Action intent must reference the currently active goal"
                )
            if any(
                existing.action_id == action.action_id
                for existing in agent.actions.proposals
            ):
                raise ValueError(f"Duplicate action ID: {action.action_id}")

            agent.actions.proposals.append(action)
            agent.state_version += 1

        case "agent.action.rejected":
            pass

        case "tool.execution.started":
            pass

        case "tool.execution.completed":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            execution = ToolExecutionRecord.model_validate(event.payload["execution"])

            if execution.action_id != action.action_id:
                raise ValueError("Execution action_id does not match proposal")
            if any(
                existing.execution_id == execution.execution_id
                for existing in agent.actions.executions
            ):
                raise ValueError(
                    f"Duplicate execution ID: {execution.execution_id}"
                )

            action.status = ActionStatus.EXECUTED
            agent.actions.executions.append(execution)
            agent.state_version += 1

        case "tool.execution.rejected":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            action.status = ActionStatus.REJECTED
            agent.state_version += 1

        case "sandbox.admission.evaluated":
            pass

        case "sandbox.execution.dispatched":
            pass

        case "sandbox.execution.completed":
            pass

        case "sandbox.execution.result_rejected":
            pass

        case "forge.repository.created":
            agent = _agent_for_event(world, event)
            repository = ForgeRepositoryRecord.model_validate(
                event.payload["repository"]
            )

            if repository.creator_agent_id != agent.agent_id:
                raise ValueError("Forge repository creator does not match actor")
            if repository.creator_generation != agent.generation:
                raise ValueError("Forge repository creator generation mismatch")
            if repository.created_tick != event.world_tick:
                raise ValueError("Forge repository created_tick mismatch")
            if repository.head_commit_id is not None:
                raise ValueError("New forge repository must have no head commit")
            if repository.commit_count != 0 or repository.artifact_count != 0:
                raise ValueError("New forge repository counters must start at zero")
            if repository.path_heads:
                raise ValueError("New forge repository must have no path heads")
            if repository.repo_id in world.forge.repositories:
                raise ValueError(f"Duplicate forge repository ID: {repository.repo_id}")
            if any(
                existing.name == repository.name
                for existing in world.forge.repositories.values()
            ):
                raise ValueError(f"Duplicate forge repository name: {repository.name}")

            world.forge.repositories[repository.repo_id] = repository

        case "forge.artifact.committed":
            agent = _agent_for_event(world, event)
            commit = ForgeCommitRecord.model_validate(event.payload["commit"])
            artifact = ForgeArtifactRecord.model_validate(event.payload["artifact"])

            try:
                repository = world.forge.repositories[commit.repo_id]
            except KeyError as error:
                raise ValueError(
                    f"Unknown forge repository: {commit.repo_id}"
                ) from error

            if artifact.repo_id != repository.repo_id:
                raise ValueError("Artifact repository does not match commit repository")
            if artifact.commit_id != commit.commit_id:
                raise ValueError("Artifact commit_id does not match commit")
            if commit.artifact_ids != [artifact.artifact_id]:
                raise ValueError("M11 commits must contain exactly the committed artifact")
            if commit.parent_commit_id != repository.head_commit_id:
                raise ValueError(
                    "Forge commit parent does not match current repository head"
                )
            if commit.commit_id in world.forge.commits:
                raise ValueError(f"Duplicate forge commit ID: {commit.commit_id}")
            if artifact.artifact_id in world.forge.artifacts:
                raise ValueError(f"Duplicate forge artifact ID: {artifact.artifact_id}")

            if commit.author_agent_id != agent.agent_id:
                raise ValueError("Forge commit author does not match actor")
            if artifact.creator_agent_id != agent.agent_id:
                raise ValueError("Forge artifact creator does not match actor")
            if commit.author_generation != agent.generation:
                raise ValueError("Forge commit author generation mismatch")
            if artifact.creator_generation != agent.generation:
                raise ValueError("Forge artifact creator generation mismatch")
            if commit.created_tick != event.world_tick:
                raise ValueError("Forge commit created_tick mismatch")
            if artifact.created_tick != event.world_tick:
                raise ValueError("Forge artifact created_tick mismatch")
            if artifact.source_event_id != event.event_id:
                raise ValueError("Forge artifact source_event_id mismatch")

            encoded = artifact.content.encode("utf-8")
            if artifact.content_bytes != len(encoded):
                raise ValueError("Forge artifact content byte count mismatch")
            if artifact.content_sha256 != state_hash_bytes(encoded):
                raise ValueError("Forge artifact content hash mismatch")

            previous = repository.path_heads.get(artifact.path)
            if artifact.previous_artifact_id != previous:
                raise ValueError(
                    "Forge artifact previous revision does not match path head"
                )
            if previous is not None and previous not in artifact.parent_artifact_ids:
                raise ValueError(
                    "Forge revision lineage must include the previous artifact"
                )

            if len(artifact.parent_artifact_ids) != len(
                set(artifact.parent_artifact_ids)
            ):
                raise ValueError("Forge artifact lineage contains duplicate parents")
            for parent_artifact_id in artifact.parent_artifact_ids:
                if parent_artifact_id not in world.forge.artifacts:
                    raise ValueError(
                        f"Unknown parent forge artifact: {parent_artifact_id}"
                    )

            world.forge.commits[commit.commit_id] = commit
            world.forge.artifacts[artifact.artifact_id] = artifact
            repository.head_commit_id = commit.commit_id
            repository.commit_count += 1
            repository.artifact_count += 1
            repository.path_heads[artifact.path] = artifact.artifact_id

        case "forge.operation.rejected":
            _agent_for_event(world, event)

        case "forge.action.completed":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            result = ForgeActionResultRecord.model_validate(
                event.payload["result"]
            )
            if not action.kind.startswith("forge_"):
                raise ValueError("Forge action result requires forge_* action")
            if result.action_id != action.action_id:
                raise ValueError("Forge result action_id mismatch")
            if result.status != "completed":
                raise ValueError("Completed forge action requires completed result")
            if any(
                existing.result_id == result.result_id
                for existing in agent.actions.forge_results
            ):
                raise ValueError(f"Duplicate forge result ID: {result.result_id}")

            action.status = ActionStatus.EXECUTED
            agent.actions.forge_results.append(result)
            agent.state_version += 1

        case "forge.action.rejected":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            result = ForgeActionResultRecord.model_validate(
                event.payload["result"]
            )
            if not action.kind.startswith("forge_"):
                raise ValueError("Forge action rejection requires forge_* action")
            if result.action_id != action.action_id:
                raise ValueError("Forge rejection action_id mismatch")
            if result.status != "rejected":
                raise ValueError("Rejected forge action requires rejected result")
            if any(
                existing.result_id == result.result_id
                for existing in agent.actions.forge_results
            ):
                raise ValueError(f"Duplicate forge result ID: {result.result_id}")

            action.status = ActionStatus.REJECTED
            agent.actions.forge_results.append(result)
            agent.state_version += 1

        case "culture.text.published":
            agent = _agent_for_event(world, event)
            entry = TextEntryRecord.model_validate(event.payload["entry"])
            action = _proposed_action(agent, entry.source_action_id)

            if action.kind != "text_publish":
                raise ValueError("Text entry requires text_publish action")
            if entry.creator_agent_id != agent.agent_id:
                raise ValueError("Text creator does not match actor")
            if entry.creator_generation != agent.generation:
                raise ValueError("Text creator generation mismatch")
            if entry.created_tick != event.world_tick:
                raise ValueError("Text entry created_tick mismatch")
            if entry.source_event_id != event.event_id:
                raise ValueError("Text entry source_event_id mismatch")
            if entry.entry_id in world.text_culture.entries:
                raise ValueError(f"Duplicate text entry ID: {entry.entry_id}")

            encoded = entry.content.encode("utf-8")
            if entry.content_bytes != len(encoded):
                raise ValueError("Text entry byte count mismatch")
            if entry.content_sha256 != state_hash_bytes(encoded):
                raise ValueError("Text entry content hash mismatch")
            if len(entry.parent_entry_ids) != len(set(entry.parent_entry_ids)):
                raise ValueError("Text lineage contains duplicate parents")
            for parent_id in entry.parent_entry_ids:
                if parent_id not in world.text_culture.entries:
                    raise ValueError(f"Unknown parent text entry: {parent_id}")

            world.text_culture.entries[entry.entry_id] = entry
            world.text_culture.order.append(entry.entry_id)

        case "social.message.sent":
            agent = _agent_for_event(world, event)
            message = SocialMessageRecord.model_validate(event.payload["message"])
            action = _proposed_action(agent, message.source_action_id)

            if action.kind != "social_send_message":
                raise ValueError("Direct message requires social_send_message action")
            _validate_social_message_actor(agent, event, message)
            if message.channel != "direct" or message.visibility != "direct":
                raise ValueError("social.message.sent must be a direct message")
            if message.recipient_agent_id is None:
                raise ValueError("Direct message requires recipient")
            try:
                recipient = world.agents[message.recipient_agent_id]
            except KeyError as error:
                raise ValueError("Direct message recipient is unknown") from error
            if message.recipient_generation != recipient.generation:
                raise ValueError("Direct message recipient generation mismatch")
            _validate_social_message_content(message)
            _append_social_message(world, message)

        case "social.thread.opened":
            agent = _agent_for_event(world, event)
            thread = SocialThreadRecord.model_validate(event.payload["thread"])
            message = SocialMessageRecord.model_validate(event.payload["message"])
            action = _proposed_action(agent, message.source_action_id)

            expected_kind = (
                "issue"
                if action.kind == "social_open_issue"
                else "pull_request"
                if action.kind == "social_open_pr"
                else None
            )
            if expected_kind is None or thread.kind != expected_kind:
                raise ValueError("Thread kind does not match social action")
            _validate_social_message_actor(agent, event, message)
            if message.visibility != "public" or message.channel != thread.kind:
                raise ValueError("Opening social message must be public thread content")
            if message.thread_id != thread.thread_id:
                raise ValueError("Opening message thread_id mismatch")
            if thread.creator_agent_id != agent.agent_id:
                raise ValueError("Social thread creator does not match actor")
            if thread.creator_generation != agent.generation:
                raise ValueError("Social thread creator generation mismatch")
            if thread.created_tick != event.world_tick:
                raise ValueError("Social thread created_tick mismatch")
            if thread.opening_message_id != message.message_id:
                raise ValueError("Social thread opening message mismatch")
            if thread.message_ids != [message.message_id]:
                raise ValueError("New social thread must contain opening message only")
            if thread.thread_id in world.social.threads:
                raise ValueError(f"Duplicate social thread ID: {thread.thread_id}")
            _validate_social_message_content(message)
            _append_social_message(world, message)
            world.social.threads[thread.thread_id] = thread
            world.social.thread_order.append(thread.thread_id)

        case "social.thread.message_posted":
            agent = _agent_for_event(world, event)
            message = SocialMessageRecord.model_validate(event.payload["message"])
            action = _proposed_action(agent, message.source_action_id)

            if action.kind != "social_post_message":
                raise ValueError("Thread message requires social_post_message action")
            _validate_social_message_actor(agent, event, message)
            if message.visibility != "public" or message.channel != "thread_message":
                raise ValueError("Thread message must be public")
            if message.thread_id is None:
                raise ValueError("Thread message requires thread_id")
            try:
                thread = world.social.threads[message.thread_id]
            except KeyError as error:
                raise ValueError("Thread message references unknown thread") from error
            _validate_social_message_content(message)
            _append_social_message(world, message)
            thread.message_ids.append(message.message_id)

        case "culture.operation.rejected":
            _agent_for_event(world, event)

        case "culture.action.completed":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            result = CultureActionResultRecord.model_validate(event.payload["result"])
            if not (
                action.kind == "text_publish"
                or action.kind.startswith("social_")
            ):
                raise ValueError("Culture result requires text/social action")
            if result.action_id != action.action_id:
                raise ValueError("Culture result action_id mismatch")
            if result.status != "completed":
                raise ValueError("Completed culture action requires completed result")
            if any(
                existing.result_id == result.result_id
                for existing in agent.actions.culture_results
            ):
                raise ValueError(f"Duplicate culture result ID: {result.result_id}")

            action.status = ActionStatus.EXECUTED
            agent.actions.culture_results.append(result)
            agent.state_version += 1

        case "culture.action.rejected":
            agent = _agent_for_event(world, event)
            action = _proposed_action(agent, str(event.payload["action_id"]))
            result = CultureActionResultRecord.model_validate(event.payload["result"])
            if not (
                action.kind == "text_publish"
                or action.kind.startswith("social_")
            ):
                raise ValueError("Culture rejection requires text/social action")
            if result.action_id != action.action_id:
                raise ValueError("Culture rejection action_id mismatch")
            if result.status != "rejected":
                raise ValueError("Rejected culture action requires rejected result")
            if any(
                existing.result_id == result.result_id
                for existing in agent.actions.culture_results
            ):
                raise ValueError(f"Duplicate culture result ID: {result.result_id}")

            action.status = ActionStatus.REJECTED
            agent.actions.culture_results.append(result)
            agent.state_version += 1

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


def _active_goal(agent, goal_id: str) -> GoalRecord:
    if agent.goals.active_goal_id != goal_id:
        raise ValueError(
            f"Goal {goal_id} is not active; active={agent.goals.active_goal_id}"
        )

    for goal in agent.goals.goals:
        if goal.goal_id == goal_id:
            if goal.status != GoalStatus.ACTIVE:
                raise ValueError(f"Goal {goal_id} is not in active status")
            return goal

    raise ValueError(f"Unknown goal ID: {goal_id}")


def _proposed_action(agent, action_id: str) -> ActionIntentRecord:
    for action in agent.actions.proposals:
        if action.action_id == action_id:
            if action.status != ActionStatus.PROPOSED:
                raise ValueError(
                    f"Action {action_id} is not proposed: {action.status}"
                )
            return action
    raise ValueError(f"Unknown action ID: {action_id}")


def _validate_social_message_actor(agent, event, message) -> None:
    if message.sender_agent_id != agent.agent_id:
        raise ValueError("Social sender does not match actor")
    if message.sender_generation != agent.generation:
        raise ValueError("Social sender generation mismatch")
    if message.created_tick != event.world_tick:
        raise ValueError("Social message created_tick mismatch")
    if message.source_event_id != event.event_id:
        raise ValueError("Social message source_event_id mismatch")


def _validate_social_message_content(message) -> None:
    encoded = message.content.encode("utf-8")
    if message.content_bytes != len(encoded):
        raise ValueError("Social message byte count mismatch")
    if message.content_sha256 != state_hash_bytes(encoded):
        raise ValueError("Social message content hash mismatch")


def _append_social_message(world: WorldState, message) -> None:
    if message.message_id in world.social.messages:
        raise ValueError(f"Duplicate social message ID: {message.message_id}")
    world.social.messages[message.message_id] = message
    world.social.message_order.append(message.message_id)


def state_hash_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


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
