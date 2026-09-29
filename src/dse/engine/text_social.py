import hashlib

from dse.contracts.agent import ActionStatus, CultureActionResultRecord
from dse.contracts.culture import (
    SocialMessageRecord,
    SocialThreadRecord,
    TextEntryRecord,
)
from dse.contracts.event import WorldEvent, deterministic_event_id, make_event
from dse.contracts.experiment import ExperimentManifest
from dse.engine.hashing import state_hash
from dse.engine.reducer import apply_event
from dse.engine.world import WorldState


TEXT_SOCIAL_ACTION_KINDS = {
    "text_publish",
    "social_send_message",
    "social_open_issue",
    "social_open_pr",
    "social_post_message",
}


async def process_text_social_actions(
    world: WorldState,
    manifest: ExperimentManifest,
) -> list[WorldEvent]:
    events: list[WorldEvent] = []

    for agent_id in sorted(world.agents):
        agent = world.agents[agent_id]
        pending = [
            action
            for action in agent.actions.proposals
            if (
                action.status == ActionStatus.PROPOSED
                and action.kind in TEXT_SOCIAL_ACTION_KINDS
            )
        ]

        for action in pending:
            if action.kind == "text_publish":
                events.extend(
                    _process_text_publish(
                        world,
                        manifest,
                        agent_id=agent_id,
                        action=action,
                    )
                )
            else:
                events.extend(
                    _process_social_action(
                        world,
                        manifest,
                        agent_id=agent_id,
                        action=action,
                    )
                )

    return events


def visible_text_entries(
    world: WorldState,
    manifest: ExperimentManifest,
) -> list[dict]:
    config = manifest.runtime.text_culture
    if not config.enabled:
        return []

    entry_ids = world.text_culture.order[-config.context_entry_limit :]
    return [
        {
            "entry_id": entry.entry_id,
            "created_tick": entry.created_tick,
            "creator_agent_id": entry.creator_agent_id,
            "creator_generation": entry.creator_generation,
            "title": entry.title,
            "content": entry.content[: config.context_content_chars],
            "content_truncated": (
                len(entry.content) > config.context_content_chars
            ),
            "content_sha256": entry.content_sha256,
            "parent_entry_ids": list(entry.parent_entry_ids),
        }
        for entry in (
            world.text_culture.entries[entry_id]
            for entry_id in entry_ids
        )
    ]


def visible_social_messages(
    world: WorldState,
    manifest: ExperimentManifest,
    *,
    agent_id: str,
) -> list[dict]:
    config = manifest.runtime.social
    if not config.enabled:
        return []

    agent = world.agents[agent_id]
    visible = []
    for message_id in world.social.message_order:
        message = world.social.messages[message_id]
        if config.mode == "direct":
            is_visible = (
                message.visibility == "direct"
                and (
                    (
                        message.recipient_agent_id == agent.agent_id
                        and message.recipient_generation == agent.generation
                    )
                    or (
                        message.sender_agent_id == agent.agent_id
                        and message.sender_generation == agent.generation
                    )
                )
            )
        else:
            is_visible = message.visibility == "public"

        if is_visible:
            visible.append(message)

    selected = visible[-config.context_message_limit :]
    return [
        {
            "message_id": message.message_id,
            "created_tick": message.created_tick,
            "sender_agent_id": message.sender_agent_id,
            "sender_generation": message.sender_generation,
            "channel": message.channel,
            "visibility": message.visibility,
            "recipient_agent_id": message.recipient_agent_id,
            "recipient_generation": message.recipient_generation,
            "thread_id": message.thread_id,
            "subject": message.subject,
            "content": message.content[: config.context_content_chars],
            "content_truncated": (
                len(message.content) > config.context_content_chars
            ),
            "content_sha256": message.content_sha256,
        }
        for message in selected
    ]


def visible_social_threads(
    world: WorldState,
    manifest: ExperimentManifest,
) -> list[dict]:
    config = manifest.runtime.social
    if not config.enabled or config.mode != "issues_pr_messages":
        return []

    thread_ids = world.social.thread_order[-config.context_message_limit :]
    return [
        {
            "thread_id": thread.thread_id,
            "kind": thread.kind,
            "created_tick": thread.created_tick,
            "creator_agent_id": thread.creator_agent_id,
            "creator_generation": thread.creator_generation,
            "title": thread.title,
            "message_ids": list(thread.message_ids),
        }
        for thread in (
            world.social.threads[thread_id]
            for thread_id in thread_ids
        )
    ]


def _process_text_publish(
    world: WorldState,
    manifest: ExperimentManifest,
    *,
    agent_id: str,
    action,
) -> list[WorldEvent]:
    config = manifest.runtime.text_culture
    agent = world.agents[agent_id]

    if not config.enabled:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation="publish_text",
            reason="text_culture_disabled",
        )
    if agent.resources.text_operations_remaining <= 0:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation="publish_text",
            reason="text_operation_budget_exhausted",
        )
    if len(world.text_culture.entries) >= config.max_entries:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation="publish_text",
            reason="text_entry_limit_reached",
        )
    if action.draft_content is None:
        raise ValueError("Validated text_publish action lacks draft_content")

    encoded = action.draft_content.encode("utf-8")
    if len(encoded) > config.max_entry_bytes:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation="publish_text",
            reason="text_entry_too_large",
        )

    for parent_id in action.parent_text_entry_ids:
        if parent_id not in world.text_culture.entries:
            return _reject_action(
                world,
                agent_id=agent_id,
                action_id=action.action_id,
                operation="publish_text",
                reason="parent_text_entry_not_found",
            )

    events = [
        _emit(
            world,
            event_type="resource.text_operation.consumed",
            actor_agent_id=agent_id,
            payload={"amount": 1},
        )
    ]

    sequence = world.last_sequence_number + 1
    event_id = deterministic_event_id(world.experiment_id, sequence)
    digest = state_hash(
        {
            "kind": "text-entry",
            "experiment_id": world.experiment_id,
            "action_id": action.action_id,
            "content_sha256": hashlib.sha256(encoded).hexdigest(),
        }
    )
    entry = TextEntryRecord(
        entry_id=f"text-{digest[:40]}",
        created_tick=world.tick,
        creator_agent_id=agent_id,
        creator_generation=agent.generation,
        source_event_id=event_id,
        source_action_id=action.action_id,
        title=action.target,
        content=action.draft_content,
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        content_bytes=len(encoded),
        parent_entry_ids=list(action.parent_text_entry_ids),
    )
    events.append(
        _emit(
            world,
            event_type="culture.text.published",
            actor_agent_id=agent_id,
            payload={"entry": entry.model_dump(mode="json")},
        )
    )
    events.append(
        _terminal_event(
            world,
            actor_agent_id=agent_id,
            action_id=action.action_id,
            operation="publish_text",
            status="completed",
            reason="text_published",
            result_data={
                "entry_id": entry.entry_id,
                "parent_entry_ids": list(entry.parent_entry_ids),
                "content_sha256": entry.content_sha256,
            },
        )
    )
    return events


def _process_social_action(
    world: WorldState,
    manifest: ExperimentManifest,
    *,
    agent_id: str,
    action,
) -> list[WorldEvent]:
    config = manifest.runtime.social
    agent = world.agents[agent_id]

    if not config.enabled:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation=_operation_name(action.kind),
            reason="social_channel_disabled",
        )
    if agent.resources.social_operations_remaining <= 0:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation=_operation_name(action.kind),
            reason="social_operation_budget_exhausted",
        )
    if len(world.social.messages) >= config.max_messages:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation=_operation_name(action.kind),
            reason="social_message_limit_reached",
        )
    if action.draft_content is None:
        raise ValueError("Validated social action lacks draft_content")

    encoded = action.draft_content.encode("utf-8")
    if len(encoded) > config.max_message_bytes:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation=_operation_name(action.kind),
            reason="social_message_too_large",
        )

    allowed = (
        {"social_send_message"}
        if config.mode == "direct"
        else {
            "social_open_issue",
            "social_open_pr",
            "social_post_message",
        }
    )
    if action.kind not in allowed:
        return _reject_action(
            world,
            agent_id=agent_id,
            action_id=action.action_id,
            operation=_operation_name(action.kind),
            reason="social_action_not_allowed_for_mode",
        )

    events = [
        _emit(
            world,
            event_type="resource.social_operation.consumed",
            actor_agent_id=agent_id,
            payload={"amount": 1},
        )
    ]

    if action.kind == "social_send_message":
        recipient = world.agents.get(action.target)
        if recipient is None:
            events.extend(
                _reject_action(
                    world,
                    agent_id=agent_id,
                    action_id=action.action_id,
                    operation="send_message",
                    reason="recipient_not_found",
                )
            )
            return events
        message = _message_record(
            world,
            action=action,
            sender_agent_id=agent_id,
            sender_generation=agent.generation,
            channel="direct",
            visibility="direct",
            recipient_agent_id=recipient.agent_id,
            recipient_generation=recipient.generation,
            thread_id=None,
            subject=None,
        )
        events.append(
            _emit(
                world,
                event_type="social.message.sent",
                actor_agent_id=agent_id,
                payload={"message": message.model_dump(mode="json")},
            )
        )
        events.append(
            _terminal_event(
                world,
                actor_agent_id=agent_id,
                action_id=action.action_id,
                operation="send_message",
                status="completed",
                reason="message_sent",
                result_data={
                    "message_id": message.message_id,
                    "recipient_agent_id": recipient.agent_id,
                    "recipient_generation": recipient.generation,
                },
            )
        )
        return events

    if action.kind in {"social_open_issue", "social_open_pr"}:
        if len(world.social.threads) >= config.max_threads:
            events.extend(
                _reject_action(
                    world,
                    agent_id=agent_id,
                    action_id=action.action_id,
                    operation=_operation_name(action.kind),
                    reason="social_thread_limit_reached",
                )
            )
            return events
        thread_kind = "issue" if action.kind == "social_open_issue" else "pull_request"
        thread_id = _thread_id(world, action.action_id, thread_kind)
        message = _message_record(
            world,
            action=action,
            sender_agent_id=agent_id,
            sender_generation=agent.generation,
            channel=thread_kind,
            visibility="public",
            recipient_agent_id=None,
            recipient_generation=None,
            thread_id=thread_id,
            subject=action.target,
        )
        thread = SocialThreadRecord(
            thread_id=thread_id,
            kind=thread_kind,
            created_tick=world.tick,
            creator_agent_id=agent_id,
            creator_generation=agent.generation,
            title=action.target,
            opening_message_id=message.message_id,
            message_ids=[message.message_id],
        )
        events.append(
            _emit(
                world,
                event_type="social.thread.opened",
                actor_agent_id=agent_id,
                payload={
                    "thread": thread.model_dump(mode="json"),
                    "message": message.model_dump(mode="json"),
                },
            )
        )
        events.append(
            _terminal_event(
                world,
                actor_agent_id=agent_id,
                action_id=action.action_id,
                operation=_operation_name(action.kind),
                status="completed",
                reason=f"{thread_kind}_opened",
                result_data={
                    "thread_id": thread.thread_id,
                    "message_id": message.message_id,
                },
            )
        )
        return events

    thread = world.social.threads.get(action.target)
    if thread is None:
        events.extend(
            _reject_action(
                world,
                agent_id=agent_id,
                action_id=action.action_id,
                operation="post_message",
                reason="social_thread_not_found",
            )
        )
        return events
    message = _message_record(
        world,
        action=action,
        sender_agent_id=agent_id,
        sender_generation=agent.generation,
        channel="thread_message",
        visibility="public",
        recipient_agent_id=None,
        recipient_generation=None,
        thread_id=thread.thread_id,
        subject=None,
    )
    events.append(
        _emit(
            world,
            event_type="social.thread.message_posted",
            actor_agent_id=agent_id,
            payload={"message": message.model_dump(mode="json")},
        )
    )
    events.append(
        _terminal_event(
            world,
            actor_agent_id=agent_id,
            action_id=action.action_id,
            operation="post_message",
            status="completed",
            reason="thread_message_posted",
            result_data={
                "thread_id": thread.thread_id,
                "message_id": message.message_id,
            },
        )
    )
    return events


def _message_record(
    world: WorldState,
    *,
    action,
    sender_agent_id: str,
    sender_generation: int,
    channel: str,
    visibility: str,
    recipient_agent_id: str | None,
    recipient_generation: int | None,
    thread_id: str | None,
    subject: str | None,
) -> SocialMessageRecord:
    if action.draft_content is None:
        raise ValueError("Social message requires draft_content")
    encoded = action.draft_content.encode("utf-8")
    sequence = world.last_sequence_number + 1
    event_id = deterministic_event_id(world.experiment_id, sequence)
    digest = state_hash(
        {
            "kind": "social-message",
            "experiment_id": world.experiment_id,
            "action_id": action.action_id,
            "channel": channel,
            "thread_id": thread_id,
        }
    )
    return SocialMessageRecord(
        message_id=f"message-{digest[:40]}",
        created_tick=world.tick,
        sender_agent_id=sender_agent_id,
        sender_generation=sender_generation,
        source_event_id=event_id,
        source_action_id=action.action_id,
        channel=channel,
        visibility=visibility,
        recipient_agent_id=recipient_agent_id,
        recipient_generation=recipient_generation,
        thread_id=thread_id,
        subject=subject,
        content=action.draft_content,
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        content_bytes=len(encoded),
    )


def _thread_id(world: WorldState, action_id: str, kind: str) -> str:
    digest = state_hash(
        {
            "kind": f"social-thread-{kind}",
            "experiment_id": world.experiment_id,
            "action_id": action_id,
        }
    )
    return f"thread-{digest[:40]}"


def _operation_name(action_kind: str) -> str:
    return {
        "social_send_message": "send_message",
        "social_open_issue": "open_issue",
        "social_open_pr": "open_pr",
        "social_post_message": "post_message",
    }[action_kind]


def _reject_action(
    world: WorldState,
    *,
    agent_id: str,
    action_id: str,
    operation: str,
    reason: str,
) -> list[WorldEvent]:
    events = [
        _emit(
            world,
            event_type="culture.operation.rejected",
            actor_agent_id=agent_id,
            payload={
                "action_id": action_id,
                "operation": operation,
                "reason": reason,
            },
        )
    ]
    events.append(
        _terminal_event(
            world,
            actor_agent_id=agent_id,
            action_id=action_id,
            operation=operation,
            status="rejected",
            reason=reason,
            result_data={},
        )
    )
    return events


def _terminal_event(
    world: WorldState,
    *,
    actor_agent_id: str,
    action_id: str,
    operation: str,
    status: str,
    reason: str,
    result_data: dict,
) -> WorldEvent:
    result_id = f"{action_id}:culture-result"
    material = {
        "result_id": result_id,
        "action_id": action_id,
        "created_tick": world.tick,
        "operation": operation,
        "status": status,
        "reason": reason,
        "result_data": result_data,
    }
    result = CultureActionResultRecord(
        **material,
        result_hash=state_hash(material),
    )
    return _emit(
        world,
        event_type=(
            "culture.action.completed"
            if status == "completed"
            else "culture.action.rejected"
        ),
        actor_agent_id=actor_agent_id,
        payload={
            "action_id": action_id,
            "result": result.model_dump(mode="json"),
        },
    )


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
