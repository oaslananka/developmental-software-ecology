from dse.contracts.model import (
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
        active_goal = request.context.get("active_goal")

        if (
            request.response_schema in {
                "CognitionDecision/v0.2",
                "CognitionDecision/v0.3",
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
