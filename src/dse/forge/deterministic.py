from dse.contracts.forge import (
    ForgeCommitArtifactRequest,
    ForgeCommitArtifactResult,
    ForgeCreateRepositoryRequest,
    ForgeCreateRepositoryResult,
)
from dse.engine.hashing import state_hash


class DeterministicMemoryForgeProvider:
    provider_name = "deterministic-memory"

    async def create_repository(
        self,
        request: ForgeCreateRepositoryRequest,
    ) -> ForgeCreateRepositoryResult:
        digest = state_hash(
            {
                "kind": "forge-repository",
                "experiment_id": request.experiment_id,
                "name": request.name,
            }
        )
        return ForgeCreateRepositoryResult(
            provider=self.provider_name,
            repo_id=f"repo-{digest[:32]}",
        )

    async def commit_artifact(
        self,
        request: ForgeCommitArtifactRequest,
    ) -> ForgeCommitArtifactResult:
        commit_digest = state_hash(
            {
                "kind": "forge-commit",
                **request.model_dump(mode="json"),
            }
        )
        commit_id = f"commit-{commit_digest[:40]}"

        artifact_digest = state_hash(
            {
                "kind": "forge-artifact",
                "commit_id": commit_id,
                "repo_id": request.repo_id,
                "path": request.path,
                "content_sha256": request.content_sha256,
            }
        )
        return ForgeCommitArtifactResult(
            provider=self.provider_name,
            commit_id=commit_id,
            artifact_id=f"artifact-{artifact_digest[:40]}",
        )
