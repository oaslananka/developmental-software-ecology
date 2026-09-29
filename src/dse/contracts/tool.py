from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolExecutionResult(StrictModel):
    executor: Literal["deterministic-fake"] = "deterministic-fake"
    success: bool
    result_type: Literal[
        "workspace_inspection",
        "artifact_draft_preview",
        "validation_report",
    ]
    summary: str = Field(min_length=1, max_length=320)
    result_data: dict[str, Any]
    result_hash: str = Field(min_length=64, max_length=64)
