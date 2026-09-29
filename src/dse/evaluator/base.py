from typing import Literal, Protocol

from dse.contracts.evaluation import (
    HiddenEvaluationRequest,
    HiddenEvaluationRunnerResult,
)


class HiddenEvaluatorRunner(Protocol):
    runner_kind: Literal["test-double", "external-hardened"]
    runner_version: str

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
    ) -> HiddenEvaluationRunnerResult:
        """Evaluate public culture against a privately held hidden suite."""
        ...
