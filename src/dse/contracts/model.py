from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalProposal(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=320)
    motivation_summary: str = Field(min_length=1, max_length=240)
    expected_value: float = Field(ge=0.0, le=1.0)
    estimated_cost: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class GoalProgressUpdate(StrictModel):
    progress: float = Field(gt=0.0, le=1.0)
    progress_summary: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)


class GoalClosure(StrictModel):
    summary: str = Field(min_length=1, max_length=320)
    confidence: float = Field(ge=0.0, le=1.0)


class ActionProposal(StrictModel):
    kind: Literal[
        "inspect_workspace",
        "draft_artifact",
        "run_validation",
        "functional_submit",
        "forge_create_repository",
        "forge_inspect_repository",
        "forge_publish_artifact",
        "text_publish",
        "social_send_message",
        "social_open_issue",
        "social_open_pr",
        "social_post_message",
    ]
    summary: str = Field(min_length=1, max_length=240)
    target: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=240)
    expected_value: float = Field(ge=0.0, le=1.0)
    estimated_cost: float = Field(ge=0.0, le=1.0)
    draft_content: str | None = Field(default=None, max_length=4000)
    repo_id: str | None = Field(default=None, max_length=96)
    parent_artifact_ids: list[str] = Field(default_factory=list, max_length=64)
    expected_parent_commit_id: str | None = Field(default=None, max_length=96)
    parent_text_entry_ids: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_action_shape(self):
        forge_publish = self.kind == "forge_publish_artifact"
        text_publish = self.kind == "text_publish"
        content_kinds = {
            "draft_artifact",
            "functional_submit",
            "forge_publish_artifact",
            "text_publish",
            "social_send_message",
            "social_open_issue",
            "social_open_pr",
            "social_post_message",
        }
        if self.kind in content_kinds:
            if not self.draft_content:
                raise ValueError(f"{self.kind} requires draft_content")
        elif self.draft_content is not None:
            raise ValueError(
                "draft_content is only allowed for content-bearing actions"
            )

        if forge_publish:
            if self.repo_id is None:
                raise ValueError("forge_publish_artifact requires repo_id")
        elif self.repo_id is not None:
            raise ValueError("repo_id is only allowed for forge_publish_artifact")

        if self.parent_artifact_ids and not forge_publish:
            raise ValueError(
                "parent_artifact_ids are only allowed for forge_publish_artifact"
            )
        if self.expected_parent_commit_id is not None and not forge_publish:
            raise ValueError(
                "expected_parent_commit_id is only allowed for forge_publish_artifact"
            )
        if self.parent_text_entry_ids and not text_publish:
            raise ValueError(
                "parent_text_entry_ids are only allowed for text_publish"
            )
        return self


class CognitionDecision(StrictModel):
    decision: Literal[
        "idle",
        "observe",
        "propose_goal",
        "update_goal",
        "complete_goal",
        "abandon_goal",
        "propose_action",
    ]
    reason_summary: str = Field(min_length=1, max_length=240)
    confidence: float = Field(ge=0.0, le=1.0)
    focus: str | None = Field(default=None, max_length=160)
    goal: GoalProposal | None = None
    goal_update: GoalProgressUpdate | None = None
    goal_closure: GoalClosure | None = None
    action: ActionProposal | None = None

    @model_validator(mode="after")
    def validate_payload_shape(self):
        expected = {
            "propose_goal": ("goal",),
            "update_goal": ("goal_update",),
            "complete_goal": ("goal_closure",),
            "abandon_goal": ("goal_closure",),
            "propose_action": ("action",),
        }

        populated = {
            name
            for name, value in (
                ("goal", self.goal),
                ("goal_update", self.goal_update),
                ("goal_closure", self.goal_closure),
                ("action", self.action),
            )
            if value is not None
        }

        required = set(expected.get(self.decision, ()))
        if populated != required:
            raise ValueError(
                f"{self.decision} requires exactly {sorted(required)}, "
                f"received {sorted(populated)}"
            )
        return self


class ModelUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelRequest(StrictModel):
    call_id: str
    experiment_id: str
    agent_id: str
    world_tick: int = Field(ge=0)
    context: dict[str, Any]
    response_schema: Literal[
        "CognitionDecision/v0.1",
        "CognitionDecision/v0.2",
        "CognitionDecision/v0.3",
        "CognitionDecision/v0.4",
        "CognitionDecision/v0.5",
        "CognitionDecision/v0.6",
    ] = "CognitionDecision/v0.1"


class ModelResponse(StrictModel):
    call_id: str
    provider: str
    model: str
    model_version: str | None = None
    decision: CognitionDecision
    usage: ModelUsage
    request_hash: str
    response_hash: str
