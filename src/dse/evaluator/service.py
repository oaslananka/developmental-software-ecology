import argparse
import asyncio
import hmac
import json
import os
import ssl
import threading
from collections.abc import Coroutine, Mapping
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

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


class EvaluatorAsyncRuntime:
    """One persistent asyncio loop with bounded evaluator-operation concurrency."""

    def __init__(
        self,
        *,
        max_concurrent_operations: int = 1,
        operation_timeout_seconds: float = 300.0,
    ) -> None:
        if max_concurrent_operations < 1:
            raise ValueError(
                "max_concurrent_operations must be positive"
            )
        if operation_timeout_seconds <= 0:
            raise ValueError(
                "operation_timeout_seconds must be positive"
            )

        self.max_concurrent_operations = (
            max_concurrent_operations
        )
        self.operation_timeout_seconds = (
            operation_timeout_seconds
        )
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._operation_slots: asyncio.Semaphore | None = None
        self._thread = threading.Thread(
            target=self._run_loop,
            name="dse-evaluator-async-runtime",
            daemon=False,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError(
                "evaluator async runtime failed to start"
            )

    def run(
        self,
        coroutine: Coroutine[Any, Any, EvaluatorServiceResponse],
    ) -> EvaluatorServiceResponse:
        if self._closed:
            coroutine.close()
            raise RuntimeError(
                "evaluator async runtime is closed"
            )
        future = asyncio.run_coroutine_threadsafe(
            self._bounded(coroutine),
            self._loop,
        )
        try:
            return future.result(
                timeout=self.operation_timeout_seconds
            )
        except FutureTimeoutError:
            future.cancel()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(
            self._loop.stop
        )
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            raise RuntimeError(
                "evaluator async runtime failed to stop"
            )
        self._loop.close()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._operation_slots = asyncio.Semaphore(
            self.max_concurrent_operations
        )
        self._ready.set()
        self._loop.run_forever()

    async def _bounded(
        self,
        coroutine: Coroutine[Any, Any, EvaluatorServiceResponse],
    ) -> EvaluatorServiceResponse:
        slots = self._operation_slots
        if slots is None:
            coroutine.close()
            raise RuntimeError(
                "evaluator async runtime is not ready"
            )
        async with slots:
            return await coroutine


class EvaluatorHTTPServer(ThreadingHTTPServer):
    """Threaded ingress with explicit connection and worker-operation bounds."""

    daemon_threads = False
    block_on_close = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        application: EvaluatorServiceApplication,
        request_timeout_seconds: float,
        max_connections: int,
        max_concurrent_operations: int,
        operation_timeout_seconds: float,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError(
                "request_timeout_seconds must be positive"
            )
        if max_connections < 1:
            raise ValueError(
                "max_connections must be positive"
            )
        self.application = application
        self.request_timeout_seconds = (
            request_timeout_seconds
        )
        self.max_connections = max_connections
        self._connection_slots = threading.BoundedSemaphore(
            max_connections
        )
        self.async_runtime = EvaluatorAsyncRuntime(
            max_concurrent_operations=max_concurrent_operations,
            operation_timeout_seconds=operation_timeout_seconds,
        )
        try:
            super().__init__(
                server_address,
                handler_class,
            )
        except Exception:
            self.async_runtime.close()
            raise

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(
            self.request_timeout_seconds
        )
        return connection, address

    def process_request(
        self,
        request,
        client_address,
    ) -> None:
        acquired = self._connection_slots.acquire(
            timeout=self.request_timeout_seconds
        )
        if not acquired:
            self.shutdown_request(request)
            return
        try:
            super().process_request(
                request,
                client_address,
            )
        except Exception:
            self._connection_slots.release()
            raise

    def process_request_thread(
        self,
        request,
        client_address,
    ) -> None:
        try:
            super().process_request_thread(
                request,
                client_address,
            )
        finally:
            self._connection_slots.release()

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self.async_runtime.close()


class EvaluatorRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DSEEvaluator/0.1"
    sys_version = ""

    def do_POST(self) -> None:
        self._handle_request()

    def do_GET(self) -> None:
        self._handle_request()

    def do_PUT(self) -> None:
        self._handle_request()

    def do_DELETE(self) -> None:
        self._handle_request()

    def _handle_request(self) -> None:
        server = self.server
        if not isinstance(
            server,
            EvaluatorHTTPServer,
        ):
            self.send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR
            )
            return

        headers = {
            key: value
            for key, value in self.headers.items()
        }
        length_value = self.headers.get(
            "Content-Length"
        )
        content_length: int | None = None

        if self.command == "POST":
            if length_value is None:
                self._finish(
                    EvaluatorServiceResponse.json(
                        HTTPStatus.LENGTH_REQUIRED,
                        {
                            "error": (
                                "content_length_required"
                            )
                        },
                    )
                )
                return
            try:
                content_length = int(length_value)
            except ValueError:
                self._finish(
                    EvaluatorServiceResponse.json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                "invalid_content_length"
                            )
                        },
                    )
                )
                return
            if content_length < 0:
                self._finish(
                    EvaluatorServiceResponse.json(
                        HTTPStatus.BAD_REQUEST,
                        {
                            "error": (
                                "invalid_content_length"
                            )
                        },
                    )
                )
                return

        preflight = server.application.preflight(
            method=self.command,
            path=self.path,
            headers=headers,
            content_length=content_length,
        )
        if preflight is not None:
            self._finish(preflight)
            return

        assert content_length is not None
        try:
            body = self.rfile.read(
                content_length
            )
        except (TimeoutError, OSError):
            self._finish(
                EvaluatorServiceResponse.json(
                    HTTPStatus.REQUEST_TIMEOUT,
                    {"error": "request_timeout"},
                )
            )
            return

        if len(body) != content_length:
            self._finish(
                EvaluatorServiceResponse.json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "incomplete_body"},
                )
            )
            return

        try:
            response = server.async_runtime.run(
                server.application.handle(
                    method=self.command,
                    path=self.path,
                    headers=headers,
                    body=body,
                )
            )
        except FutureTimeoutError:
            response = EvaluatorServiceResponse.json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "evaluator_timeout"},
            )
        except RuntimeError:
            response = EvaluatorServiceResponse.json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "evaluator_unavailable"},
            )
        self._finish(response)

    def _finish(
        self,
        response: EvaluatorServiceResponse,
    ) -> None:
        self.close_connection = True
        self._write_response(response)

    def _write_response(
        self,
        response: EvaluatorServiceResponse,
    ) -> None:
        try:
            self.send_response(response.status)
            for name, value in response.headers:
                self.send_header(name, value)
            self.send_header(
                "Connection",
                "close",
            )
            self.end_headers()
            self.wfile.write(response.body)
        except (
            BrokenPipeError,
            ConnectionResetError,
            OSError,
        ):
            self.close_connection = True

    def log_message(
        self,
        log_format: str,
        *args,
    ) -> None:
        return


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
    request_timeout_seconds: float = 30.0
    max_connections: int = 16
    max_concurrent_operations: int = 1
    operation_timeout_seconds: float = 300.0

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
            request_timeout_seconds=_env_float(
                "DSE_EVALUATOR_REQUEST_TIMEOUT_SECONDS",
                30.0,
                minimum=1.0,
                maximum=300.0,
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
            operation_timeout_seconds=_env_float(
                "DSE_EVALUATOR_OPERATION_TIMEOUT_SECONDS",
                300.0,
                minimum=1.0,
                maximum=900.0,
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


def serve_https(
    config: EvaluatorServiceConfig,
) -> None:
    application = build_service(config)
    server = EvaluatorHTTPServer(
        (config.host, config.port),
        EvaluatorRequestHandler,
        application=application,
        request_timeout_seconds=(
            config.request_timeout_seconds
        ),
        max_connections=config.max_connections,
        max_concurrent_operations=(
            config.max_concurrent_operations
        ),
        operation_timeout_seconds=(
            config.operation_timeout_seconds
        ),
    )
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
    server.socket = context.wrap_socket(
        server.socket,
        server_side=True,
    )
    try:
        server.serve_forever(
            poll_interval=0.5
        )
    finally:
        server.server_close()


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
