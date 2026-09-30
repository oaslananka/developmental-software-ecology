import asyncio
import hashlib
import ssl
from pathlib import Path

import httpx

from dse.contracts.evaluator_worker import WorkerEvaluationAggregate
from dse.contracts.experiment import load_manifest
from dse.contracts.sandbox import SandboxAttestation, SandboxCheck
from dse.engine.hidden_evaluator import evaluate_hidden_functional_culture
from dse.engine.sandbox_admission import (
    REQUIRED_SANDBOX_CHECKS,
    sandbox_policy_hash_for,
)
from dse.engine.snapshot import world_state_hash
from dse.engine.world import create_world
from dse.evaluator.http import ExternalEvaluatorClient
from dse.evaluator.service import (
    EvaluatorServiceApplication,
    EvaluatorServiceConfig,
    EvaluatorTLSServer,
)
from dse.evaluator.suite_store import HiddenSuiteBundle, HiddenSuiteError
from dse.evaluator.worker import HardenedEvaluatorWorker
from dse.experiments.conditions import assess_condition_runtime_readiness


E_MANIFEST = Path("experiments/v0_1/conditions/e-executable-culture.yaml")
AUTH_FIXTURE = hashlib.sha256(
    b"dse-m18-3-public-auth-fixture"
).hexdigest()
WORKER_BUILD = "1" * 64
RUNTIME_BUILD = "2" * 64
SUITE_PAYLOAD = b"M18_3_PUBLIC_SERVICE_FIXTURE"


def _check(
    condition: bool,
    message: str = "test condition failed",
) -> None:
    if not condition:
        raise AssertionError(message)


class MemorySuiteStore:
    def __init__(self, bundle: HiddenSuiteBundle) -> None:
        self.bundle = bundle

    def load(self, suite_id: str) -> HiddenSuiteBundle:
        if suite_id != self.bundle.suite_id:
            raise HiddenSuiteError("private suite path detail must stay hidden")
        return self.bundle


class RecordingBackend:
    backend = "external-hardened"
    backend_version = "m18-3-service-fixture-1"
    runtime_build_sha256 = RUNTIME_BUILD

    async def attest(self, policy, policy_hash: str) -> SandboxAttestation:
        assert policy_hash == sandbox_policy_hash_for(policy)
        return SandboxAttestation(
            attestation_id="m18-3-runtime-attestation",
            evidence_kind="runtime-measured",
            backend=self.backend,
            backend_version=self.backend_version,
            policy_hash=policy_hash,
            checks=[
                SandboxCheck(
                    name=name,
                    passed=True,
                    detail="measured service integration fixture",
                )
                for name in REQUIRED_SANDBOX_CHECKS
            ],
        )

    async def evaluate(
        self,
        request,
        suite: HiddenSuiteBundle,
    ) -> WorkerEvaluationAggregate:
        assert request.plan.suite_hash == suite.suite_hash
        return WorkerEvaluationAggregate(
            passed_cases=1,
            failed_cases=0,
            total_cases=1,
            duration_ms=7,
        )


class ApplicationTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        application: EvaluatorServiceApplication,
    ) -> None:
        self.application = application

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        body = await request.aread()
        response = await self.application.handle(
            method=request.method,
            path=request.url.path,
            headers=dict(request.headers),
            body=body,
        )
        return httpx.Response(
            response.status,
            headers=dict(response.headers),
            content=response.body,
            request=request,
        )


def _bundle() -> HiddenSuiteBundle:
    return HiddenSuiteBundle(
        suite_id="m18-3-public-service-suite",
        suite_hash=hashlib.sha256(SUITE_PAYLOAD).hexdigest(),
        total_cases=1,
        payload=SUITE_PAYLOAD,
    )


def _manifest(bundle: HiddenSuiteBundle):
    manifest = load_manifest(E_MANIFEST)
    evaluation = manifest.evaluation.model_copy(
        update={
            "suite_id": bundle.suite_id,
            "suite_hash": bundle.suite_hash,
        }
    )
    return manifest.model_copy(
        update={"evaluation": evaluation}
    )


def _application(
    *,
    max_request_bytes: int = 2_097_152,
) -> EvaluatorServiceApplication:
    bundle = _bundle()
    worker = HardenedEvaluatorWorker(
        service_id="m18-3-service-fixture",
        runner_version="m18-3-service-test-1",
        worker_build_sha256=WORKER_BUILD,
        suite_store=MemorySuiteStore(bundle),
        backend=RecordingBackend(),
    )
    return EvaluatorServiceApplication(
        worker=worker,
        bearer_token=AUTH_FIXTURE,
        max_request_bytes=max_request_bytes,
    )


def test_service_rejects_unauthorized_requests_without_echoing_body() -> None:
    application = _application()
    secret_marker = b"PRIVATE_SUITE_OR_REQUEST_DETAIL"

    response = asyncio.run(
        application.handle(
            method="POST",
            path="/v1/handshake",
            headers={"Content-Type": "application/json"},
            body=secret_marker,
        )
    )

    assert response.status == 401
    assert secret_marker not in response.body
    assert b"unauthorized" in response.body


def test_service_rejects_unbounded_or_invalid_transport_shapes() -> None:
    application = _application(max_request_bytes=1024)
    authorization = f"Bearer {AUTH_FIXTURE}"

    cases = [
        (
            "GET",
            "/v1/handshake",
            {
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            b"{}",
            405,
        ),
        (
            "POST",
            "/v1/unknown",
            {
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            b"{}",
            404,
        ),
        (
            "POST",
            "/v1/handshake",
            {
                "Authorization": authorization,
                "Content-Type": "text/plain",
            },
            b"{}",
            415,
        ),
        (
            "POST",
            "/v1/handshake",
            {
                "Authorization": authorization,
                "Content-Type": "application/json",
                "Transfer-Encoding": "chunked",
            },
            b"{}",
            400,
        ),
        (
            "POST",
            "/v1/handshake",
            {
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            b"x" * 1025,
            413,
        ),
    ]

    for method, path, headers, body, expected in cases:
        response = asyncio.run(
            application.handle(
                method=method,
                path=path,
                headers=headers,
                body=body,
            )
        )
        assert response.status == expected


def test_worker_rejection_is_sanitized() -> None:
    application = _application()

    response = asyncio.run(
        application.handle(
            method="POST",
            path="/v1/handshake",
            headers={
                "Authorization": f"Bearer {AUTH_FIXTURE}",
                "Content-Type": "application/json",
            },
            body=b"""{
                "protocol_version": "0.1",
                "request_id": "bad-suite",
                "experiment_id": "fixture",
                "suite_id": "missing-suite",
                "suite_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "policy": {
                    "backend": "external-hardened",
                    "network_enabled": false,
                    "host_mounts_enabled": false,
                    "secrets_enabled": false,
                    "shell_enabled": false,
                    "cpu_seconds": 2,
                    "memory_mb": 256,
                    "pids_max": 32,
                    "disk_mb": 64,
                    "output_bytes": 65536,
                    "wall_timeout_seconds": 5
                },
                "policy_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            }""",
        )
    )

    assert response.status == 422
    assert b"evaluation_rejected" in response.body
    assert b"private suite path detail" not in response.body
    assert b"missing-suite" not in response.body


def test_external_client_round_trips_through_service_application() -> None:
    bundle = _bundle()
    manifest = _manifest(bundle)
    application = _application()

    async def exercise() -> None:
        transport = ApplicationTransport(application)
        async with httpx.AsyncClient(
            transport=transport,
        ) as http_client:
            client = ExternalEvaluatorClient(
                base_url="https://evaluator.example",
                bearer_token=AUTH_FIXTURE,
                client=http_client,
            )
            session = await client.open_session(manifest)
            assert session.attestation.evidence_kind == "runtime-measured"

            world = create_world(manifest)
            before_hash = world_state_hash(world)
            outcome = await evaluate_hidden_functional_culture(
                world,
                manifest,
                attestation=session.attestation,
                runner=session,
            )

            assert outcome.completed is True
            assert outcome.report is not None
            assert outcome.report.passed_cases == 1
            assert outcome.report.total_cases == 1
            assert outcome.report.worker_build_sha256 == WORKER_BUILD
            assert outcome.report.runtime_build_sha256 == RUNTIME_BUILD
            assert world_state_hash(world) == before_hash

            readiness = assess_condition_runtime_readiness(
                manifest,
                outcome.report,
            )
            assert readiness.research_runtime_ready is True
            assert readiness.missing_surfaces == []

    asyncio.run(exercise())



def _tls_server(
    application: EvaluatorServiceApplication,
) -> EvaluatorTLSServer:
    context = ssl.SSLContext(
        ssl.PROTOCOL_TLS_SERVER
    )
    context.minimum_version = (
        ssl.TLSVersion.TLSv1_2
    )
    return EvaluatorTLSServer(
        application=application,
        ssl_context=context,
        request_timeout_seconds=1.0,
        operation_timeout_seconds=5.0,
        max_connections=2,
        max_concurrent_operations=1,
        max_header_bytes=4096,
    )


def test_tls_parser_accepts_one_strict_http11_request() -> None:
    application = _application()
    server = _tls_server(application)
    raw = (
        b"POST /v1/handshake HTTP/1.1\r\n"
        b"Host: evaluator.example\r\n"
        + f"Authorization: Bearer {AUTH_FIXTURE}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"Content-Length: 2\r\n\r\n"
    )

    async def parse():
        reader = asyncio.StreamReader(
            limit=4097
        )
        reader.feed_data(raw)
        reader.feed_eof()
        return await server._read_request_head(
            reader
        )

    parsed = asyncio.run(parse())

    assert parsed.method == "POST"
    assert parsed.path == "/v1/handshake"
    assert parsed.content_length == 2
    assert parsed.headers["host"] == "evaluator.example"


def test_tls_parser_rejects_duplicate_headers() -> None:
    application = _application()
    server = _tls_server(application)
    raw = (
        b"POST /v1/handshake HTTP/1.1\r\n"
        b"Host: evaluator.example\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 2\r\n"
        b"Content-Length: 2\r\n\r\n"
    )

    async def parse():
        reader = asyncio.StreamReader(
            limit=4097
        )
        reader.feed_data(raw)
        reader.feed_eof()
        return await server._read_request_head(
            reader
        )

    response = asyncio.run(parse())

    assert response.status == 400
    assert b"duplicate_header" in response.body

def test_service_config_requires_operator_secrets_without_repr_leak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    suite_root = tmp_path / "private-suites"
    suite_root.mkdir()
    cert = tmp_path / "tls.crt"
    key = tmp_path / "tls.key"
    cert.write_text("fixture cert", encoding="utf-8")
    key.write_text("fixture key", encoding="utf-8")

    monkeypatch.setenv("DSE_EVALUATOR_TLS_CERT", str(cert))
    monkeypatch.setenv("DSE_EVALUATOR_TLS_KEY", str(key))
    monkeypatch.setenv("DSE_EVALUATOR_SUITE_ROOT", str(suite_root))
    monkeypatch.setenv("DSE_EVALUATOR_BEARER_TOKEN", AUTH_FIXTURE)
    monkeypatch.setenv(
        "DSE_EVALUATOR_WORKER_BUILD_SHA256",
        WORKER_BUILD,
    )

    config = EvaluatorServiceConfig.from_env()

    assert config.suite_root == suite_root.resolve()
    assert config.worker_build_sha256 == WORKER_BUILD
    assert AUTH_FIXTURE not in repr(config)
