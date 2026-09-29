import asyncio
import json

import httpx
import pytest

from dse.contracts.experiment import ModelProviderConfig
from dse.contracts.model import ModelRequest
from dse.providers.factory import build_provider
from dse.providers.opencode import OpenCodeProvider, ProviderResponseError


def _request() -> ModelRequest:
    return ModelRequest(
        call_id="call-1",
        experiment_id="exp-1",
        agent_id="agent-0001",
        world_tick=200,
        context={
            "lifecycle_state": "idle",
            "activity_units_remaining": 760,
            "model_calls_remaining": 4,
        },
    )


def test_opencode_free_request_omits_authorization_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "model": "space-bunny-free",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "decision": "idle",
                                    "reason_summary": "No useful action is required.",
                                    "confidence": 0.8,
                                    "focus": None,
                                }
                            ),
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 11,
                },
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = OpenCodeProvider(
                model="space-bunny-free",
                client=client,
            )
            return await provider.generate(_request())

    response = asyncio.run(run())

    assert captured["authorization"] is None
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "space-bunny-free"
    assert response.provider == "opencode"
    assert response.model == "space-bunny-free"
    assert response.decision.decision == "idle"
    assert response.usage.input_tokens == 21
    assert response.usage.output_tokens == 11


def test_opencode_optional_api_key_adds_bearer_header() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "model": "space-bunny-free",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "decision": "observe",
                                    "reason_summary": "Inspect the environment.",
                                    "confidence": 0.7,
                                    "focus": "environment",
                                }
                            ),
                        }
                    }
                ],
                "usage": {},
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = OpenCodeProvider(
                model="space-bunny-free",
                api_key="test-key",
                client=client,
            )
            return await provider.generate(_request())

    asyncio.run(run())
    assert captured["authorization"] == "Bearer test-key"


def test_invalid_provider_output_is_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "space-bunny-free",
                "choices": [{"message": {"content": "not-json"}}],
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = OpenCodeProvider(
                model="space-bunny-free",
                client=client,
            )
            return await provider.generate(_request())

    with pytest.raises(ProviderResponseError):
        asyncio.run(run())


def test_factory_builds_opencode_provider_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    provider = build_provider(
        ModelProviderConfig(
            kind="opencode",
            model="space-bunny-free",
            base_url="https://opencode.ai/inference/openai/v1",
        )
    )

    assert isinstance(provider, OpenCodeProvider)
    assert provider.api_key is None
