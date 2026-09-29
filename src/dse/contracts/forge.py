from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_forge_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("forge path must be relative")
    if "://" in normalized:
        raise ValueError("forge path must not be a network target")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("forge path contains an unsafe segment")
    return normalized


class MutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ForgeArtifactRecord(MutableModel):
    artifact_id: str = Field(min_length=1, max_length=96)
    repo_id: str = Field(min_length=1, max_length=96)
    path: str = Field(min_length=1, max_length=240)
    created_tick: int = Field(ge=0)
    creator_agent_id: str = Field(min_length=1, max_length=120)
    creator_generation: int = Field(ge=0)
    source_event_id: str = Field(min_length=1)
    source_action_id: str | None = Field(default=None, max_length=240)
    commit_id: str = Field(min_length=1, max_length=96)
    content: str = Field(max_length=65_536)
    content_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    content_bytes: int = Field(ge=0, le=65_536)
    media_type: str = Field(default="text/plain", min_length=1, max_length=120)
    previous_artifact_id: str | None = Field(default=None, max_length=96)
    parent_artifact_ids: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_forge_path(value)


class ForgeCommitRecord(MutableModel):
    commit_id: str = Field(min_length=1, max_length=96)
    repo_id: str = Field(min_length=1, max_length=96)
    parent_commit_id: str | None = Field(default=None, max_length=96)
    created_tick: int = Field(ge=0)
    author_agent_id: str = Field(min_length=1, max_length=120)
    author_generation: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=240)
    artifact_ids: list[str] = Field(min_length=1, max_length=64)


class ForgeRepositoryRecord(MutableModel):
    repo_id: str = Field(min_length=1, max_length=96)
    name: str = Field(
        min_length=1,
        max_length=80,
        pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
    )
    created_tick: int = Field(ge=0)
    creator_agent_id: str = Field(min_length=1, max_length=120)
    creator_generation: int = Field(ge=0)
    default_branch: str = Field(min_length=1, max_length=80)
    head_commit_id: str | None = Field(default=None, max_length=96)
    commit_count: int = Field(default=0, ge=0)
    artifact_count: int = Field(default=0, ge=0)
    path_heads: dict[str, str] = Field(default_factory=dict)


class ForgeWorldState(MutableModel):
    repositories: dict[str, ForgeRepositoryRecord] = Field(default_factory=dict)
    commits: dict[str, ForgeCommitRecord] = Field(default_factory=dict)
    artifacts: dict[str, ForgeArtifactRecord] = Field(default_factory=dict)


class ForgeCreateRepositoryRequest(StrictModel):
    experiment_id: str = Field(min_length=1)
    operation_sequence: int = Field(ge=1)
    world_tick: int = Field(ge=0)
    actor_agent_id: str = Field(min_length=1)
    actor_generation: int = Field(ge=0)
    name: str = Field(
        min_length=1,
        max_length=80,
        pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
    )
    default_branch: str = Field(min_length=1, max_length=80)


class ForgeCreateRepositoryResult(StrictModel):
    provider: str = Field(pattern="^deterministic-memory$")
    repo_id: str = Field(min_length=1, max_length=96)


class ForgeCommitArtifactRequest(StrictModel):
    experiment_id: str = Field(min_length=1)
    operation_sequence: int = Field(ge=1)
    world_tick: int = Field(ge=0)
    actor_agent_id: str = Field(min_length=1)
    actor_generation: int = Field(ge=0)
    repo_id: str = Field(min_length=1, max_length=96)
    parent_commit_id: str | None = Field(default=None, max_length=96)
    path: str = Field(min_length=1, max_length=240)
    content_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    content_bytes: int = Field(ge=0, le=65_536)
    message: str = Field(min_length=1, max_length=240)
    previous_artifact_id: str | None = Field(default=None, max_length=96)
    parent_artifact_ids: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_forge_path(value)


class ForgeCommitArtifactResult(StrictModel):
    provider: str = Field(pattern="^deterministic-memory$")
    commit_id: str = Field(min_length=1, max_length=96)
    artifact_id: str = Field(min_length=1, max_length=96)


class ForgeOperationOutcome(StrictModel):
    accepted: bool
    reason: str = Field(min_length=1, max_length=120)
    repo_id: str | None = Field(default=None, max_length=96)
    commit_id: str | None = Field(default=None, max_length=96)
    artifact_id: str | None = Field(default=None, max_length=96)
