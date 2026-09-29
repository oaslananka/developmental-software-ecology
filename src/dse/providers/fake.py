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
            }
            and goal_generation_enabled
            and active_goal is None
        ):
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
            decision = CognitionDecision(
                decision="propose_goal",
                reason_summary="A durable self-generated objective is currently absent.",
                confidence=0.8,
                focus="environment",
                goal=goal,
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
                decision = CognitionDecision(
                    decision="complete_goal",
                    reason_summary="The internal inquiry has reached its intended stopping point.",
                    confidence=0.9,
                    focus="environment",
                    goal_closure=GoalClosure(
                        summary=(
                            "A stable recurring-pattern hypothesis and bounded action intents "
                            "were formed across cognition cycles."
                        ),
                        confidence=0.9,
                    ),
                )
        elif request.response_schema == "CognitionDecision/v0.3" and active_goal is not None:
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
                decision = CognitionDecision(
                    decision="complete_goal",
                    reason_summary="The active inquiry has reached its intended stopping point.",
                    confidence=0.9,
                    focus="environment",
                    goal_closure=GoalClosure(
                        summary=(
                            "A stable recurring-pattern hypothesis was formed and refined "
                            "across multiple cognition cycles."
                        ),
                        confidence=0.9,
                    ),
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
