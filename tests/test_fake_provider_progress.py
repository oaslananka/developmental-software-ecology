"""Regression coverage for v0.4 deterministic fake zero-progress decisions."""

import asyncio
import unittest

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


class TestFakeProviderProgress(unittest.TestCase):
    def test_exact_zero_progress_proposes_workspace_inspection(self) -> None:
        for zero in (0.0, -0.0):
            with self.subTest(progress=zero):
                decision = _decision_at_progress(zero)
                self.assertEqual(decision.decision, "propose_action")
                self.assertIsNotNone(decision.action)
                self.assertEqual(decision.action.kind, "inspect_workspace")

    def test_small_positive_progress_bypasses_zero_progress_action(self) -> None:
        decision = _decision_at_progress(1e-7)
        self.assertEqual(decision.decision, "update_goal")
        self.assertIsNone(decision.action)

    def test_explicit_null_progress_is_not_coerced_to_zero(self) -> None:
        with self.assertRaises(TypeError):
            _decision_at_progress(None)
