"""Keep the sensitive M21 science gate separate from the public CI job."""

import unittest
from pathlib import Path

import yaml


class TestM21ManualWorkflowContract(unittest.TestCase):
    def test_manual_preflight_has_no_background_or_secret_authority(self) -> None:
        path = Path(".github/workflows/m21-manual-runtime.yml")
        content = path.read_text(encoding="utf-8")
        # PyYAML YAML 1.1 can parse "on" as True; both forms are checked.
        workflow = yaml.safe_load(content)
        self.assertEqual(set(workflow.get("on", workflow.get(True))), {"workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(len(workflow["jobs"]), 1)
        job = workflow["jobs"]["public-tls-gvisor-preflight"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(job["timeout-minutes"], "35")
        self.assertEqual(job["env"]["DSE_M21_MANUAL_PROBE"], "1")
        self.assertIn(
            "github.actor_id != '285490571'",
            job["steps"][0]["if"],
        )
        self.assertIn(
            "github.ref != 'refs/heads/main'",
            job["steps"][0]["if"],
        )
        self.assertNotIn("secrets.", content)
        self.assertNotIn("id-token: write", content)
        self.assertNotIn("pull_request:", content)
        self.assertNotIn("schedule:", content)
