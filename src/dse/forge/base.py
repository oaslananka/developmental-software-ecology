from typing import Protocol

from dse.contracts.forge import (
    ForgeCommitArtifactRequest,
    ForgeCommitArtifactResult,
    ForgeCreateRepositoryRequest,
    ForgeCreateRepositoryResult,
)


class ForgeProvider(Protocol):
    provider_name: str

    async def create_repository(
        self,
        request: ForgeCreateRepositoryRequest,
    ) -> ForgeCreateRepositoryResult:
        ...

    async def commit_artifact(
        self,
        request: ForgeCommitArtifactRequest,
    ) -> ForgeCommitArtifactResult:
        ...
