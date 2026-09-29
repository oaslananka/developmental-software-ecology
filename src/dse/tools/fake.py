from dse.contracts.agent import ActionIntentRecord
from dse.contracts.tool import ToolExecutionResult
from dse.engine.hashing import state_hash


class DeterministicFakeExecutor:
    executor_name = "deterministic-fake"

    def policy_rejection_reason(
        self,
        action: ActionIntentRecord,
    ) -> str | None:
        return None

    async def execute(
        self,
        action: ActionIntentRecord,
    ) -> ToolExecutionResult:
        if action.kind == "inspect_workspace":
            result_type = "workspace_inspection"
            summary = "Simulated workspace inspection completed."
            result_data = {
                "simulated": True,
                "target": action.target,
                "entries": [
                    "README.md",
                    "docs",
                    "experiments",
                    "src",
                    "tests",
                ],
            }
        elif action.kind == "draft_artifact":
            content = action.draft_content or ""
            result_type = "artifact_draft_preview"
            summary = "Simulated artifact draft preview generated without writing a file."
            result_data = {
                "simulated": True,
                "target": action.target,
                "content_hash": state_hash({"content": content}),
                "content_bytes": len(content.encode("utf-8")),
            }
        elif action.kind == "run_validation":
            result_type = "validation_report"
            summary = "Simulated validation completed successfully."
            result_data = {
                "simulated": True,
                "target": action.target,
                "passed": True,
                "checks": [
                    "schema",
                    "deterministic-policy",
                ],
            }
        else:
            raise ValueError(f"Unsupported fake action kind: {action.kind}")

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
