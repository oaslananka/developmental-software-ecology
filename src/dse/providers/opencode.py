import json

import httpx
from pydantic import ValidationError

from dse.contracts.model import (
    CognitionDecision,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from dse.engine.hashing import state_hash


class ProviderResponseError(ValueError):
    """Raised when a provider response cannot be validated safely."""


class OpenCodeProvider:
    provider_name = "opencode"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://opencode.ai/inference/openai/v1",
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def generate(self, request: ModelRequest) -> ModelResponse:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": (
                820
                if request.response_schema == "CognitionDecision/v0.6"
                else 700
                if request.response_schema == "CognitionDecision/v0.5"
                else 520
                if request.response_schema == "CognitionDecision/v0.4"
                else 420
                if request.response_schema == "CognitionDecision/v0.3"
                else 320
                if request.response_schema == "CognitionDecision/v0.2"
                else 160
            ),
            "messages": [
                {
                    "role": "system",
                    "content": self._system_prompt(request),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "agent_id": request.agent_id,
                            "world_tick": request.world_tick,
                            "context": request.context,
                        },
                        sort_keys=True,
                    ),
                },
            ],
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        response_data = await self._post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            payload=payload,
        )

        try:
            raw_content = response_data["choices"][0]["message"]["content"]
            decision = CognitionDecision.model_validate_json(raw_content)
        except (KeyError, IndexError, TypeError, ValidationError) as error:
            raise ProviderResponseError(
                "OpenCode returned an invalid cognition response"
            ) from error

        if (
            request.response_schema == "CognitionDecision/v0.1"
            and decision.decision
            in {
                "propose_goal",
                "update_goal",
                "complete_goal",
                "abandon_goal",
                "propose_action",
            }
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.1 does not allow goal/action decisions"
            )

        if (
            request.response_schema == "CognitionDecision/v0.2"
            and decision.decision
            in {
                "update_goal",
                "complete_goal",
                "abandon_goal",
                "propose_action",
            }
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.2 does not allow lifecycle/action decisions"
            )

        if (
            request.response_schema == "CognitionDecision/v0.3"
            and decision.decision == "propose_action"
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.3 does not allow action proposals"
            )

        if (
            request.response_schema == "CognitionDecision/v0.4"
            and decision.decision == "propose_action"
            and decision.action is not None
            and (
                decision.action.kind.startswith("forge_")
                or decision.action.kind.startswith("text_")
                or decision.action.kind.startswith("social_")
            )
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.4 does not allow culture action proposals"
            )

        if (
            request.response_schema == "CognitionDecision/v0.5"
            and decision.decision == "propose_action"
            and decision.action is not None
            and (
                decision.action.kind.startswith("text_")
                or decision.action.kind.startswith("social_")
            )
        ):
            raise ProviderResponseError(
                "CognitionDecision/v0.5 does not allow text/social action proposals"
            )

        usage_data = response_data.get("usage") or {}
        usage = ModelUsage(
            input_tokens=int(usage_data.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_data.get("completion_tokens", 0) or 0),
        )

        request_hash = state_hash(request.model_dump(mode="json"))
        response_material = {
            "provider": self.provider_name,
            "model": response_data.get("model", self.model),
            "decision": decision.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
            "request_hash": request_hash,
        }

        return ModelResponse(
            call_id=request.call_id,
            provider=self.provider_name,
            model=str(response_data.get("model", self.model)),
            model_version=None,
            decision=decision,
            usage=usage,
            request_hash=request_hash,
            response_hash=state_hash(response_material),
        )

    def _system_prompt(self, request: ModelRequest) -> str:
        if request.response_schema == "CognitionDecision/v0.6":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", "propose_goal", "update_goal", '
                '"complete_goal", "abandon_goal", or "propose_action". '
                "For propose_action include action with kind, summary, target, "
                "rationale, expected_value, estimated_cost, draft_content, repo_id, "
                "parent_artifact_ids, expected_parent_commit_id, parent_text_entry_ids. "
                "Allowed action kinds are inspect_workspace, draft_artifact, run_validation, "
                "functional_submit, "
                "forge_create_repository, forge_inspect_repository, forge_publish_artifact, "
                "text_publish, social_send_message, social_open_issue, social_open_pr, "
                "social_post_message. "
                "Use only action families whose context flags are enabled and whose "
                "operation budget is positive. "
                "text_publish requires draft_content; target is a short title; "
                "parent_text_entry_ids may cite visible text_culture entries. "
                "social_send_message requires draft_content and target must be a visible "
                "social_peers agent_id. social_open_issue/social_open_pr require "
                "draft_content and use target as a public thread title. "
                "social_post_message requires draft_content and target must be a visible "
                "social_threads thread_id. "
                "functional_submit requires draft_content and target must match "
                "functional_opportunity.submission_path; use it to export the current "
                "generation's bounded executable capability for the public environmental "
                "opportunity. "
                "Forge action fields follow v0.5 semantics. Non-Forge actions must not use "
                "repo_id, parent_artifact_ids, or expected_parent_commit_id. "
                "Non-text actions must keep parent_text_entry_ids empty. "
                "Treat text_culture and public issue/PR threads as external world evidence. "
                "Direct messages are generation-scoped and must not be assumed inherited "
                "after turnover. Do not include markdown or extra fields."
            )

        if request.response_schema == "CognitionDecision/v0.5":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", "propose_goal", "update_goal", '
                '"complete_goal", "abandon_goal", or "propose_action". '
                "For propose_action include action with kind, summary, target, "
                "rationale, expected_value, estimated_cost, draft_content, repo_id, "
                "parent_artifact_ids, expected_parent_commit_id. "
                "Allowed action kinds are inspect_workspace, draft_artifact, "
                "run_validation, functional_submit, forge_create_repository, "
                "forge_inspect_repository, "
                "forge_publish_artifact. "
                "forge_create_repository: target is a new repository name; draft_content "
                "and repo_id must be null; parent_artifact_ids must be empty; "
                "expected_parent_commit_id must be null. "
                "forge_inspect_repository: target is an existing repo_id; draft_content "
                "and repo_id must be null; parent_artifact_ids must be empty; "
                "expected_parent_commit_id must be null. "
                "forge_publish_artifact: target is a relative artifact path; repo_id and "
                "draft_content are required; parent_artifact_ids may cite existing public "
                "artifacts; expected_parent_commit_id may pin the repository head. "
                "draft_artifact and functional_submit require draft_content. "
                "functional_submit target must match "
                "functional_opportunity.submission_path when that opportunity is enabled. "
                "Non-publish actions must not use "
                "repo_id, parent_artifact_ids, or expected_parent_commit_id. "
                "Forge action proposals are intents only; the Forge Action Broker applies "
                "budgets, provenance and world-state validation. "
                "Only propose_action when active_goal exists and action_proposals_remaining "
                "is greater than zero. Only propose Forge actions when forge_action_enabled "
                "is true and forge_operations_remaining is greater than zero. "
                "Use forge_world and forge_results as observed public evidence. "
                "Use goal/goal_update/goal_closure exactly as required by v0.3 goal "
                "decisions. Do not include markdown or extra fields."
            )

        if request.response_schema == "CognitionDecision/v0.4":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", "propose_goal", "update_goal", '
                '"complete_goal", "abandon_goal", or "propose_action". '
                "For propose_action, include only action with kind, summary, target, "
                "rationale, expected_value, estimated_cost, draft_content, repo_id, "
                "parent_artifact_ids, expected_parent_commit_id. "
                "Allowed action kinds are inspect_workspace, draft_artifact, "
                "run_validation, functional_submit. "
                "draft_artifact and functional_submit require draft_content; "
                "all other kinds require it to be null. functional_submit target must match "
                "functional_opportunity.submission_path when that opportunity is enabled. "
                "repo_id and expected_parent_commit_id must be null and "
                "parent_artifact_ids must be empty in v0.4. "
                "Action proposals are intents only and are NOT executed directly. "
                "Only propose_action when an active_goal exists and "
                "action_proposals_remaining is greater than zero. "
                "Use goal/goal_update/goal_closure exactly as required by v0.3 goal decisions. "
                "Do not include markdown or extra fields."
            )

        if request.response_schema == "CognitionDecision/v0.3":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", "propose_goal", "update_goal", '
                '"complete_goal", or "abandon_goal". '
                'Always include "decision", "reason_summary", "confidence", and "focus". '
                'For propose_goal, include only "goal" with title, description, '
                "motivation_summary, expected_value, estimated_cost, confidence. "
                'For update_goal, include only "goal_update" with progress, '
                "progress_summary, confidence. Progress must increase and remain below 1.0. "
                'For complete_goal or abandon_goal, include only "goal_closure" '
                "with summary and confidence. "
                "For idle or observe, goal, goal_update, goal_closure, and action must be null. "
                "Only propose a goal when context.active_goal is null. "
                "Only update/complete/abandon when context.active_goal is present. "
                "Do not include markdown or extra fields."
            )

        if request.response_schema == "CognitionDecision/v0.2":
            return (
                "Return exactly one JSON object. Allowed decisions are "
                '"idle", "observe", or "propose_goal". '
                'Fields: "decision", "reason_summary", "confidence", "focus", "goal", '
                '"goal_update", "goal_closure", "action". '
                "When decision is propose_goal, goal must be an object with "
                '"title", "description", "motivation_summary", "expected_value", '
                '"estimated_cost", and "confidence". All numeric values must be 0..1. '
                "For idle or observe, goal must be null. "
                "goal_update, goal_closure, and action must always be null in v0.2. "
                "Only propose a goal when context.goal_generation_enabled is true and "
                "context.active_goal is null. Do not include markdown or extra fields."
            )

        return (
            "Return exactly one JSON object matching this schema: "
            '{"decision":"idle|observe","reason_summary":"short text",'
            '"confidence":0.0,"focus":"optional short text or null",'
            '"goal":null,"goal_update":null,"goal_closure":null,"action":null}. '
            "Do not include markdown or additional fields."
        )

    async def _post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict,
    ) -> dict:
        if self._client is not None:
            response = await self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
