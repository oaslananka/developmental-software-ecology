"""Manual, genuinely isolated TLS/gVisor integration with PUBLIC synthetic data.

This test deliberately does NOT read the frozen M21 suite. It is only enabled
on the owner-dispatched ephemeral GitHub Actions runner.
"""

import asyncio
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import tempfile
import unittest

import httpx

from dse.contracts.experiment import load_manifest
from dse.engine.hidden_evaluator import evaluate_hidden_functional_culture
from dse.engine.sandbox_admission import REQUIRED_SANDBOX_CHECKS
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.evaluator.gvisor_docker import GVisorDockerBackend
from dse.evaluator.http import ExternalEvaluatorClient
from dse.evaluator.service import EvaluatorServiceApplication, EvaluatorTLSServer
from dse.evaluator.suite_store import FilesystemHiddenSuiteStore
from dse.evaluator.worker import HardenedEvaluatorWorker
from dse.experiments.conditions import assess_condition_runtime_readiness


_MANIFEST = Path("experiments/v0_1/conditions/e-executable-culture.yaml")
_SUITE_ID = "m21-public-only-tls-gvisor-fixture"
_PUBLIC_SOURCE = b"""import json

# This fixture is intentionally public and contains no frozen hidden cases.
artifacts = DSE_REQUEST["snapshot"]["artifacts"]
passed = int(any("PUBLIC_ONLY_MARKER" in a["content"] for a in artifacts))
print(json.dumps({"passed_cases": passed, "failed_cases": 1 - passed, "total_cases": 1}))
"""


@unittest.skipUnless(
    os.environ.get("DSE_M21_MANUAL_PROBE") == "1",
    "only the manual public gVisor TLS preflight enables this integration",
)
class TestManualPublicTlsGvisor(unittest.TestCase):
    def test_real_tls_filesystem_store_worker_and_gvisor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dse-public-m21-") as dirname:
            self._exercise(Path(dirname))

    def _exercise(self, root: Path) -> None:
        manifest = load_manifest(_MANIFEST)
        opportunity = manifest.functional_opportunity
        self.assertIsNotNone(opportunity.spec_id)
        self.assertIsNotNone(opportunity.spec_sha256)
        self.assertEqual(opportunity.aggregation, "population_any")

        suite_hash = hashlib.sha256(_PUBLIC_SOURCE).hexdigest()
        evaluation = manifest.evaluation.model_copy(
            update={"suite_id": _SUITE_ID, "suite_hash": suite_hash}
        )
        public_manifest = manifest.model_copy(update={"evaluation": evaluation})

        suite_path = root / "public-only-suite" / _SUITE_ID
        suite_path.mkdir(parents=True, mode=0o700)
        (suite_path / "suite.bundle").write_bytes(_PUBLIC_SOURCE)
        (suite_path / "manifest.json").write_text(
            json.dumps(
                {
                    "total_cases": 1,
                    "opportunity_spec_id": opportunity.spec_id,
                    "opportunity_spec_hash": opportunity.spec_sha256,
                    "opportunity_aggregation": opportunity.aggregation,
                }
            ),
            encoding="utf-8",
        )

        cert = root / "public-local-tls-cert.pem"
        key = root / "public-local-tls-key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-sha256", "-nodes", "-days", "1",
                "-keyout", str(key), "-out", str(cert),
                "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
                "-addext", "basicConstraints=critical,CA:TRUE",
            ],
            check=True,
            capture_output=True,
        )
        key.chmod(0o600)

        backend = GVisorDockerBackend(
            runtime_image=os.environ.get(
                "DSE_GVISOR_RUNTIME_IMAGE",
                "gcr.io/distroless/python3-debian13:nonroot",
            )
        )
        worker = HardenedEvaluatorWorker(
            service_id="m21-public-only-tls-gvisor",
            runner_version="m21-public-probe-v1",
            worker_build_sha256=hashlib.sha256(
                Path("src/dse/evaluator/worker.py").read_bytes()
            ).hexdigest(),
            suite_store=FilesystemHiddenSuiteStore(
                root / "public-only-suite"
            ),
            backend=backend,
        )
        token = secrets.token_urlsafe(48)
        application = EvaluatorServiceApplication(
            worker=worker,
            authorization_value=token,
        )
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        server_context.load_cert_chain(certfile=cert, keyfile=key)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        server = EvaluatorTLSServer(
            application=application,
            ssl_context=server_context,
            request_timeout_seconds=30.0,
            operation_timeout_seconds=180.0,
            max_connections=2,
            max_concurrent_operations=1,
        )
        asyncio.run(
            self._round_trip(server, cert, port, token, public_manifest)
        )

    async def _round_trip(
        self, server, cert: Path, port: int, token: str, manifest
    ) -> None:
        task = asyncio.create_task(
            server.serve_forever("127.0.0.1", port)
        )
        try:
            await asyncio.sleep(0.25)
            async with httpx.AsyncClient(
                verify=str(cert),
                timeout=180.0,
                follow_redirects=False,
            ) as transport:
                # The true TLS entrypoint must reject missing credentials.
                unauthenticated = await transport.post(
                    f"https://localhost:{port}/v1/handshake",
                    json={},
                )
                self.assertEqual(unauthenticated.status_code, 401)
                client = ExternalEvaluatorClient(
                    base_url=f"https://localhost:{port}",
                    bearer_token=token,
                    timeout_seconds=180.0,
                    client=transport,
                )
                session = await client.open_session(manifest)
                self.assertEqual(
                    session.attestation.evidence_kind,
                    "runtime-measured",
                )
                checks = {
                    c.name: c.passed
                    for c in session.attestation.checks
                }
                self.assertTrue(
                    all(checks.get(name) for name in REQUIRED_SANDBOX_CHECKS)
                )
                self.assertEqual(len(session.runtime_build_sha256), 64)

                world = create_world(manifest)
                initial_hash = world_state_hash(world)
                outcome = await evaluate_hidden_functional_culture(
                    world,
                    manifest,
                    attestation=session.attestation,
                    runner=session,
                )
                self.assertTrue(outcome.completed)
                self.assertIsNotNone(outcome.report)
                self.assertEqual(outcome.report.total_cases, 1)
                self.assertEqual(outcome.report.passed_cases, 0)
                self.assertEqual(outcome.report.failed_cases, 1)
                self.assertEqual(
                    outcome.report.attestation_evidence_kind,
                    "runtime-measured",
                )
                self.assertEqual(
                    outcome.report.runtime_build_sha256,
                    session.runtime_build_sha256,
                )
                self.assertEqual(world_state_hash(world), initial_hash)
                readiness = assess_condition_runtime_readiness(
                    manifest, outcome.report
                )
                self.assertTrue(readiness.research_runtime_ready)
                # Readiness above is for THIS PUBLIC FIXTURE only, never the
                # frozen M21 private suite; it cannot clear the M21 gate.
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
