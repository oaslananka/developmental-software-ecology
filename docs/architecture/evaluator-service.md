# M18.3 — authenticated evaluator service boundary

M18.3 exposes the M17 external evaluator protocol around the real M18 worker
and M18.2 gVisor backend without moving hidden-suite material into the control
plane.

## Data path

```text
ExternalEvaluatorClient
    |
    | HTTPS + Bearer
    v
EvaluatorRequestHandler
    |
    v
EvaluatorServiceApplication
    |
    v
HardenedEvaluatorWorker
    |
    +--> FilesystemHiddenSuiteStore
    |
    +--> GVisorDockerBackend
```

The service exposes exactly:

- `POST /v1/handshake`
- `POST /v1/evaluate`

No generic file, shell, health-debug, suite-inspection, or arbitrary execution
endpoint is part of this protocol surface.

## Authentication

The bearer token is operator-only configuration.

The service:

- requires at least 32 characters;
- accepts it only in the HTTP `Authorization: Bearer ...` header;
- compares it with `hmac.compare_digest`;
- keeps it out of the dataclass repr;
- does not place it in evaluator request objects, experiment manifests, world
  state, events, snapshots, hidden-suite payloads, or child containers;
- disables the default `BaseHTTPRequestHandler` request logging path.

Unauthorized responses are generic and do not parse or echo the request body.

## TLS ingress and concurrency

The production server uses one persistent asyncio event loop and
`asyncio.start_server(..., ssl=...)`; it does not create a new event loop per
request and does not use Python's development-oriented `http.server`.

The ingress is deliberately narrow:

- one HTTP/1.1 request per TLS connection;
- a bounded connection count and listen backlog;
- a bounded header byte limit;
- duplicate headers are rejected;
- obsolete folded headers are rejected;
- absolute-form/non-origin request targets are rejected;
- every response closes the connection.

Long evaluator work does not block acceptance/parsing of unrelated
connections. A separate operation semaphore bounds concurrent hardened
handshake/evaluation work; the default is one expensive evaluator operation at
a time.

## Request bounding

The server rejects requests before body parsing when:

- the method is not `POST`;
- the path is not one of the two evaluator routes;
- `Transfer-Encoding` is present;
- `Content-Length` is absent, invalid, negative, or above the configured
  maximum.

The default request ceiling is 2 MiB, which is above the V0.1 default
1 MiB culture-snapshot ceiling while remaining independently bounded.

Accepted bodies must use `application/json`. JSON model validation remains
strict through the existing Pydantic contracts.

Each accepted connection receives a socket read timeout. Slow or incomplete
request bodies fail closed.

## Error sanitization

The public response surface returns only small generic JSON errors such as:

```json
{"error":"evaluation_rejected"}
```

Worker validation failures do not expose:

- private suite paths or bytes;
- hidden case source;
- stdout/stderr;
- runtime command details;
- bearer tokens;
- local certificate/key paths.

Unexpected evaluator/runtime failures return a generic service-unavailable
response.

## TLS

The production entrypoint always creates a TLS server with Python's
`ssl.PROTOCOL_TLS_SERVER` and requires TLS 1.2 or newer.

There is no production plaintext-mode CLI flag.

Required operator configuration:

```text
DSE_EVALUATOR_TLS_CERT
DSE_EVALUATOR_TLS_KEY
DSE_EVALUATOR_SUITE_ROOT
DSE_EVALUATOR_BEARER_TOKEN
DSE_EVALUATOR_WORKER_BUILD_SHA256
```

Optional configuration:

```text
DSE_EVALUATOR_HOST
DSE_EVALUATOR_PORT
DSE_EVALUATOR_SERVICE_ID
DSE_EVALUATOR_RUNNER_VERSION
DSE_GVISOR_RUNTIME_IMAGE
DSE_EVALUATOR_MAX_REQUEST_BYTES
DSE_EVALUATOR_MAX_HEADER_BYTES
DSE_EVALUATOR_REQUEST_TIMEOUT_SECONDS
DSE_EVALUATOR_OPERATION_TIMEOUT_SECONDS
DSE_EVALUATOR_MAX_CONNECTIONS
DSE_EVALUATOR_MAX_CONCURRENT_OPERATIONS
```

The service defaults to `127.0.0.1:8443`. Operators that expose it beyond
loopback are responsible for host/network policy in addition to the
application-layer TLS and bearer boundary.

## Immutable worker provenance

`DSE_EVALUATOR_WORKER_BUILD_SHA256` is required rather than computed from
the live source tree.

This is deliberate: scientific provenance should identify the immutable
worker build artifact chosen by the operator/deployment pipeline, not a
best-effort hash of whatever files happen to be visible at runtime.

The M18.2 backend independently measures and binds the runtime build.

## Private-suite boundary

`DSE_EVALUATOR_SUITE_ROOT` points to the operator-controlled
`FilesystemHiddenSuiteStore`.

A suite still uses:

```text
<private-root>/
  <suite-id>/
    suite.bundle
    manifest.json
```

The service never provides a suite listing or read endpoint.

The M18 worker verifies exact suite bytes against the manifest-pinned suite
hash before the backend can execute them.

M18.2 then streams those verified bytes into the gVisor container over stdin;
the service itself does not add a bind mount, Docker volume, temporary suite
file, or public artifact.

## Test boundary

`tests/test_evaluator_service.py` uses an in-memory public fixture backend and
suite to prove:

- bearer enforcement;
- route/method/content-type/size/transfer-encoding rejection;
- sanitized worker errors;
- `ExternalEvaluatorClient -> service -> HardenedEvaluatorWorker` protocol
  compatibility;
- runtime-measured evidence propagation;
- immutable worker/runtime build binding;
- no world-state mutation during functional evaluation;
- E-condition readiness can be cleared by correctly bound measured evidence;
- operator token omission from config repr.

This remains a protocol/service acceptance fixture, not the canonical V0.1
private hidden suite.

## Remaining scientific acceptance

After M18.3 code acceptance, one operator-controlled validation is still
required before starting the replicated pilot:

1. deploy this service with a real immutable worker build hash;
2. point it at the private V0.1 suite bytes whose SHA-256 matches the
   manifest-pinned suite hash;
3. run the M17 client through TLS into the real M18.2 gVisor backend;
4. confirm the persisted functional report is sanitized and
   `runtime-measured`;
5. only then treat E/ES evaluator readiness as real experimental evidence.
