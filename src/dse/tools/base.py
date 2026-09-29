from typing import Protocol

from dse.contracts.agent import ActionIntentRecord
from dse.contracts.tool import ToolExecutionResult


class ToolExecutor(Protocol):
    executor_name: str

    def policy_rejection_reason(
        self,
        action: ActionIntentRecord,
    ) -> str | None:
        """Return a policy rejection reason before budget consumption, if any."""
        ...

    async def execute(
        self,
        action: ActionIntentRecord,
    ) -> ToolExecutionResult:
        """Execute one already-validated action intent."""
        ...
