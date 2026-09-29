from dse.contracts.agent import ActionIntentRecord, ActionStatus
from dse.contracts.event import WorldEvent, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.engine.reducer import apply_event
from dse.engine.structured_cognition import advance_structured_cognition_tick
from dse.engine.world import WorldState
from dse.providers.base import ModelProvider
from dse.tools.base import ToolExecutor


async def process_tool_broker(
    world: WorldState,
    manifest: ExperimentManifest,
    executor: ToolExecutor,
) -> list[WorldEvent]:
    if not manifest.agents.tool_broker.enabled:
        return []

    expected_executor = manifest.agents.tool_broker.executor
    if executor.executor_name != expected_executor:
        raise ValueError(
            f"Configured executor {expected_executor} does not match "
            f"runtime executor {executor.executor_name}"
        )

    events: list[WorldEvent] = []

    for agent_id in sorted(world.agents):
        agent = world.agents[agent_id]
        pending = [
            action
            for action in agent.actions.proposals
            if (
                action.status == ActionStatus.PROPOSED
                and not action.kind.startswith("forge_")
            )
        ]

        for action in pending:
            rejection_reason = _policy_rejection_reason(action)
            if rejection_reason is None:
                rejection_reason = executor.policy_rejection_reason(action)

            if rejection_reason is not None:
                events.append(
                    _emit(
                        world,
                        event_type="tool.execution.rejected",
                        actor_agent_id=agent_id,
                        payload={
                            "action_id": action.action_id,
                            "reason": rejection_reason,
                            "executor": executor.executor_name,
                        },
                    )
                )
                continue

            if agent.resources.tool_executions_remaining <= 0:
                events.append(
                    _emit(
                        world,
                        event_type="tool.execution.rejected",
                        actor_agent_id=agent_id,
                        payload={
                            "action_id": action.action_id,
                            "reason": "execution_budget_exhausted",
                            "executor": executor.executor_name,
                        },
                    )
                )
                continue

            events.append(
                _emit(
                    world,
                    event_type="resource.tool_execution.consumed",
                    actor_agent_id=agent_id,
                    payload={"amount": 1},
                )
            )
            events.append(
                _emit(
                    world,
                    event_type="tool.execution.started",
                    actor_agent_id=agent_id,
                    payload={
                        "action_id": action.action_id,
                        "executor": executor.executor_name,
                    },
                )
            )

            result = await executor.execute(action)
            if result.executor != executor.executor_name:
                raise ValueError(
                    "Tool result executor does not match runtime executor"
                )

            execution_id = f"{action.action_id}:execution"
            events.append(
                _emit(
                    world,
                    event_type="tool.execution.completed",
                    actor_agent_id=agent_id,
                    payload={
                        "action_id": action.action_id,
                        "execution": {
                            "execution_id": execution_id,
                            "action_id": action.action_id,
                            "created_tick": world.tick,
                            "executor": result.executor,
                            "success": result.success,
                            "result_type": result.result_type,
                            "summary": result.summary,
                            "result_data": result.result_data,
                            "result_hash": result.result_hash,
                        },
                    },
                )
            )

    return events


async def advance_tool_runtime_tick(
    world: WorldState,
    manifest: ExperimentManifest,
    provider: ModelProvider,
    executor: ToolExecutor,
) -> list[WorldEvent]:
    events = await advance_structured_cognition_tick(
        world,
        manifest,
        provider,
    )
    events.extend(
        await process_tool_broker(
            world,
            manifest,
            executor,
        )
    )
    return events


async def advance_tool_runtime_ticks(
    world: WorldState,
    manifest: ExperimentManifest,
    provider: ModelProvider,
    executor: ToolExecutor,
    count: int,
) -> list[WorldEvent]:
    if count < 0:
        raise ValueError("count must be non-negative")

    events: list[WorldEvent] = []
    for _ in range(count):
        events.extend(
            await advance_tool_runtime_tick(
                world,
                manifest,
                provider,
                executor,
            )
        )
    return events


def execution_context(agent) -> list[dict[str, object]]:
    return [
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


def _policy_rejection_reason(action: ActionIntentRecord) -> str | None:
    normalized = action.target.replace("\\", "/")

    if normalized.startswith("/"):
        return "unsafe_absolute_target"
    if "://" in normalized:
        return "unsafe_network_target"
    if any(part == ".." for part in normalized.split("/")):
        return "unsafe_parent_traversal"

    if action.kind == "inspect_workspace" and normalized != "workspace":
        return "inspect_target_not_allowed"

    return None


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
