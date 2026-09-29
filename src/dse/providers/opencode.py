import json

import httpx
from pydantic import ValidationError

from dse.contracts.model import (
    CognitionDecision,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from dse.engine.hashing import state_hash


class ProviderResponseError(ValueError):
    """Raised when a provider response cannot be validated safely."""


class OpenCodeProvider:
    provider_name = "opencode"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://opencode.ai/inference/openai/v1",
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def generate(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": (
                520
                if request.response_schema == "CognitionDecision/v0.4"
                else 420
                if request.response_schema == "CognitionDecision/v0.3"
                else 320
                if request.response_schema == "CognitionDecision/v0.2"
                else 160
            ),
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(request),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "agent_id": request.agent_id,
                            "world_tick": request.world_tick,
                            "context": request.context,
                        },
                        sort_keys=True,
                    ),
                },
            ],
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response_data = await self._post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            payload=payload,
        )

        try:
            raw_content = response_data["choices"][0]["message"]["content"]
            decision = CognitionDecision.model_validate_json(raw_content)
        except (KeyError, IndexError, TypeError, ValidationError) as error:
            raise ProviderResponseError("OpenCode returned an invalid cognition response") from error

        if (
            request.response_schema == "CognitionDecision/v0.1"
            and decision.decision
            in {
                "propose_goal",
                "update_goal",
                "complete_goal",
                "abandon_goal",
                "propose_action",
            }
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.1 does not allow goal/action decisions"
            )

        if (
            request.response_schema == "CognitionDecision/v0.2"
            and decision.decision
            in {"update_goal", "complete_goal", "abandon_goal", "propose_action"}
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.2 does not allow lifecycle/action decisions"
            )

        if (
            request.response_schema == "CognitionDecision/v0.3"
            and decision.decision == "propose_action"
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.3 does not allow action proposals"
            )

        usage_data = response_data.get("usage") or {}
        usage = ModelUsage(
            input_tokens=int(usage_data.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_data.get("completion_tokens", 0) or 0),
        )

        request_hash = state_hash(request.model_dump(mode="json"))
        response_material = {
            "provider": self.provider_name,
            "model": response_data.get("model", self.model),
            "decision": decision.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
            "request_hash": request_hash,
        }

        return ModelResponse(
            call_id=request.call_id,
            provider=self.provider_name,
            model=str(response_data.get("model", self.model)),
            model_version=None,
            decision=decision,
            usage=usage,
            request_hash=request_hash,
            response_hash=state_hash(response_material),
        )

    def _system_prompt(self, request: ModelRequest) -> str:
        if request.response_schema == "CognitionDecision/v0.4":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", "propose_goal", "update_goal", '
                '"complete_goal", "abandon_goal", or "propose_action". '
                "For propose_action, include only action with kind, summary, target, "
                "rationale, expected_value, estimated_cost, and draft_content. "
                "Allowed action kinds are inspect_workspace, draft_artifact, run_validation. "
                "draft_artifact requires draft_content; all other kinds require it to be null. "
                "Action proposals are intents only and are NOT executed. "
                "Only propose_action when an active_goal exists and "
                "action_proposals_remaining is greater than zero. "
                "Use goal/goal_update/goal_closure exactly as required by v0.3 goal decisions. "
                "Do not include markdown or extra fields."
            )

        if request.response_schema == "CognitionDecision/v0.3":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", "propose_goal", "update_goal", '
                '"complete_goal", or "abandon_goal". '
                'Always include "decision", "reason_summary", "confidence", and "focus". '
                'For propose_goal, include only "goal" with title, description, '
                "motivation_summary, expected_value, estimated_cost, confidence. "
                'For update_goal, include only "goal_update" with progress, '
                "progress_summary, confidence. Progress must increase and remain below 1.0. "
                'For complete_goal or abandon_goal, include only "goal_closure" '
                "with summary and confidence. "
                "For idle or observe, goal, goal_update, goal_closure, and action must be null. "
                "Only propose a goal when context.active_goal is null. "
                "Only update/complete/abandon when context.active_goal is present. "
                "Do not include markdown or extra fields."
            )

        if request.response_schema == "CognitionDecision/v0.2":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", or "propose_goal". '
                'Fields: "decision", "reason_summary", "confidence", "focus", "goal", '
                '"goal_update", "goal_closure", "action". '
                "When decision is propose_goal, goal must be an object with "
                '"title", "description", "motivation_summary", "expected_value", '
                '"estimated_cost", and "confidence". All numeric values must be 0..1. '
                "For idle or observe, goal must be null. "
                "goal_update, goal_closure, and action must always be null in v0.2. "
                "Only propose a goal when context.goal_generation_enabled is true and "
                "context.active_goal is null. Do not include markdown or extra fields."
            )

        return (
            "Return exactly one JSON object matching this schema: "
            '{"decision":"idle|observe","reason_summary":"short text",'
            '"confidence":0.0,"focus":"optional short text or null",'
            '"goal":null,"goal_update":null,"goal_closure":null,"action":null}. '
            "Do not include markdown or additional fields."
        )

    async def _post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict,
    ) -> dict:
        if self._client is not None:
            response = await self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
