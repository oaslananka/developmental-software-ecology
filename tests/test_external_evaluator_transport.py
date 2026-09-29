import asyncio
import json
from pathlib import Path

import httpx
import pytest

from dse.contracts.evaluation import HiddenEvaluationRequest
from dse.contracts.experiment import load_manifest
from dse.engine.hidden_evaluator import evaluate_hidden_functional_culture
from dse.engine.sandbox_admission import REQUIRED_SANDBOX_CHECKS
from dse.engine.world import create_world
from dse.evaluator.http import (
    ExternalEvaluatorClient,
    ExternalEvaluatorProtocolError,
    ExternalEvaluatorTransportError,
)
from dse.experiments.conditions import assess_condition_runtime_readiness


E_MANIFEST = Path("experiments/v0_1/conditions/e-executable-culture.yaml")
TOKEN = "m17-control-plane-secret"


def _handshake_response(
    payload: dict,
    *,
    suite_hash: str | None = None,
    failed_check: str | None = None,
) -> dict:
    checks = [
        {
            "name": name,
            "passed": name != failed_check,
            "detail": (
                "verified by transport fixture"
                if name != failed_check
                else "intentional fixture failure"
            ),
        }
        for name in REQUIRED_SANDBOX_CHECKS
    ]
    return {
        "protocol_version": "0.1",
        "request_id": payload["request_id"],
        "service_id": "m17-fixture-service",
        "suite_id": payload["suite_id"],
        "suite_hash": suite_hash or payload["suite_hash"],
        "runner_kind": "external-hardened",
        "runner_version": "fixture-runner-1",
        "attestation": {
            "attestation_id": "m17-fixture-attestation",
            "backend": "external-hardened",
            "backend_version": "fixture-sandbox-1",
            "policy_hash": payload["policy_hash"],
            "checks": checks,
        },
    }


def _evaluation_response(
    payload: dict,
    *,
    runner_version: str = "fixture-runner-1",
) -> dict:
    plan = payload["plan"]
    return {
        "request_id": payload["request_id"],
        "evaluation_id": plan["evaluation_id"],
        "plan_hash": payload["plan_hash"],
        "culture_snapshot_hash": plan["culture_snapshot_hash"],
        "suite_hash": plan["suite_hash"],
        "policy_hash": payload["policy_hash"],
        "attestation_id": payload["attestation_id"],
        "backend": payload["backend"],
        "backend_version": payload["backend_version"],
        "runner_kind": "external-hardened",
        "runner_version": runner_version,
        "passed_cases": 1,
        "failed_cases": 0,
        "total_cases": 1,
        "duration_ms": 7,
    }


def test_transport_requires_https_by_default() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        ExternalEvaluatorClient(
            base_url="http://evaluator.example",
            bearer_token=TOKEN,
        )


def test_transport_rejects_credentials_in_base_url() -> None:
    with pytest.raises(ValueError, match="must not contain credentials"):
        ExternalEvaluatorClient(
            base_url="https://user:pass@evaluator.example",
            bearer_token=TOKEN,
        )


def test_handshake_and_evaluation_bind_secret_attestation_and_readiness() -> None:
    manifest = load_manifest(E_MANIFEST)
    captured_bodies: list[dict] = []
    captured_authorization: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured_bodies.append(payload)
        captured_authorization.append(request.headers["Authorization"])

        if request.url.path == "/v1/handshake":
            return httpx.Response(
                200,
                json=_handshake_response(payload),
            )
        if request.url.path == "/v1/evaluate":
            return httpx.Response(
                200,
                json=_evaluation_response(payload),
            )
        return httpx.Response(404, json={"error": "not found"})

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as http_client:
            client = ExternalEvaluatorClient(
                base_url="https://evaluator.example",
                bearer_token=TOKEN,
                client=http_client,
            )
            session = await client.open_session(manifest)
            world = create_world(manifest)
            outcome = await evaluate_hidden_functional_culture(
                world,
                manifest,
                attestation=session.attestation,
                runner=session,
            )
            return session, outcome

    session, outcome = asyncio.run(scenario())

    assert session.runner_kind == "external-hardened"
    assert session.runner_version == "fixture-runner-1"
    assert session.service_id == "m17-fixture-service"
    assert outcome.completed is True
    assert outcome.report is not None
    assert outcome.report.runner_kind == "external-hardened"

    readiness = assess_condition_runtime_readiness(
        manifest,
        outcome.report,
    )
    assert readiness.research_runtime_ready is True
    assert readiness.missing_surfaces == []

    assert captured_authorization == [
        f"Bearer {TOKEN}",
        f"Bearer {TOKEN}",
    ]
    serialized_bodies = json.dumps(captured_bodies, sort_keys=True)
    assert TOKEN not in serialized_bodies


def test_handshake_rejects_suite_hash_drift() -> None:
    manifest = load_manifest(E_MANIFEST)

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json=_handshake_response(
                payload,
                suite_hash="b" * 64,
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = ExternalEvaluatorClient(
                base_url="https://evaluator.example",
                bearer_token=TOKEN,
                client=http_client,
            )
            await client.open_session(manifest)

    with pytest.raises(
        ExternalEvaluatorProtocolError,
        match="handshake binding mismatch",
    ):
        asyncio.run(scenario())


def test_handshake_rejects_failed_sandbox_attestation() -> None:
    manifest = load_manifest(E_MANIFEST)

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json=_handshake_response(
                payload,
                failed_check="network_disabled",
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = ExternalEvaluatorClient(
                base_url="https://evaluator.example",
                bearer_token=TOKEN,
                client=http_client,
            )
            await client.open_session(manifest)

    with pytest.raises(
        ExternalEvaluatorProtocolError,
        match="attestation rejected",
    ):
        asyncio.run(scenario())


def test_transport_rejects_redirects() -> None:
    manifest = load_manifest(E_MANIFEST)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            307,
            headers={"Location": "https://other.example/v1/handshake"},
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = ExternalEvaluatorClient(
                base_url="https://evaluator.example",
                bearer_token=TOKEN,
                client=http_client,
            )
            await client.open_session(manifest)

    with pytest.raises(
        ExternalEvaluatorTransportError,
        match="redirects are not allowed",
    ):
        asyncio.run(scenario())


def test_session_rejects_runner_version_drift_after_handshake() -> None:
    manifest = load_manifest(E_MANIFEST)

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path == "/v1/handshake":
            return httpx.Response(
                200,
                json=_handshake_response(payload),
            )
        return httpx.Response(
            200,
            json=_evaluation_response(
                payload,
                runner_version="fixture-runner-2",
            ),
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = ExternalEvaluatorClient(
                base_url="https://evaluator.example",
                bearer_token=TOKEN,
                client=http_client,
            )
            session = await client.open_session(manifest)
            world = create_world(manifest)
            await evaluate_hidden_functional_culture(
                world,
                manifest,
                attestation=session.attestation,
                runner=session,
            )

    with pytest.raises(
        ExternalEvaluatorProtocolError,
        match="runner_version changed",
    ):
        asyncio.run(scenario())


def test_session_refuses_evaluation_request_from_another_attestation() -> None:
    manifest = load_manifest(E_MANIFEST)

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json=_handshake_response(payload),
        )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = ExternalEvaluatorClient(
                base_url="https://evaluator.example",
                bearer_token=TOKEN,
                client=http_client,
            )
            session = await client.open_session(manifest)
            request = HiddenEvaluationRequest.model_construct(
                request_id="different-request",
                plan=None,
                plan_hash="a" * 64,
                snapshot=None,
                policy=manifest.evaluation.sandbox_policy,
                policy_hash=session.attestation.policy_hash,
                attestation_id="different-attestation",
                backend=session.attestation.backend,
                backend_version=session.attestation.backend_version,
            )
            await session.evaluate(request)

    with pytest.raises(
        ExternalEvaluatorProtocolError,
        match="does not match external evaluator session",
    ):
        asyncio.run(scenario())
