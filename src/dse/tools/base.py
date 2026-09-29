from typing import Protocol

from dse.contracts.agent import ActionIntentRecord
from dse.contracts.tool import ToolExecutionResult


class ToolExecutor(Protocol):
    async def execute(
        self,
        action: ActionIntentRecord,
    ) -> ToolExecutionResult:
        """Execute one already-validated action intent."""
        ...
