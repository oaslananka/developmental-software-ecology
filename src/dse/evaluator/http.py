import json
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from dse.contracts.evaluation import (
    HiddenEvaluationRequest,
    HiddenEvaluationRunnerResult,
)
from dse.contracts.evaluator_transport import (
    ExternalEvaluatorHandshakeRequest,
    ExternalEvaluatorHandshakeResponse,
)
from dse.contracts.experiment import ExperimentManifest
from dse.engine.hashing import state_hash
from dse.engine.sandbox_admission import (
    evaluate_sandbox_runtime_evidence,
    sandbox_policy_hash_for,
)


class ExternalEvaluatorTransportError(RuntimeError):
    """Raised when the external evaluator transport cannot complete safely."""


class ExternalEvaluatorProtocolError(ValueError):
    """Raised when the external evaluator violates the declared protocol."""


class ExternalHiddenEvaluatorSession:
    runner_kind = "external-hardened"

    def __init__(
        self,
        *,
        transport: "ExternalEvaluatorClient",
        handshake: ExternalEvaluatorHandshakeResponse,
    ) -> None:
        self._transport = transport
        self._handshake = handshake

    @property
    def runner_version(self) -> str:
        return self._handshake.runner_version

    @property
    def service_id(self) -> str:
        return self._handshake.service_id

    @property
    def worker_build_sha256(self) -> str:
        return self._handshake.worker_build_sha256

    @property
    def runtime_build_sha256(self) -> str:
        return self._handshake.runtime_build_sha256

    @property
    def attestation(self):
        return self._handshake.attestation

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
    ) -> HiddenEvaluationRunnerResult:
        _validate_session_request(self._handshake, request)
        payload = await self._transport.post_json(
            "/v1/evaluate",
            request.model_dump(mode="json"),
        )
        try:
            result = HiddenEvaluationRunnerResult.model_validate(payload)
        except ValidationError as error:
            raise ExternalEvaluatorProtocolError(
                "external evaluator returned an invalid evaluation result"
            ) from error

        if result.runner_kind != self.runner_kind:
            raise ExternalEvaluatorProtocolError(
                "external evaluator runner_kind changed after handshake"
            )
        if result.runner_version != self.runner_version:
            raise ExternalEvaluatorProtocolError(
                "external evaluator runner_version changed after handshake"
            )
        if result.worker_build_sha256 != self.worker_build_sha256:
            raise ExternalEvaluatorProtocolError(
                "external evaluator worker build changed after handshake"
            )
        if result.runtime_build_sha256 != self.runtime_build_sha256:
            raise ExternalEvaluatorProtocolError(
                "external evaluator runtime build changed after handshake"
            )
        return result


class ExternalEvaluatorClient:
    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 1_048_576,
        client: httpx.AsyncClient | None = None,
        allow_insecure_http: bool = False,
    ) -> None:
        self.base_url = _validated_base_url(
            base_url,
            allow_insecure_http=allow_insecure_http,
        )
        if not bearer_token.strip():
            raise ValueError("bearer_token must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1024")

        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self._bearer_token = bearer_token
        self._client = client

    async def open_session(
        self,
        manifest: ExperimentManifest,
    ) -> ExternalHiddenEvaluatorSession:
        config = manifest.evaluation
        opportunity = manifest.functional_opportunity
        if not config.enabled or config.suite_id is None or config.suite_hash is None:
            raise ExternalEvaluatorProtocolError(
                "manifest does not configure hidden evaluation"
            )
        if (
            not opportunity.enabled
            or opportunity.spec_id is None
            or opportunity.spec_sha256 is None
        ):
            raise ExternalEvaluatorProtocolError(
                "manifest does not configure functional opportunity"
            )

        policy_hash = sandbox_policy_hash_for(config.sandbox_policy)
        identity = {
            "experiment_id": manifest.experiment.id,
            "suite_id": config.suite_id,
            "suite_hash": config.suite_hash,
            "opportunity_spec_id": opportunity.spec_id,
            "opportunity_spec_hash": opportunity.spec_sha256,
            "opportunity_aggregation": opportunity.aggregation,
            "policy_hash": policy_hash,
        }
        request = ExternalEvaluatorHandshakeRequest(
            request_id=f"handshake-{state_hash(identity)[:48]}",
            experiment_id=manifest.experiment.id,
            suite_id=config.suite_id,
            suite_hash=config.suite_hash,
            opportunity_spec_id=opportunity.spec_id,
            opportunity_spec_hash=opportunity.spec_sha256,
            opportunity_aggregation=opportunity.aggregation,
            policy=config.sandbox_policy,
            policy_hash=policy_hash,
        )

        payload = await self.post_json(
            "/v1/handshake",
            request.model_dump(mode="json"),
        )
        try:
            response = ExternalEvaluatorHandshakeResponse.model_validate(payload)
        except ValidationError as error:
            raise ExternalEvaluatorProtocolError(
                "external evaluator returned an invalid handshake"
            ) from error

        mismatches = _handshake_binding_errors(request, response)
        if mismatches:
            raise ExternalEvaluatorProtocolError(
                "external evaluator handshake binding mismatch: "
                + ", ".join(mismatches)
            )

        admission = evaluate_sandbox_runtime_evidence(
            enabled=config.sandbox_enabled,
            policy=config.sandbox_policy,
            plan_id=f"{request.request_id}:plan",
            action_id=f"{request.request_id}:handshake",
            plan_hash=state_hash(request.model_dump(mode="json")),
            attestation=response.attestation,
        )
        if not admission.admitted:
            details = [admission.reason, *admission.failed_checks]
            raise ExternalEvaluatorProtocolError(
                "external evaluator attestation rejected: "
                + ", ".join(details)
            )

        return ExternalHiddenEvaluatorSession(
            transport=self,
            handshake=response,
        )

    async def post_json(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("external evaluator path must be absolute and host-relative")

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}{path}"

        try:
            if self._client is not None:
                response = await self._request(
                    self._client,
                    url,
                    headers=headers,
                    payload=payload,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = await self._request(
                        client,
                        url,
                        headers=headers,
                        payload=payload,
                    )
        except httpx.HTTPError as error:
            raise ExternalEvaluatorTransportError(
                "external evaluator request failed"
            ) from error

        if response.is_redirect:
            raise ExternalEvaluatorTransportError(
                "external evaluator redirects are not allowed"
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ExternalEvaluatorTransportError(
                f"external evaluator returned HTTP {response.status_code}"
            ) from error

        if len(response.content) > self.max_response_bytes:
            raise ExternalEvaluatorTransportError(
                "external evaluator response exceeded byte limit"
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise ExternalEvaluatorTransportError(
                "external evaluator returned invalid JSON"
            ) from error
        if not isinstance(data, dict):
            raise ExternalEvaluatorProtocolError(
                "external evaluator response must be a JSON object"
            )
        return data

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict,
    ) -> httpx.Response:
        return await client.request(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=self.timeout_seconds,
            follow_redirects=False,
        )


def _validated_base_url(
    value: str,
    *,
    allow_insecure_http: bool,
) -> str:
    parsed = urlsplit(value)
    allowed_schemes = {"https"}
    if allow_insecure_http:
        allowed_schemes.add("http")

    if parsed.scheme not in allowed_schemes:
        raise ValueError("external evaluator base_url must use HTTPS")
    if not parsed.hostname:
        raise ValueError("external evaluator base_url requires a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("external evaluator base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("external evaluator base_url must not contain query/fragment")
    return value.rstrip("/")


def _handshake_binding_errors(
    request: ExternalEvaluatorHandshakeRequest,
    response: ExternalEvaluatorHandshakeResponse,
) -> list[str]:
    checks = (
        ("request_id", request.request_id, response.request_id),
        ("suite_id", request.suite_id, response.suite_id),
        ("suite_hash", request.suite_hash, response.suite_hash),
        (
            "opportunity_spec_id",
            request.opportunity_spec_id,
            response.opportunity_spec_id,
        ),
        (
            "opportunity_spec_hash",
            request.opportunity_spec_hash,
            response.opportunity_spec_hash,
        ),
        (
            "opportunity_aggregation",
            request.opportunity_aggregation,
            response.opportunity_aggregation,
        ),
        ("policy_hash", request.policy_hash, response.attestation.policy_hash),
    )
    return [
        field
        for field, expected, actual in checks
        if expected != actual
    ]


def _validate_session_request(
    handshake: ExternalEvaluatorHandshakeResponse,
    request: HiddenEvaluationRequest,
) -> None:
    checks = (
        ("suite_hash", handshake.suite_hash, request.plan.suite_hash),
        (
            "opportunity_spec_id",
            handshake.opportunity_spec_id,
            request.plan.opportunity_spec_id,
        ),
        (
            "opportunity_spec_hash",
            handshake.opportunity_spec_hash,
            request.plan.opportunity_spec_hash,
        ),
        (
            "opportunity_aggregation",
            handshake.opportunity_aggregation,
            request.plan.opportunity_aggregation,
        ),
        (
            "policy_hash",
            handshake.attestation.policy_hash,
            request.policy_hash,
        ),
        (
            "attestation_id",
            handshake.attestation.attestation_id,
            request.attestation_id,
        ),
        ("backend", handshake.attestation.backend, request.backend),
        (
            "backend_version",
            handshake.attestation.backend_version,
            request.backend_version,
        ),
    )
    mismatches = [
        field
        for field, expected, actual in checks
        if expected != actual
    ]
    if mismatches:
        raise ExternalEvaluatorProtocolError(
            "evaluation request does not match external evaluator session: "
            + ", ".join(mismatches)
        )
