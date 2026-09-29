import os

import httpx

from dse.contracts.experiment import ModelProviderConfig
from dse.providers.base import ModelProvider
from dse.providers.fake import DeterministicFakeProvider
from dse.providers.opencode import OpenCodeProvider


def build_provider(
    config: ModelProviderConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> ModelProvider:
    if config.kind == "fake":
        return DeterministicFakeProvider()

    if config.kind == "opencode":
        return OpenCodeProvider(
            model=config.model,
            base_url=(
                config.base_url
                or "https://opencode.ai/inference/openai/v1"
            ),
            api_key=os.environ.get("OPENCODE_API_KEY"),
            timeout_seconds=config.timeout_seconds,
            client=client,
        )

    raise ValueError(f"Unsupported provider kind: {config.kind}")
