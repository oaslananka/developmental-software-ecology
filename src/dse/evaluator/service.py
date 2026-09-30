import argparse
import asyncio
import hmac
import json
import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path

from pydantic import ValidationError

from dse.contracts.evaluation import HiddenEvaluationRequest
from dse.contracts.evaluator_transport import ExternalEvaluatorHandshakeRequest
from dse.evaluator.gvisor_docker import GVisorDockerBackend
from dse.evaluator.suite_store import FilesystemHiddenSuiteStore, HiddenSuiteError
from dse.evaluator.worker import EvaluatorWorkerError, HardenedEvaluatorWorker


_HANDSHAKE_PATH = "/v1/handshake"
_EVALUATE_PATH = "/v1/evaluate"
_JSON_CONTENT_TYPE = "application/json"
_ALLOWED_PATHS = frozenset({_HANDSHAKE_PATH, _EVALUATE_PATH})
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_\x60|~0-9A-Za-z-]+$")


@dataclass(frozen=True)
class EvaluatorServiceResponse:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def json(
        cls,
        status: int,
        payload: Mapping[str, object],
        *,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> "EvaluatorServiceResponse":
        body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            status=status,
            body=body,
            headers=(
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
                *headers,
            ),
        )


class EvaluatorServiceApplication:
    """Authenticated protocol adapter around a hardened evaluator worker."""

    def __init__(
        self,
        *,
        worker: HardenedEvaluatorWorker,
        bearer_token: str,
        max_request_bytes: int = 2_097_152,
    ) -> None:
        if len(bearer_token) < 32:
            raise ValueError(
                "bearer_token must contain at least 32 characters"
            )
        if max_request_bytes < 1024:
            raise ValueError(
                "max_request_bytes must be at least 1024"
            )
        self.worker = worker
        self._bearer_token = bearer_token
        self.max_request_bytes = max_request_bytes

    def preflight(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        content_length: int | None = None,
    ) -> EvaluatorServiceResponse | None:
        normalized = {
            key.lower(): value
            for key, value in headers.items()
        }

        if not self._authorized(
            normalized.get("authorization")
        ):
            return EvaluatorServiceResponse.json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "unauthorized"},
                headers=(
                    (
                        "WWW-Authenticate",
                        'Bearer realm="dse-evaluator"',
                    ),
                ),
            )

        if method != "POST":
            return EvaluatorServiceResponse.json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "method_not_allowed"},
                headers=(("Allow", "POST"),),
            )

        if path not in _ALLOWED_PATHS:
            return EvaluatorServiceResponse.json(
                HTTPStatus.NOT_FOUND,
                {"error": "not_found"},
            )

        if normalized.get("transfer-encoding"):
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "unsupported_transfer_encoding"},
            )

        media_type = normalized.get(
            "content-type",
            "",
        ).split(";", maxsplit=1)[0].strip().lower()
        if media_type != _JSON_CONTENT_TYPE:
            return EvaluatorServiceResponse.json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "unsupported_media_type"},
            )

        if (
            content_length is not None
            and content_length > self.max_request_bytes
        ):
            return EvaluatorServiceResponse.json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "request_too_large"},
            )
        return None

    async def handle(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> EvaluatorServiceResponse:
        preflight = self.preflight(
            method=method,
            path=path,
            headers=headers,
            content_length=len(body),
        )
        if preflight is not None:
            return preflight

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_json"},
            )
        if not isinstance(payload, dict):
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request"},
            )

        try:
            if path == _HANDSHAKE_PATH:
                request = (
                    ExternalEvaluatorHandshakeRequest.model_validate(
                        payload
                    )
                )
                response = await self.worker.handshake(
                    request
                )
            else:
                request = HiddenEvaluationRequest.model_validate(
                    payload
                )
                response = await self.worker.evaluate(
                    request
                )
        except ValidationError:
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request"},
            )
        except (
            EvaluatorWorkerError,
            HiddenSuiteError,
        ):
            return EvaluatorServiceResponse.json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "evaluation_rejected"},
            )
        except Exception:
            return EvaluatorServiceResponse.json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "evaluator_unavailable"},
            )

        return EvaluatorServiceResponse.json(
            HTTPStatus.OK,
            response.model_dump(mode="json"),
        )

    def _authorized(
        self,
        value: str | None,
    ) -> bool:
        if value is None:
            return False
        scheme, separator, token = value.partition(" ")
        if separator != " " or scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(
            token.encode("utf-8"),
            self._bearer_token.encode("utf-8"),
        )


@dataclass(frozen=True)
class _ParsedRequestHead:
    method: str
    path: str
    headers: dict[str, str]
    content_length: int | None


class EvaluatorTLSServer:
    """Small fail-closed TLS/HTTP boundary for the evaluator protocol."""

    def __init__(
        self,
        *,
        application: EvaluatorServiceApplication,
        ssl_context: ssl.SSLContext,
        request_timeout_seconds: float,
        operation_timeout_seconds: float,
        max_connections: int,
        max_concurrent_operations: int,
        max_header_bytes: int = 16_384,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError(
                "request_timeout_seconds must be positive"
            )
        if operation_timeout_seconds <= 0:
            raise ValueError(
                "operation_timeout_seconds must be positive"
            )
        if max_connections < 1:
            raise ValueError(
                "max_connections must be positive"
            )
        if max_concurrent_operations < 1:
            raise ValueError(
                "max_concurrent_operations must be positive"
            )
        if max_header_bytes < 1024:
            raise ValueError(
                "max_header_bytes must be at least 1024"
            )

        self.application = application
        self.ssl_context = ssl_context
        self.request_timeout_seconds = (
            request_timeout_seconds
        )
        self.operation_timeout_seconds = (
            operation_timeout_seconds
        )
        self.max_connections = max_connections
        self.max_concurrent_operations = (
            max_concurrent_operations
        )
        self.max_header_bytes = max_header_bytes
        self._active_connections = 0
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._operation_slots: asyncio.Semaphore | None = None

    async def serve_forever(
        self,
        host: str,
        port: int,
    ) -> None:
        self._operation_slots = asyncio.Semaphore(
            self.max_concurrent_operations
        )
        server = await asyncio.start_server(
            self._accept_connection,
            host=host,
            port=port,
            ssl=self.ssl_context,
            ssl_handshake_timeout=(
                self.request_timeout_seconds
            ),
            limit=self.max_header_bytes + 1,
            backlog=self.max_connections,
        )
        async with server:
            await server.serve_forever()

    def _accept_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if self._active_connections >= self.max_connections:
            writer.close()
            return

        self._active_connections += 1
        task = asyncio.create_task(
            self._handle_connection(
                reader,
                writer,
            )
        )
        self._connection_tasks.add(task)
        task.add_done_callback(
            self._connection_finished
        )

    def _connection_finished(
        self,
        task: asyncio.Task[None],
    ) -> None:
        self._connection_tasks.discard(task)
        self._active_connections -= 1
        try:
            task.result()
        except Exception:
            return

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            parsed = await self._read_request_head(
                reader
            )
            if isinstance(
                parsed,
                EvaluatorServiceResponse,
            ):
                await self._write_response(
                    writer,
                    parsed,
                )
                return

            preflight = self.application.preflight(
                method=parsed.method,
                path=parsed.path,
                headers=parsed.headers,
                content_length=(
                    parsed.content_length
                ),
            )
            if preflight is not None:
                await self._write_response(
                    writer,
                    preflight,
                )
                return

            if parsed.content_length is None:
                await self._write_response(
                    writer,
                    EvaluatorServiceResponse.json(
                        HTTPStatus.LENGTH_REQUIRED,
                        {
                            "error": (
                                "content_length_required"
                            )
                        },
                    ),
                )
                return

            try:
                body = await asyncio.wait_for(
                    reader.readexactly(
                        parsed.content_length
                    ),
                    timeout=self.request_timeout_seconds,
                )
            except TimeoutError:
                await self._write_response(
                    writer,
                    EvaluatorServiceResponse.json(
                        HTTPStatus.REQUEST_TIMEOUT,
                        {"error": "request_timeout"},
                    ),
                )
                return
            except asyncio.IncompleteReadError:
                await self._write_response(
                    writer,
                    EvaluatorServiceResponse.json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "incomplete_body"},
                    ),
                )
                return

            slots = self._operation_slots
            if slots is None:
                await self._write_response(
                    writer,
                    EvaluatorServiceResponse.json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "evaluator_unavailable"},
                    ),
                )
                return

            try:
                async with slots:
                    response = await asyncio.wait_for(
                        self.application.handle(
                            method=parsed.method,
                            path=parsed.path,
                            headers=parsed.headers,
                            body=body,
                        ),
                        timeout=(
                            self.operation_timeout_seconds
                        ),
                    )
            except TimeoutError:
                response = EvaluatorServiceResponse.json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "evaluator_timeout"},
                )

            await self._write_response(
                writer,
                response,
            )
        except Exception:
            try:
                await self._write_response(
                    writer,
                    EvaluatorServiceResponse.json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_request"},
                    ),
                )
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _read_request_head(
        self,
        reader: asyncio.StreamReader,
    ) -> _ParsedRequestHead | EvaluatorServiceResponse:
        try:
            raw = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                timeout=self.request_timeout_seconds,
            )
        except TimeoutError:
            return EvaluatorServiceResponse.json(
                HTTPStatus.REQUEST_TIMEOUT,
                {"error": "request_timeout"},
            )
        except asyncio.LimitOverrunError:
            return EvaluatorServiceResponse.json(
                HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                {"error": "headers_too_large"},
            )
        except asyncio.IncompleteReadError:
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "incomplete_headers"},
            )

        if len(raw) > self.max_header_bytes:
            return EvaluatorServiceResponse.json(
                HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                {"error": "headers_too_large"},
            )

        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_headers"},
            )

        lines = text[:-4].split("\r\n")
        if not lines:
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request_line"},
            )

        request_parts = lines[0].split(" ")
        if len(request_parts) != 3:
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request_line"},
            )
        method, path, version = request_parts
        if (
            not method
            or not path.startswith("/")
            or version != "HTTP/1.1"
        ):
            return EvaluatorServiceResponse.json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request_line"},
            )

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if (
                not line
                or line[0] in " \t"
                or ":" not in line
            ):
                return EvaluatorServiceResponse.json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_headers"},
                )
            name, value = line.split(":", maxsplit=1)
            if not _HEADER_NAME.fullmatch(name):
                return EvaluatorServiceResponse.json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_headers"},
                )
            normalized_name = name.lower()
            if normalized_name in headers:
                return EvaluatorServiceResponse.json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "duplicate_header"},
                )
            if any(
                ord(character) < 32
                and character != "\t"
                for character in value
            ):
                return EvaluatorServiceResponse.json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_headers"},
                )
            headers[normalized_name] = value.strip()

        content_length: int | None = None
        length_value = headers.get(
            "content-length"
        )
        if length_value is not None:
            try:
                content_length = int(
                    length_value,
                    10,
                )
            except ValueError:
                return EvaluatorServiceResponse.json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_content_length"},
                )
            if content_length < 0:
                return EvaluatorServiceResponse.json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_content_length"},
                )
            if (
                content_length
                > self.application.max_request_bytes
            ):
                return EvaluatorServiceResponse.json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "request_too_large"},
                )

        return _ParsedRequestHead(
            method=method,
            path=path,
            headers=headers,
            content_length=content_length,
        )

    @staticmethod
    async def _write_response(
        writer: asyncio.StreamWriter,
        response: EvaluatorServiceResponse,
    ) -> None:
        try:
            status = HTTPStatus(response.status)
        except ValueError:
            status = HTTPStatus.INTERNAL_SERVER_ERROR

        header_lines = [
            (
                f"HTTP/1.1 {status.value} "
                f"{status.phrase}\r\n"
            ).encode("ascii")
        ]
        for name, value in response.headers:
            header_lines.append(
                f"{name}: {value}\r\n".encode(
                    "ascii"
                )
            )
        header_lines.append(
            b"Connection: close\r\n\r\n"
        )
        writer.writelines(
            [*header_lines, response.body]
        )
        await writer.drain()


@dataclass(frozen=True)
class EvaluatorServiceConfig:
    host: str
    port: int
    tls_cert: Path
    tls_key: Path
    suite_root: Path
    bearer_token: str = field(repr=False)
    worker_build_sha256: str
    service_id: str = "dse-evaluator"
    runner_version: str = "m18.3-service-1"
    runtime_image: str = (
        "gcr.io/distroless/python3-debian13:nonroot"
    )
    max_request_bytes: int = 2_097_152
    max_header_bytes: int = 16_384
    request_timeout_seconds: float = 30.0
    operation_timeout_seconds: float = 300.0
    max_connections: int = 16
    max_concurrent_operations: int = 1

    @classmethod
    def from_env(
        cls,
    ) -> "EvaluatorServiceConfig":
        return cls(
            host=os.environ.get(
                "DSE_EVALUATOR_HOST",
                "127.0.0.1",
            ),
            port=_env_int(
                "DSE_EVALUATOR_PORT",
                8443,
                minimum=1,
                maximum=65_535,
            ),
            tls_cert=_required_path(
                "DSE_EVALUATOR_TLS_CERT"
            ),
            tls_key=_required_path(
                "DSE_EVALUATOR_TLS_KEY"
            ),
            suite_root=_required_directory(
                "DSE_EVALUATOR_SUITE_ROOT"
            ),
            bearer_token=_required_secret(
                "DSE_EVALUATOR_BEARER_TOKEN"
            ),
            worker_build_sha256=_required_sha256(
                "DSE_EVALUATOR_WORKER_BUILD_SHA256"
            ),
            service_id=os.environ.get(
                "DSE_EVALUATOR_SERVICE_ID",
                "dse-evaluator",
            ),
            runner_version=os.environ.get(
                "DSE_EVALUATOR_RUNNER_VERSION",
                "m18.3-service-1",
            ),
            runtime_image=os.environ.get(
                "DSE_GVISOR_RUNTIME_IMAGE",
                "gcr.io/distroless/python3-debian13:nonroot",
            ),
            max_request_bytes=_env_int(
                "DSE_EVALUATOR_MAX_REQUEST_BYTES",
                2_097_152,
                minimum=1024,
                maximum=67_108_864,
            ),
            max_header_bytes=_env_int(
                "DSE_EVALUATOR_MAX_HEADER_BYTES",
                16_384,
                minimum=1024,
                maximum=65_536,
            ),
            request_timeout_seconds=_env_float(
                "DSE_EVALUATOR_REQUEST_TIMEOUT_SECONDS",
                30.0,
                minimum=1.0,
                maximum=300.0,
            ),
            operation_timeout_seconds=_env_float(
                "DSE_EVALUATOR_OPERATION_TIMEOUT_SECONDS",
                300.0,
                minimum=1.0,
                maximum=900.0,
            ),
            max_connections=_env_int(
                "DSE_EVALUATOR_MAX_CONNECTIONS",
                16,
                minimum=1,
                maximum=128,
            ),
            max_concurrent_operations=_env_int(
                "DSE_EVALUATOR_MAX_CONCURRENT_OPERATIONS",
                1,
                minimum=1,
                maximum=8,
            ),
        )


def build_service(
    config: EvaluatorServiceConfig,
) -> EvaluatorServiceApplication:
    backend = GVisorDockerBackend(
        runtime_image=config.runtime_image,
    )
    worker = HardenedEvaluatorWorker(
        service_id=config.service_id,
        runner_version=config.runner_version,
        worker_build_sha256=(
            config.worker_build_sha256
        ),
        suite_store=FilesystemHiddenSuiteStore(
            config.suite_root
        ),
        backend=backend,
    )
    return EvaluatorServiceApplication(
        worker=worker,
        bearer_token=config.bearer_token,
        max_request_bytes=config.max_request_bytes,
    )


def _tls_context(
    config: EvaluatorServiceConfig,
) -> ssl.SSLContext:
    context = ssl.SSLContext(
        ssl.PROTOCOL_TLS_SERVER
    )
    context.minimum_version = (
        ssl.TLSVersion.TLSv1_2
    )
    context.load_cert_chain(
        certfile=config.tls_cert,
        keyfile=config.tls_key,
    )
    return context


async def _serve_https_async(
    config: EvaluatorServiceConfig,
) -> None:
    application = build_service(config)
    server = EvaluatorTLSServer(
        application=application,
        ssl_context=_tls_context(config),
        request_timeout_seconds=(
            config.request_timeout_seconds
        ),
        operation_timeout_seconds=(
            config.operation_timeout_seconds
        ),
        max_connections=config.max_connections,
        max_concurrent_operations=(
            config.max_concurrent_operations
        ),
        max_header_bytes=config.max_header_bytes,
    )
    await server.serve_forever(
        config.host,
        config.port,
    )


def serve_https(
    config: EvaluatorServiceConfig,
) -> None:
    asyncio.run(
        _serve_https_async(config)
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the authenticated DSE hardened "
            "evaluator HTTPS service."
        )
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help=(
            "Validate required environment "
            "configuration without starting."
        ),
    )
    args = parser.parse_args()
    config = EvaluatorServiceConfig.from_env()
    if args.check_config:
        return
    serve_https(config)


def _required_secret(name: str) -> str:
    value = os.environ.get(name)
    if value is None or len(value) < 32:
        raise ValueError(
            f"{name} must contain at least 32 characters"
        )
    return value


def _required_sha256(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise ValueError(f"{name} is required")
    if len(value) != 64 or any(
        character not in "0123456789abcdef"
        for character in value
    ):
        raise ValueError(
            f"{name} must be a lowercase SHA-256 digest"
        )
    return value


def _required_path(name: str) -> Path:
    value = os.environ.get(name)
    if value is None:
        raise ValueError(f"{name} is required")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(
            f"{name} must reference an existing file"
        )
    return path


def _required_directory(name: str) -> Path:
    value = os.environ.get(name)
    if value is None:
        raise ValueError(f"{name} is required")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(
            f"{name} must reference an existing directory"
        )
    return path


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.environ.get(name)
    value = default if raw is None else int(raw)
    if value < minimum or value > maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.environ.get(name)
    value = default if raw is None else float(raw)
    if value < minimum or value > maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


if __name__ == "__main__":
    main()
