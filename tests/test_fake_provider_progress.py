"""Regression coverage for v0.4 deterministic fake zero-progress decisions."""

import asyncio

import pytest

from dse.contracts.model import ModelRequest
from dse.providers.fake import DeterministicFakeProvider


def _decision_at_progress(progress: float | None):
    request = ModelRequest(
        call_id="progress-boundary",
        experiment_id="fake-progress-branch",
        agent_id="agent-0001",
        world_tick=0,
        response_schema="CognitionDecision/v0.4",
        context={
            "active_goal": {"progress": progress},
            "action_generation_enabled": True,
            "action_proposals_remaining": 3,
        },
    )
    return asyncio.run(DeterministicFakeProvider().generate(request)).decision


@pytest.mark.parametrize("zero", [0.0, -0.0])
def test_exact_zero_progress_proposes_workspace_inspection(zero: float) -> None:
    decision = _decision_at_progress(zero)
    assert decision.decision == "propose_action"
    assert decision.action is not None
    assert decision.action.kind == "inspect_workspace"


def test_small_positive_progress_does_not_trigger_zero_progress_action() -> None:
    decision = _decision_at_progress(1e-7)
    assert decision.decision == "update_goal"
    assert decision.action is None


def test_explicit_null_progress_is_not_silently_treated_as_zero() -> None:
    with pytest.raises(TypeError):
        _decision_at_progress(None)
