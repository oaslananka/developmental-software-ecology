from typing import Protocol

from dse.contracts.sandbox_runner import SandboxRunRequest, SandboxRunResult


class SandboxRunner(Protocol):
    async def run(
        self,
        request: SandboxRunRequest,
    ) -> SandboxRunResult:
        """Dispatch one already-admitted execution request to an external runner."""
        ...
