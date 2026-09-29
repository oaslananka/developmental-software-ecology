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
            "max_tokens": 160,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object matching this schema: "
                        '{"decision":"idle|observe","reason_summary":"short text",'
                        '"confidence":0.0,"focus":"optional short text or null"}. '
                        "Do not include markdown or additional fields."
                    ),
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
