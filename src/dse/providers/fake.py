from dse.contracts.model import (
    ActionProposal,
    CognitionDecision,
    GoalClosure,
    GoalProgressUpdate,
    GoalProposal,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from dse.engine.hashing import state_hash


class DeterministicFakeProvider:
    provider_name = "deterministic-fake"
    model_name = "fake-cognition-v0.1"
    model_version = "1"

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        request_hash = state_hash(request.model_dump(mode="json"))

        goal_generation_enabled = bool(
            request.context.get("goal_generation_enabled", False)
        )
        action_generation_enabled = bool(
            request.context.get("action_generation_enabled", False)
        )
        active_goal = request.context.get("active_goal")
        action_budget = int(
            request.context.get("action_proposals_remaining", 0) or 0
        )

        if (
            request.response_schema
            in {
                "CognitionDecision/v0.2",
                "CognitionDecision/v0.3",
                "CognitionDecision/v0.4",
                "CognitionDecision/v0.5",
            }
            and goal_generation_enabled
            and active_goal is None
        ):
            decision = _goal_proposal()
        elif (
            request.response_schema == "CognitionDecision/v0.5"
            and active_goal is not None
        ):
            decision = _forge_v05_decision(
                request,
                active_goal=active_goal,
                action_generation_enabled=action_generation_enabled,
                action_budget=action_budget,
            )
        elif (
            request.response_schema == "CognitionDecision/v0.4"
            and action_generation_enabled
            and active_goal is not None
            and float(active_goal.get("progress", 0.0)) == 0.0
            and action_budget >= 2
        ):
            decision = CognitionDecision(
                decision="propose_action",
                reason_summary=(
                    "Inspecting the workspace is the next bounded step toward the active goal."
                ),
                confidence=0.85,
                focus="workspace",
                action=ActionProposal(
                    kind="inspect_workspace",
                    summary="Inspect the current workspace structure.",
                    target="workspace",
                    rationale=(
                        "The active goal needs grounded context before an artifact is drafted."
                    ),
                    expected_value=0.8,
                    estimated_cost=0.2,
                ),
            )
        elif (
            request.response_schema == "CognitionDecision/v0.4"
            and action_generation_enabled
            and active_goal is not None
            and float(active_goal.get("progress", 0.0)) == 0.0
            and action_budget == 1
        ):
            decision = CognitionDecision(
                decision="propose_action",
                reason_summary=(
                    "A draft artifact would externalize the current working hypothesis."
                ),
                confidence=0.85,
                focus="artifact",
                action=ActionProposal(
                    kind="draft_artifact",
                    summary="Draft a short hypothesis artifact.",
                    target="notes/recurring-patterns.md",
                    rationale=(
                        "Externalizing the hypothesis creates a persistent object for later work."
                    ),
                    expected_value=0.85,
                    estimated_cost=0.35,
                    draft_content=(
                        "# Recurring Pattern Hypothesis\n\n"
                        "Initial observations suggest recurring environmental signals "
                        "worth validating in later cycles.\n"
                    ),
                ),
            )
        elif (
            request.response_schema == "CognitionDecision/v0.4"
            and active_goal is not None
        ):
            progress = float(active_goal.get("progress", 0.0))

            if progress < 0.5:
                decision = CognitionDecision(
                    decision="update_goal",
                    reason_summary="The bounded action intents establish initial progress.",
                    confidence=0.8,
                    focus="environment",
                    goal_update=GoalProgressUpdate(
                        progress=0.5,
                        progress_summary=(
                            "Workspace inspection and artifact drafting were proposed."
                        ),
                        confidence=0.8,
                    ),
                )
            elif progress < 0.8:
                decision = CognitionDecision(
                    decision="update_goal",
                    reason_summary="The active objective has accumulated substantial progress.",
                    confidence=0.85,
                    focus="environment",
                    goal_update=GoalProgressUpdate(
                        progress=0.8,
                        progress_summary=(
                            "The recurring pattern hypothesis has been refined conceptually."
                        ),
                        confidence=0.85,
                    ),
                )
            else:
                decision = _goal_completion(
                    "A stable recurring-pattern hypothesis and bounded action intents "
                    "were formed across cognition cycles."
                )
        elif (
            request.response_schema == "CognitionDecision/v0.3"
            and active_goal is not None
        ):
            progress = float(active_goal.get("progress", 0.0))

            if progress < 0.4:
                decision = CognitionDecision(
                    decision="update_goal",
                    reason_summary="The active objective has accumulated initial progress.",
                    confidence=0.8,
                    focus="environment",
                    goal_update=GoalProgressUpdate(
                        progress=0.4,
                        progress_summary=(
                            "Initial recurring patterns have been identified and retained."
                        ),
                        confidence=0.8,
                    ),
                )
            elif progress < 0.8:
                decision = CognitionDecision(
                    decision="update_goal",
                    reason_summary="The active objective has accumulated substantial progress.",
                    confidence=0.85,
                    focus="environment",
                    goal_update=GoalProgressUpdate(
                        progress=0.8,
                        progress_summary=(
                            "The recurring pattern hypothesis has been refined across observations."
                        ),
                        confidence=0.85,
                    ),
                )
            else:
                decision = _goal_completion(
                    "A stable recurring-pattern hypothesis was formed and refined "
                    "across multiple cognition cycles."
                )
        else:
            selector = int(request_hash[:8], 16) % 2
            decision = CognitionDecision(
                decision="observe" if selector else "idle",
                reason_summary=(
                    "Inspect the current environment at the next permitted opportunity."
                    if selector
                    else "No higher-value cognition is required at this trigger."
                ),
                confidence=0.75,
                focus="environment" if selector else None,
            )

        response_material = {
            "call_id": request.call_id,
            "provider": self.provider_name,
            "model": self.model_name,
            "model_version": self.model_version,
            "decision": decision.model_dump(mode="json"),
            "usage": {"input_tokens": 32, "output_tokens": 12},
            "request_hash": request_hash,
        }

        return ModelResponse(
            call_id=request.call_id,
            provider=self.provider_name,
            model=self.model_name,
            model_version=self.model_version,
            decision=decision,
            usage=ModelUsage(input_tokens=32, output_tokens=12),
            request_hash=request_hash,
            response_hash=state_hash(response_material),
        )


def _goal_proposal() -> CognitionDecision:
    goal = GoalProposal(
        title="Investigate recurring environmental patterns",
        description=(
            "Develop a persistent line of inquiry around recurring signals "
            "in the current environment and use later observations to refine it."
        ),
        motivation_summary=(
            "A stable self-chosen objective provides continuity across cognition cycles."
        ),
        expected_value=0.75,
        estimated_cost=0.45,
        confidence=0.8,
    )
    return CognitionDecision(
        decision="propose_goal",
        reason_summary="A durable self-generated objective is currently absent.",
        confidence=0.8,
        focus="environment",
        goal=goal,
    )


def _forge_v05_decision(
    request: ModelRequest,
    *,
    active_goal: dict,
    action_generation_enabled: bool,
    action_budget: int,
) -> CognitionDecision:
    context = request.context
    forge_enabled = bool(context.get("forge_action_enabled", False))
    forge_budget = int(context.get("forge_operations_remaining", 0) or 0)
    forge_results = list(context.get("forge_results") or [])
    repositories = list((context.get("forge_world") or {}).get("repositories") or [])
    progress = float(active_goal.get("progress", 0.0))

    if action_generation_enabled and forge_enabled and action_budget > 0:
        if not forge_results and forge_budget > 0:
            return CognitionDecision(
                decision="propose_action",
                reason_summary=(
                    "A persistent public repository is needed to externalize the active inquiry."
                ),
                confidence=0.9,
                focus="culture",
                action=ActionProposal(
                    kind="forge_create_repository",
                    summary="Create a public cultural repository.",
                    target=f"culture-{request.agent_id}",
                    rationale=(
                        "A durable shared repository creates a substrate that can outlive "
                        "private agent memory."
                    ),
                    expected_value=0.9,
                    estimated_cost=0.25,
                ),
            )

        create_result = _result_for_operation(forge_results, "create_repository")
        if (
            len(forge_results) == 1
            and create_result is not None
            and create_result.get("status") == "completed"
            and forge_budget > 0
        ):
            own_repo_id = create_result["result_data"]["repo_id"]
            return CognitionDecision(
                decision="propose_action",
                reason_summary=(
                    "The first public artifact should preserve the current hypothesis."
                ),
                confidence=0.9,
                focus="culture",
                action=ActionProposal(
                    kind="forge_publish_artifact",
                    summary="Publish the initial hypothesis artifact.",
                    target="notes/hypothesis.md",
                    rationale=(
                        "Publishing a durable artifact allows later agents to inspect and reuse it."
                    ),
                    expected_value=0.92,
                    estimated_cost=0.35,
                    draft_content=(
                        "# Persistent Hypothesis\n\n"
                        f"Created by {request.agent_id}.\n"
                        "Recurring environmental patterns may become more useful when "
                        "externalized into persistent shared artifacts.\n"
                    ),
                    repo_id=own_repo_id,
                ),
            )

        last_result = forge_results[-1] if forge_results else None
        if (
            len(forge_results) == 2
            and last_result is not None
            and last_result.get("operation") == "publish_artifact"
            and last_result.get("status") == "completed"
            and forge_budget > 0
        ):
            own_repo_id = create_result["result_data"]["repo_id"] if create_result else None
            candidates = [
                repo
                for repo in repositories
                if repo.get("repo_id") != own_repo_id
                and int(repo.get("artifact_count", 0)) > 0
            ]
            if not candidates:
                candidates = [
                    repo
                    for repo in repositories
                    if int(repo.get("artifact_count", 0)) > 0
                ]
            if candidates:
                selected = sorted(
                    candidates,
                    key=lambda item: str(item.get("repo_id")),
                )[0]
                return CognitionDecision(
                    decision="propose_action",
                    reason_summary=(
                        "Inspect a public artifact produced by another repository before "
                        "creating a derived contribution."
                    ),
                    confidence=0.9,
                    focus="culture",
                    action=ActionProposal(
                        kind="forge_inspect_repository",
                        summary="Inspect an existing public repository.",
                        target=str(selected["repo_id"]),
                        rationale=(
                            "Direct observation of existing public artifacts enables "
                            "cultural reuse rather than isolated reinvention."
                        ),
                        expected_value=0.9,
                        estimated_cost=0.2,
                    ),
                )

        if (
            len(forge_results) == 3
            and last_result is not None
            and last_result.get("operation") == "inspect_repository"
            and last_result.get("status") == "completed"
            and forge_budget > 0
        ):
            files = list(last_result.get("result_data", {}).get("files") or [])
            own_repo_id = create_result["result_data"]["repo_id"] if create_result else None
            own_repo = next(
                (
                    repo
                    for repo in repositories
                    if repo.get("repo_id") == own_repo_id
                ),
                None,
            )
            if files and own_repo is not None:
                parent = files[0]
                parent_artifact_id = str(parent["artifact_id"])
                parent_content = str(parent.get("content") or "")
                return CognitionDecision(
                    decision="propose_action",
                    reason_summary=(
                        "Create a derived public artifact that explicitly reuses an observed "
                        "cultural parent."
                    ),
                    confidence=0.92,
                    focus="culture",
                    action=ActionProposal(
                        kind="forge_publish_artifact",
                        summary="Publish a derived artifact with explicit lineage.",
                        target="notes/derived.md",
                        rationale=(
                            "Explicit lineage makes cross-agent cultural reuse measurable."
                        ),
                        expected_value=0.95,
                        estimated_cost=0.4,
                        draft_content=(
                            "# Derived Cultural Artifact\n\n"
                            f"Created by {request.agent_id}.\n"
                            f"Parent artifact: {parent_artifact_id}\n\n"
                            "Observed parent excerpt:\n"
                            f"{parent_content[:400]}\n"
                        ),
                        repo_id=str(own_repo_id),
                        parent_artifact_ids=[parent_artifact_id],
                        expected_parent_commit_id=own_repo.get(
                            "head_commit_id"
                        ),
                    ),
                )

    if len(forge_results) >= 4 and progress < 0.8:
        return CognitionDecision(
            decision="update_goal",
            reason_summary=(
                "The agent has published, inspected, and explicitly reused persistent culture."
            ),
            confidence=0.9,
            focus="culture",
            goal_update=GoalProgressUpdate(
                progress=0.8,
                progress_summary=(
                    "Created a repository, published an artifact, inspected public culture, "
                    "and published a lineage-linked derivative."
                ),
                confidence=0.9,
            ),
        )

    if progress >= 0.8:
        return _goal_completion(
            "The inquiry externalized durable artifacts and reused public culture "
            "with explicit lineage."
        )

    return CognitionDecision(
        decision="observe",
        reason_summary=(
            "Wait for sufficient Forge evidence before advancing the active objective."
        ),
        confidence=0.7,
        focus="culture",
    )


def _result_for_operation(
    results: list[dict],
    operation: str,
) -> dict | None:
    for result in results:
        if result.get("operation") == operation:
            return result
    return None


def _goal_completion(summary: str) -> CognitionDecision:
    return CognitionDecision(
        decision="complete_goal",
        reason_summary="The active inquiry has reached its intended stopping point.",
        confidence=0.9,
        focus="environment",
        goal_closure=GoalClosure(
            summary=summary,
            confidence=0.9,
        ),
    )
