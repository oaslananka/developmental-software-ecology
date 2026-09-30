from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from dse.contracts.event import WorldEvent
from dse.contracts.experiment import ExperimentManifest
from dse.engine.hashing import state_hash


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComputeBudgetFingerprint(StrictModel):
    compute_match_group: str = Field(min_length=1)
    agent_count: int
    max_world_ticks: int
    tick_duration_sim_seconds: int
    active_ticks_per_cycle: int
    sleep_ticks_per_cycle: int
    turnover_ticks: tuple[int, ...]
    turnover_agent_ids: tuple[str, ...]
    model_calls_per_cycle: int
    cognition_interval_ticks: int
    goal_generation_enabled: bool
    goal_lifecycle_enabled: bool
    action_proposals_per_cycle: int
    tool_executions_per_cycle: int
    memory_capacity: int
    memory_retrieval_limit: int
    forge_operations_per_cycle: int
    text_operations_per_cycle: int
    social_operations_per_cycle: int
    text_max_entries: int
    text_max_entry_bytes: int
    social_max_messages: int
    social_max_threads: int
    social_max_message_bytes: int
    max_artifact_bytes: int
    max_repositories: int
    max_artifacts_per_repository: int
    model_provider_kind: str
    model_id: str
    model_base_url: str | None
    model_timeout_seconds: float
    sandbox_enabled: bool
    web_enabled: bool
    functional_opportunity_profile: str
    functional_submission_path: str
    functional_submission_max_bytes: int


class ComputeUsage(StrictModel):
    event_count: int = Field(ge=0)
    final_world_tick: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    action_proposals: int = Field(ge=0)
    tool_executions: int = Field(ge=0)
    forge_operations: int = Field(ge=0)
    text_operations: int = Field(ge=0)
    social_operations: int = Field(ge=0)
    text_entries_published: int = Field(ge=0)
    social_messages_created: int = Field(ge=0)
    social_threads_created: int = Field(ge=0)
    functional_submissions: int = Field(ge=0)
    turnovers: int = Field(ge=0)
    repositories_created: int = Field(ge=0)
    artifacts_committed: int = Field(ge=0)


class ObservedComputeMatchReport(StrictModel):
    matched: bool
    fields: tuple[str, ...]
    reference_label: str
    differences: dict[str, dict[str, int]] = Field(default_factory=dict)


def compute_budget_fingerprint(
    manifest: ExperimentManifest,
) -> ComputeBudgetFingerprint:
    group = manifest.study.compute_match_group
    if group is None:
        raise ValueError("Manifest does not declare a compute_match_group")

    return ComputeBudgetFingerprint(
        compute_match_group=group,
        agent_count=manifest.world.agent_count,
        max_world_ticks=manifest.world.max_world_ticks,
        tick_duration_sim_seconds=manifest.world.tick_duration_sim_seconds,
        active_ticks_per_cycle=manifest.agents.lifecycle.active_ticks_per_cycle,
        sleep_ticks_per_cycle=manifest.agents.lifecycle.sleep_ticks_per_cycle,
        turnover_ticks=tuple(manifest.agents.turnover.ticks),
        turnover_agent_ids=tuple(manifest.agents.turnover.agent_ids),
        model_calls_per_cycle=manifest.agents.cognition.model_calls_per_cycle,
        cognition_interval_ticks=manifest.agents.cognition.interval_ticks,
        goal_generation_enabled=manifest.agents.goals.enabled,
        goal_lifecycle_enabled=manifest.agents.goals.lifecycle_enabled,
        action_proposals_per_cycle=manifest.agents.actions.proposals_per_cycle,
        tool_executions_per_cycle=(
            manifest.agents.tool_broker.executions_per_cycle
        ),
        memory_capacity=manifest.agents.memory.capacity,
        memory_retrieval_limit=manifest.agents.memory.retrieval_limit,
        forge_operations_per_cycle=manifest.runtime.forge.operations_per_cycle,
        text_operations_per_cycle=(
            manifest.runtime.text_culture.operations_per_cycle
        ),
        social_operations_per_cycle=manifest.runtime.social.operations_per_cycle,
        text_max_entries=manifest.runtime.text_culture.max_entries,
        text_max_entry_bytes=manifest.runtime.text_culture.max_entry_bytes,
        social_max_messages=manifest.runtime.social.max_messages,
        social_max_threads=manifest.runtime.social.max_threads,
        social_max_message_bytes=manifest.runtime.social.max_message_bytes,
        max_artifact_bytes=manifest.runtime.forge.max_artifact_bytes,
        max_repositories=manifest.runtime.forge.max_repositories,
        max_artifacts_per_repository=(
            manifest.runtime.forge.max_artifacts_per_repository
        ),
        model_provider_kind=manifest.runtime.model_provider.kind,
        model_id=manifest.runtime.model_provider.model,
        model_base_url=manifest.runtime.model_provider.base_url,
        model_timeout_seconds=manifest.runtime.model_provider.timeout_seconds,
        sandbox_enabled=manifest.runtime.sandbox_enabled,
        web_enabled=manifest.runtime.web_enabled,
        functional_opportunity_profile=(
            manifest.functional_opportunity_profile
        ),
        functional_submission_path=(
            manifest.functional_opportunity.submission_path
        ),
        functional_submission_max_bytes=(
            manifest.functional_opportunity.max_submission_bytes
        ),
    )


def assert_compute_budget_matched(
    manifests: Iterable[ExperimentManifest],
) -> str:
    items = list(manifests)
    if len(items) < 2:
        raise ValueError("At least two manifests are required for compute matching")

    fingerprints = [
        compute_budget_fingerprint(manifest)
        for manifest in items
    ]
    groups = {item.compute_match_group for item in fingerprints}
    if len(groups) != 1:
        raise ValueError(
            "Compute-matched manifests must share one compute_match_group"
        )

    reference = fingerprints[0]
    reference_payload = reference.model_dump(mode="json")
    reference_payload.pop("compute_match_group")

    mismatches: list[str] = []
    for manifest, fingerprint in zip(items[1:], fingerprints[1:], strict=True):
        payload = fingerprint.model_dump(mode="json")
        payload.pop("compute_match_group")
        if payload != reference_payload:
            mismatches.append(manifest.experiment.id)

    if mismatches:
        raise ValueError(
            "Compute budget fingerprint mismatch: "
            + ", ".join(mismatches)
        )

    return state_hash(reference.model_dump(mode="json"))


def compute_usage(events: Iterable[WorldEvent]) -> ComputeUsage:
    batch = list(events)
    counts = Counter(event.event_type for event in batch)
    model_events = [
        event
        for event in batch
        if event.event_type == "model.call.completed"
    ]

    input_tokens = sum(
        int(event.payload.get("input_tokens", 0))
        for event in model_events
    )
    output_tokens = sum(
        int(event.payload.get("output_tokens", 0))
        for event in model_events
    )
    final_world_tick = max(
        (event.world_tick for event in batch),
        default=0,
    )

    return ComputeUsage(
        event_count=len(batch),
        final_world_tick=final_world_tick,
        model_calls=len(model_events),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        action_proposals=counts["resource.action_proposal.consumed"],
        tool_executions=counts["resource.tool_execution.consumed"],
        forge_operations=counts["resource.forge_operation.consumed"],
        text_operations=counts["resource.text_operation.consumed"],
        social_operations=counts["resource.social_operation.consumed"],
        text_entries_published=counts["culture.text.published"],
        social_messages_created=(
            counts["social.message.sent"]
            + counts["social.thread.opened"]
            + counts["social.thread.message_posted"]
        ),
        social_threads_created=counts["social.thread.opened"],
        functional_submissions=counts["functional.submission.published"],
        turnovers=counts["agent.lifecycle.turned_over"],
        repositories_created=counts["forge.repository.created"],
        artifacts_committed=counts["forge.artifact.committed"],
    )


def compare_observed_compute(
    usages: dict[str, ComputeUsage],
    *,
    fields: tuple[str, ...] = (
        "final_world_tick",
        "model_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "action_proposals",
    ),
) -> ObservedComputeMatchReport:
    if len(usages) < 2:
        raise ValueError("At least two usages are required for comparison")

    labels = sorted(usages)
    reference_label = labels[0]
    reference = usages[reference_label]
    differences: dict[str, dict[str, int]] = {}

    for label in labels[1:]:
        current = usages[label]
        field_differences: dict[str, int] = {}
        for field in fields:
            reference_value = int(getattr(reference, field))
            current_value = int(getattr(current, field))
            if current_value != reference_value:
                field_differences[field] = current_value - reference_value
        if field_differences:
            differences[label] = field_differences

    return ObservedComputeMatchReport(
        matched=not differences,
        fields=fields,
        reference_label=reference_label,
        differences=differences,
    )
