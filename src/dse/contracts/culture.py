from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextEntryRecord(MutableModel):
    entry_id: str = Field(min_length=1, max_length=120)
    created_tick: int = Field(ge=0)
    creator_agent_id: str = Field(min_length=1, max_length=120)
    creator_generation: int = Field(ge=0)
    source_event_id: str = Field(min_length=1)
    source_action_id: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=65_536)
    content_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    content_bytes: int = Field(ge=1, le=65_536)
    parent_entry_ids: list[str] = Field(default_factory=list, max_length=64)


class TextCultureState(MutableModel):
    entries: dict[str, TextEntryRecord] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)


class SocialMessageRecord(MutableModel):
    message_id: str = Field(min_length=1, max_length=120)
    created_tick: int = Field(ge=0)
    sender_agent_id: str = Field(min_length=1, max_length=120)
    sender_generation: int = Field(ge=0)
    source_event_id: str = Field(min_length=1)
    source_action_id: str = Field(min_length=1, max_length=240)
    channel: Literal[
        "direct",
        "issue",
        "pull_request",
        "thread_message",
    ]
    visibility: Literal["direct", "public"]
    recipient_agent_id: str | None = Field(default=None, max_length=120)
    recipient_generation: int | None = Field(default=None, ge=0)
    thread_id: str | None = Field(default=None, max_length=120)
    subject: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=32_768)
    content_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    content_bytes: int = Field(ge=1, le=32_768)


class SocialThreadRecord(MutableModel):
    thread_id: str = Field(min_length=1, max_length=120)
    kind: Literal["issue", "pull_request"]
    created_tick: int = Field(ge=0)
    creator_agent_id: str = Field(min_length=1, max_length=120)
    creator_generation: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)
    opening_message_id: str = Field(min_length=1, max_length=120)
    message_ids: list[str] = Field(default_factory=list, max_length=4096)


class SocialWorldState(MutableModel):
    messages: dict[str, SocialMessageRecord] = Field(default_factory=dict)
    message_order: list[str] = Field(default_factory=list)
    threads: dict[str, SocialThreadRecord] = Field(default_factory=dict)
    thread_order: list[str] = Field(default_factory=list)
