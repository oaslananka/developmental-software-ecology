from dse.contracts.model import (
    CognitionDecision,
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
