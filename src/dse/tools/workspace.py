import hashlib
import os
from pathlib import Path

from dse.contracts.agent import ActionIntentRecord
from dse.contracts.tool import ToolExecutionResult
from dse.engine.hashing import state_hash


class EphemeralLocalWorkspaceExecutor:
    executor_name = "ephemeral-local-workspace"

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        max_file_bytes: int = 16_384,
        max_entries: int = 128,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        root = Path(workspace_root)
        root.mkdir(parents=True, exist_ok=True)

        self.workspace_root = root.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_entries = max_entries

    def policy_rejection_reason(
        self,
        action: ActionIntentRecord,
    ) -> str | None:
        if action.kind == "inspect_workspace":
            return None

        target = self._resolve_target(action.target)
        if target is None:
            return "workspace_escape"

        if action.kind == "draft_artifact":
            content = (action.draft_content or "").encode("utf-8")
            if len(content) > self.max_file_bytes:
                return "content_too_large"

            parent = target.parent
            resolved_parent = parent.resolve(strict=False)
            if not self._is_within_root(resolved_parent):
                return "workspace_escape"

        return None

    async def execute(
        self,
        action: ActionIntentRecord,
    ) -> ToolExecutionResult:
        if action.kind == "inspect_workspace":
            return self._inspect_workspace(action)
        if action.kind == "draft_artifact":
            return self._write_artifact(action)
        if action.kind == "run_validation":
            return self._validate_file(action)
        raise ValueError(f"Unsupported workspace action kind: {action.kind}")

    def _inspect_workspace(
        self,
        action: ActionIntentRecord,
    ) -> ToolExecutionResult:
        entries: list[dict[str, object]] = []
        truncated = False

        for current_root, dirnames, filenames in os.walk(
            self.workspace_root,
            followlinks=False,
        ):
            dirnames.sort()
            filenames.sort()

            current = Path(current_root)
            safe_dirnames: list[str] = []
            for dirname in dirnames:
                path = current / dirname
                relative = path.relative_to(self.workspace_root).as_posix()

                if path.is_symlink():
                    entries.append({
                        "path": relative,
                        "type": "symlink",
                    })
                else:
                    safe_dirnames.append(dirname)
                    entries.append({
                        "path": relative,
                        "type": "directory",
                    })

                if len(entries) >= self.max_entries:
                    truncated = True
                    break

            dirnames[:] = safe_dirnames
            if truncated:
                break

            for filename in filenames:
                path = current / filename
                relative = path.relative_to(self.workspace_root).as_posix()

                if path.is_symlink():
                    entry = {
                        "path": relative,
                        "type": "symlink",
                    }
                else:
                    stat = path.stat()
                    entry = {
                        "path": relative,
                        "type": "file",
                        "bytes": stat.st_size,
                    }
                entries.append(entry)

                if len(entries) >= self.max_entries:
                    truncated = True
                    break

            if truncated:
                break

        result_data = {
            "simulated": False,
            "workspace": ".",
            "entries": entries,
            "entry_count": len(entries),
            "truncated": truncated,
            "max_entries": self.max_entries,
        }
        summary = (
            f"Workspace inspection returned {len(entries)} bounded entries"
            + (" (truncated)." if truncated else ".")
        )
        return self._result(
            action=action,
            result_type="workspace_inspection",
            summary=summary,
            result_data=result_data,
        )

    def _write_artifact(
        self,
        action: ActionIntentRecord,
    ) -> ToolExecutionResult:
        target = self._require_target(action.target)
        content = action.draft_content or ""
        encoded = content.encode("utf-8")

        if len(encoded) > self.max_file_bytes:
            raise ValueError("draft content exceeds max_file_bytes")

        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve(strict=True)
        if not self._is_within_root(resolved_parent):
            raise ValueError("target parent escaped workspace after mkdir")

        existed = target.exists()
        if existed and not target.is_file():
            raise ValueError("artifact target must be a regular file")

        target.write_bytes(encoded)
        resolved_target = target.resolve(strict=True)
        if not self._is_within_root(resolved_target):
            raise ValueError("written artifact escaped workspace")

        content_hash = hashlib.sha256(encoded).hexdigest()
        result_data = {
            "simulated": False,
            "target": target.relative_to(self.workspace_root).as_posix(),
            "created": not existed,
            "bytes": len(encoded),
            "sha256": content_hash,
        }
        return self._result(
            action=action,
            result_type="artifact_written",
            summary="Artifact written inside the ephemeral local workspace.",
            result_data=result_data,
        )

    def _validate_file(
        self,
        action: ActionIntentRecord,
    ) -> ToolExecutionResult:
        target = self._require_target(action.target)

        exists = target.exists()
        regular_file = exists and target.is_file()
        size_bytes = target.stat().st_size if regular_file else 0
        within_size_limit = regular_file and size_bytes <= self.max_file_bytes

        utf8_decodable = False
        content_hash = None
        nonempty = False

        if regular_file and within_size_limit:
            data = target.read_bytes()
            nonempty = bool(data)
            content_hash = hashlib.sha256(data).hexdigest()
            try:
                data.decode("utf-8")
                utf8_decodable = True
            except UnicodeDecodeError:
                utf8_decodable = False

        passed = bool(
            regular_file
            and within_size_limit
            and utf8_decodable
            and nonempty
        )

        result_data = {
            "simulated": False,
            "target": action.target,
            "passed": passed,
            "checks": {
                "exists": exists,
                "regular_file": regular_file,
                "within_size_limit": within_size_limit,
                "utf8_decodable": utf8_decodable,
                "nonempty": nonempty,
            },
            "bytes": size_bytes,
            "sha256": content_hash,
        }

        return self._result(
            action=action,
            result_type="validation_report",
            summary=(
                "Static file validation passed."
                if passed
                else "Static file validation failed."
            ),
            result_data=result_data,
        )

    def _resolve_target(self, target: str) -> Path | None:
        normalized = target.replace("\\", "/")
        candidate = (self.workspace_root / normalized).resolve(strict=False)
        if not self._is_within_root(candidate):
            return None
        return candidate

    def _require_target(self, target: str) -> Path:
        resolved = self._resolve_target(target)
        if resolved is None:
            raise ValueError("target escapes workspace root")
        return resolved

    def _is_within_root(self, path: Path) -> bool:
        return path == self.workspace_root or self.workspace_root in path.parents

    def _result(
        self,
        *,
        action: ActionIntentRecord,
        result_type: str,
        summary: str,
        result_data: dict[str, object],
    ) -> ToolExecutionResult:
        material = {
            "executor": self.executor_name,
            "action_id": action.action_id,
            "result_type": result_type,
            "summary": summary,
            "result_data": result_data,
        }
        return ToolExecutionResult(
            executor=self.executor_name,
            success=True,
            result_type=result_type,
            summary=summary,
            result_data=result_data,
            result_hash=state_hash(material),
        )
