from typing import Protocol

from dse.contracts.model import ModelRequest, ModelResponse


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one strict structured cognition response."""
        ...
