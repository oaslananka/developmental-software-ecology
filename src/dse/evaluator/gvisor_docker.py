import asyncio
import hashlib
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dse.contracts.evaluation import HiddenEvaluationRequest
from dse.contracts.evaluator_worker import WorkerEvaluationAggregate
from dse.contracts.experiment import SandboxPolicyConfig
from dse.contracts.sandbox import SandboxAttestation, SandboxCheck
from dse.evaluator.suite_store import HiddenSuiteBundle


class GVisorBackendError(RuntimeError):
    """Raised when the concrete gVisor/Docker evaluator fails closed."""


@dataclass(frozen=True)
class _AttachedResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_ms: int
    timed_out: bool = False
    output_exceeded: bool = False


class GVisorDockerBackend:
    """Concrete hardened evaluator backend using Docker with the runsc runtime."""

    backend = "external-hardened"
    backend_version = "gvisor-docker-m18.2-v1"

    def __init__(
        self,
        *,
        runtime_image: str = "gcr.io/distroless/python3-debian13:nonroot",
        docker_binary: str = "docker",
        runsc_binary: str = "runsc",
        python_binary: str = "/usr/bin/python",
        secret_canary_env: str = "DSE_SECRET_CANARY",
        max_suite_bytes: int = 4_194_304,
    ) -> None:
        if not runtime_image:
            raise ValueError("runtime_image must not be empty")
        if max_suite_bytes < 1024:
            raise ValueError("max_suite_bytes must be at least 1024")

        self.runtime_image = runtime_image
        self.docker_binary = docker_binary
        self.runsc_binary = runsc_binary
        self.python_binary = python_binary
        self.secret_canary_env = secret_canary_env
        self.max_suite_bytes = max_suite_bytes
        self._attestations: dict[str, SandboxAttestation] = {}

        self._runtime_identity = self._measure_runtime_identity()
        self.runtime_build_sha256 = self._identity_hash(self._runtime_identity)

    async def attest(
        self,
        policy: SandboxPolicyConfig,
        policy_hash: str,
    ) -> SandboxAttestation:
        return await asyncio.to_thread(
            self._attest_sync,
            policy,
            policy_hash,
        )

    async def evaluate(
        self,
        request: HiddenEvaluationRequest,
        suite: HiddenSuiteBundle,
    ) -> WorkerEvaluationAggregate:
        return await asyncio.to_thread(
            self._evaluate_sync,
            request,
            suite,
        )

    def _attest_sync(
        self,
        policy: SandboxPolicyConfig,
        policy_hash: str,
    ) -> SandboxAttestation:
        self._require_supported_policy(policy)
        self._assert_runtime_build_stable()

        cached = self._attestations.get(policy_hash)
        if cached is not None:
            return cached

        checks = [
            self._probe_basic_isolation(policy),
            self._probe_cpu_limit(policy),
            self._probe_memory_limit(policy),
            self._probe_pid_limit(policy),
            self._probe_disk_limit(policy),
            self._probe_output_limit(policy),
            self._probe_wall_timeout(policy),
            self._probe_ephemeral_filesystem(policy),
        ]
        flattened: list[SandboxCheck] = []
        for item in checks:
            if isinstance(item, list):
                flattened.extend(item)
            else:
                flattened.append(item)

        attestation_id = "gvisor-" + hashlib.sha256(
            (
                policy_hash
                + self.runtime_build_sha256
                + "|".join(
                    f"{check.name}:{int(check.passed)}:{check.detail}"
                    for check in flattened
                )
            ).encode("utf-8")
        ).hexdigest()[:48]

        attestation = SandboxAttestation(
            attestation_id=attestation_id,
            evidence_kind="runtime-measured",
            backend=self.backend,
            backend_version=self.backend_version,
            policy_hash=policy_hash,
            checks=flattened,
        )
        self._attestations[policy_hash] = attestation
        return attestation

    def _evaluate_sync(
        self,
        request: HiddenEvaluationRequest,
        suite: HiddenSuiteBundle,
    ) -> WorkerEvaluationAggregate:
        self._require_supported_policy(request.policy)
        self._assert_runtime_build_stable()

        payload_hash = hashlib.sha256(suite.payload).hexdigest()
        if payload_hash != suite.suite_hash:
            raise GVisorBackendError("hidden suite payload hash mismatch")
        if suite.suite_hash != request.plan.suite_hash:
            raise GVisorBackendError("hidden suite/request hash mismatch")
        if len(suite.payload) > self.max_suite_bytes:
            raise GVisorBackendError("hidden suite exceeds backend byte limit")

        token = uuid.uuid4().hex[:12]
        stage_name = f"dse-m18-2-stage-{token}"
        run_name = f"dse-m18-2-eval-{token}"
        staged_image = f"dse-m18-2-private-eval:{token}"

        with tempfile.TemporaryDirectory(prefix="dse-m18-2-stage-") as tmp:
            root = Path(tmp)
            (root / "suite.py").write_bytes(suite.payload)
            (root / "request.json").write_text(
                json.dumps(
                    request.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )

            try:
                self._run_checked(
                    [
                        self.docker_binary,
                        "create",
                        "--name",
                        stage_name,
                        self.runtime_image,
                    ]
                )
                self._run_checked(
                    [
                        self.docker_binary,
                        "cp",
                        f"{root}{os.sep}.",
                        f"{stage_name}:/dse_eval",
                    ]
                )
                self._run_checked(
                    [
                        self.docker_binary,
                        "commit",
                        stage_name,
                        staged_image,
                    ]
                )
                self._remove_container(stage_name)

                self._create_runtime_container(
                    run_name,
                    request.policy,
                    image=staged_image,
                    python_args=[
                        "-I",
                        "-B",
                        "/dse_eval/suite.py",
                        "/dse_eval/request.json",
                    ],
                )
                started = self._run_attached(
                    run_name,
                    wall_timeout=float(request.policy.wall_timeout_seconds),
                    output_limit=request.policy.output_bytes,
                )
                if started.timed_out:
                    raise GVisorBackendError(
                        "hidden suite exceeded wall-time limit"
                    )
                if started.output_exceeded:
                    raise GVisorBackendError(
                        "hidden suite exceeded output-byte limit"
                    )
                if started.returncode != 0:
                    raise GVisorBackendError(
                        "hidden suite exited unsuccessfully"
                    )

                aggregate_payload = self._parse_aggregate(started.stdout)
                aggregate_payload["duration_ms"] = started.duration_ms
                try:
                    aggregate = WorkerEvaluationAggregate.model_validate(
                        aggregate_payload
                    )
                except Exception as error:
                    raise GVisorBackendError(
                        "hidden suite returned an invalid aggregate"
                    ) from error
                if aggregate.total_cases != suite.total_cases:
                    raise GVisorBackendError(
                        "hidden suite aggregate case count drift"
                    )
                return aggregate
            finally:
                self._remove_container(run_name)
                self._remove_container(stage_name)
                self._remove_image(staged_image)
                self._require_container_absent(run_name)
                self._require_container_absent(stage_name)

    def _probe_basic_isolation(
        self,
        policy: SandboxPolicyConfig,
    ) -> list[SandboxCheck]:
        name = self._name("basic")
        script = f"""
import json
import os
import socket

shells = ["/bin/sh", "/bin/bash", "/busybox/sh", "/usr/bin/sh"]
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("1.1.1.1", 53))
except OSError:
    network_disabled = True
else:
    network_disabled = False
finally:
    sock.close()

print(json.dumps({{
    "network_disabled": network_disabled,
    "secret_absent": {self.secret_canary_env!r} not in os.environ,
    "shell_absent": not any(os.path.exists(path) for path in shells),
    "docker_socket_absent": not os.path.exists("/var/run/docker.sock"),
    "uid": os.geteuid(),
}}, sort_keys=True))
"""
        self._create_runtime_container(
            name,
            policy,
            python_args=["-I", "-B", "-c", script],
        )
        inspect = self._inspect_container(name)
        result = self._run_attached(
            name,
            wall_timeout=min(10.0, float(policy.wall_timeout_seconds)),
            output_limit=min(policy.output_bytes, 65_536),
        )
        try:
            measured = self._json_from_stdout(result)
            host = inspect["HostConfig"]
            mounts = inspect.get("Mounts", [])
            expected_memory = policy.memory_mb * 1024 * 1024
            expected_nano_cpus = round(
                self._cpu_quota(policy) * 1_000_000_000
            )

            config_ok = (
                host.get("Runtime") == "runsc"
                and host.get("NetworkMode") == "none"
                and host.get("ReadonlyRootfs") is True
                and "ALL" in (host.get("CapDrop") or [])
                and "no-new-privileges" in (host.get("SecurityOpt") or [])
                and host.get("PidsLimit") == policy.pids_max
                and host.get("Memory") == expected_memory
                and host.get("MemorySwap") == expected_memory
                and host.get("NanoCpus") == expected_nano_cpus
                and inspect["Config"].get("User") == "65532:65532"
                and not any(
                    mount.get("Type") in {"bind", "volume"}
                    for mount in mounts
                )
            )
            no_host_mounts = config_ok and measured["docker_socket_absent"]
            return [
                SandboxCheck(
                    name="network_disabled",
                    passed=bool(measured["network_disabled"]) and config_ok,
                    detail=(
                        "runsc container measured egress denial "
                        "with network=none"
                    ),
                ),
                SandboxCheck(
                    name="no_host_mounts",
                    passed=no_host_mounts,
                    detail=(
                        "inspect found no bind/volume mounts "
                        "or Docker socket"
                    ),
                ),
                SandboxCheck(
                    name="no_secrets",
                    passed=bool(measured["secret_absent"]),
                    detail="host secret canary name was absent inside runtime",
                ),
                SandboxCheck(
                    name="no_shell",
                    passed=bool(measured["shell_absent"]),
                    detail=(
                        "common shell paths were absent "
                        "in final runtime image"
                    ),
                ),
            ]
        finally:
            self._remove_container(name)
            self._require_container_absent(name)

    def _probe_cpu_limit(self, policy: SandboxPolicyConfig) -> SandboxCheck:
        name = self._name("cpu")
        workers = max(2, min(8, policy.pids_max - 2))
        duration = min(2.0, max(1.0, policy.wall_timeout_seconds / 2))
        script = f"""
import json
import os
import resource
import time

workers = {workers}
duration = {duration!r}
children = []
start = time.monotonic()
for _ in range(workers):
    pid = os.fork()
    if pid == 0:
        deadline = time.monotonic() + duration
        value = 0
        while time.monotonic() < deadline:
            value = (value * 33 + 17) % 1000003
        os._exit(value & 0)
    children.append(pid)

for pid in children:
    os.waitpid(pid, 0)
elapsed = max(time.monotonic() - start, 0.001)
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
cpu = usage.ru_utime + usage.ru_stime
print(json.dumps({{"cpu_seconds": cpu, "wall_seconds": elapsed}}))
"""
        self._create_runtime_container(
            name,
            policy,
            python_args=["-I", "-B", "-c", script],
        )
        try:
            result = self._run_attached(
                name,
                wall_timeout=min(
                    float(policy.wall_timeout_seconds),
                    duration + 3.0,
                ),
                output_limit=min(policy.output_bytes, 65_536),
            )
            measured = self._json_from_stdout(result)
            observed_cores = (
                measured["cpu_seconds"] / measured["wall_seconds"]
            )
            quota = self._cpu_quota(policy)
            tolerance = max(0.35, quota * 0.35)
            passed = (
                not result.timed_out
                and not result.output_exceeded
                and result.returncode == 0
                and observed_cores <= quota + tolerance
            )
            return SandboxCheck(
                name="cpu_limit_enforced",
                passed=passed,
                detail=(
                    f"busy-loop measured {observed_cores:.3f} CPU cores "
                    f"against quota {quota:.3f}"
                ),
            )
        finally:
            self._remove_container(name)
            self._require_container_absent(name)

    def _probe_memory_limit(
        self,
        policy: SandboxPolicyConfig,
    ) -> SandboxCheck:
        name = self._name("memory")
        allocation_mb = policy.memory_mb + max(64, policy.memory_mb // 2)
        script = f"""
chunks = []
for _ in range({allocation_mb}):
    chunks.append(bytearray(1024 * 1024))
print(len(chunks))
"""
        self._create_runtime_container(
            name,
            policy,
            python_args=["-I", "-B", "-c", script],
        )
        try:
            result = self._run_attached(
                name,
                wall_timeout=min(
                    15.0,
                    float(policy.wall_timeout_seconds),
                ),
                output_limit=min(policy.output_bytes, 65_536),
            )
            passed = (
                not result.timed_out
                and not result.output_exceeded
                and result.returncode != 0
            )
            return SandboxCheck(
                name="memory_limit_enforced",
                passed=passed,
                detail=(
                    f"allocation above {policy.memory_mb} MiB "
                    "was terminated"
                ),
            )
        finally:
            self._remove_container(name)
            self._require_container_absent(name)

    def _probe_pid_limit(self, policy: SandboxPolicyConfig) -> SandboxCheck:
        name = self._name("pid")
        attempts = max(policy.pids_max * 2, policy.pids_max + 8)
        script = f"""
import json
import os
import signal
import time

children = []
limited = False
try:
    for _ in range({attempts}):
        try:
            pid = os.fork()
        except OSError:
            limited = True
            break
        if pid == 0:
            time.sleep(2)
            os._exit(0)
        children.append(pid)
finally:
    for pid in children:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for pid in children:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
print(json.dumps({{"children_started": len(children), "limited": limited}}))
"""
        self._create_runtime_container(
            name,
            policy,
            python_args=["-I", "-B", "-c", script],
        )
        try:
            result = self._run_attached(
                name,
                wall_timeout=min(
                    15.0,
                    float(policy.wall_timeout_seconds),
                ),
                output_limit=min(policy.output_bytes, 65_536),
            )
            measured = self._json_from_stdout(result)
            passed = (
                not result.timed_out
                and not result.output_exceeded
                and result.returncode == 0
                and bool(measured["limited"])
                and measured["children_started"] < attempts
            )
            return SandboxCheck(
                name="pid_limit_enforced",
                passed=passed,
                detail=(
                    f"fork pressure stopped after "
                    f"{measured['children_started']} children "
                    f"with pids_max={policy.pids_max}"
                ),
            )
        finally:
            self._remove_container(name)
            self._require_container_absent(name)

    def _probe_disk_limit(self, policy: SandboxPolicyConfig) -> SandboxCheck:
        name = self._name("disk")
        write_mb = policy.disk_mb + max(8, policy.disk_mb // 4)
        script = f"""
import json

limited = False
written = 0
try:
    with open("/tmp/fill", "wb") as handle:
        block = b"x" * (1024 * 1024)
        for _ in range({write_mb}):
            handle.write(block)
            handle.flush()
            written += 1
except OSError:
    limited = True
print(json.dumps({{"limited": limited, "written_mb": written}}))
"""
        self._create_runtime_container(
            name,
            policy,
            python_args=["-I", "-B", "-c", script],
        )
        try:
            result = self._run_attached(
                name,
                wall_timeout=min(
                    15.0,
                    float(policy.wall_timeout_seconds),
                ),
                output_limit=min(policy.output_bytes, 65_536),
            )
            measured = self._json_from_stdout(result)
            passed = (
                not result.timed_out
                and not result.output_exceeded
                and result.returncode == 0
                and bool(measured["limited"])
                and measured["written_mb"] <= policy.disk_mb
            )
            return SandboxCheck(
                name="disk_limit_enforced",
                passed=passed,
                detail=(
                    f"tmpfs write failed at "
                    f"{measured['written_mb']} MiB"
                ),
            )
        finally:
            self._remove_container(name)
            self._require_container_absent(name)

    def _probe_output_limit(
        self,
        policy: SandboxPolicyConfig,
    ) -> SandboxCheck:
        name = self._name("output")
        script = """
import os
while True:
    os.write(1, b"x" * 4096)
"""
        self._create_runtime_container(
            name,
            policy,
            python_args=["-I", "-B", "-c", script],
        )
        result = self._run_attached(
            name,
            wall_timeout=float(policy.wall_timeout_seconds),
            output_limit=policy.output_bytes,
        )
        self._remove_container(name)
        cleaned = not self._container_exists(name)
        return SandboxCheck(
            name="output_limit_enforced",
            passed=result.output_exceeded and cleaned,
            detail=(
                "host attach path killed runtime "
                "at configured output ceiling"
            ),
        )

    def _probe_wall_timeout(
        self,
        policy: SandboxPolicyConfig,
    ) -> SandboxCheck:
        name = self._name("wall")
        script = f"""
import time
time.sleep({policy.wall_timeout_seconds + 5})
"""
        self._create_runtime_container(
            name,
            policy,
            python_args=["-I", "-B", "-c", script],
        )
        result = self._run_attached(
            name,
            wall_timeout=float(policy.wall_timeout_seconds),
            output_limit=min(policy.output_bytes, 65_536),
        )
        self._remove_container(name)
        cleaned = not self._container_exists(name)
        return SandboxCheck(
            name="wall_timeout_enforced",
            passed=result.timed_out and cleaned,
            detail="host watchdog killed and removed over-time runtime",
        )

    def _probe_ephemeral_filesystem(
        self,
        policy: SandboxPolicyConfig,
    ) -> SandboxCheck:
        first = self._name("ephemeral-a")
        second = self._name("ephemeral-b")
        try:
            self._create_runtime_container(
                first,
                policy,
                python_args=[
                    "-I",
                    "-B",
                    "-c",
                    (
                        'from pathlib import Path; '
                        'Path("/tmp/dse-marker").write_text("x")'
                    ),
                ],
            )
            written = self._run_attached(
                first,
                wall_timeout=float(policy.wall_timeout_seconds),
                output_limit=min(policy.output_bytes, 65_536),
            )
            self._remove_container(first)

            self._create_runtime_container(
                second,
                policy,
                python_args=[
                    "-I",
                    "-B",
                    "-c",
                    (
                        'from pathlib import Path; '
                        'print("present" if '
                        'Path("/tmp/dse-marker").exists() else "absent")'
                    ),
                ],
            )
            observed = self._run_attached(
                second,
                wall_timeout=float(policy.wall_timeout_seconds),
                output_limit=min(policy.output_bytes, 65_536),
            )
            passed = (
                written.returncode == 0
                and observed.returncode == 0
                and observed.stdout.strip() == b"absent"
            )
            return SandboxCheck(
                name="ephemeral_filesystem",
                passed=passed,
                detail=(
                    "marker from destroyed container "
                    "was absent in fresh runtime"
                ),
            )
        finally:
            self._remove_container(first)
            self._remove_container(second)
            self._require_container_absent(first)
            self._require_container_absent(second)

    def _create_runtime_container(
        self,
        name: str,
        policy: SandboxPolicyConfig,
        *,
        image: str | None = None,
        python_args: list[str],
    ) -> None:
        memory = f"{policy.memory_mb}m"
        tmpfs = (
            f"/tmp:rw,nosuid,nodev,noexec,size={policy.disk_mb}m,"
            "mode=1777"
        )
        args = [
            self.docker_binary,
            "create",
            "--name",
            name,
            "--runtime=runsc",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={policy.pids_max}",
            f"--memory={memory}",
            f"--memory-swap={memory}",
            f"--cpus={self._cpu_quota(policy):.6f}",
            "--user=65532:65532",
            "--log-driver=none",
            "--stop-timeout=1",
            "--tmpfs",
            tmpfs,
            "--entrypoint",
            self.python_binary,
            image or self.runtime_image,
            *python_args,
        ]
        self._run_checked(args)

    def _run_attached(
        self,
        name: str,
        *,
        wall_timeout: float,
        output_limit: int,
    ) -> _AttachedResult:
        started = time.monotonic()
        proc = subprocess.Popen(
            [self.docker_binary, "start", "--attach", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.stdout is None or proc.stderr is None:
            proc.kill()
            raise GVisorBackendError(
                "unable to attach evaluator streams"
            )

        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        stdout = bytearray()
        stderr = bytearray()
        total = 0
        timed_out = False
        output_exceeded = False
        deadline = started + wall_timeout

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    self._remove_container(name)
                    break

                events = selector.select(
                    timeout=min(0.1, remaining)
                )
                if not events and proc.poll() is not None:
                    events = [
                        (key, selectors.EVENT_READ)
                        for key in list(
                            selector.get_map().values()
                        )
                    ]

                for key, _ in events:
                    chunk = os.read(
                        key.fileobj.fileno(),
                        8192,
                    )
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    target = (
                        stdout
                        if key.data == "stdout"
                        else stderr
                    )
                    if len(target) < output_limit + 1:
                        target.extend(
                            chunk[
                                : output_limit
                                + 1
                                - len(target)
                            ]
                        )
                    if total > output_limit:
                        output_exceeded = True
                        self._remove_container(name)
                        break
                if output_exceeded:
                    break

            if timed_out or output_exceeded:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            else:
                proc.wait(timeout=2)
        finally:
            selector.close()

        duration_ms = int(
            (time.monotonic() - started) * 1000
        )
        return _AttachedResult(
            returncode=(
                proc.returncode
                if proc.returncode is not None
                else -1
            ),
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            duration_ms=duration_ms,
            timed_out=timed_out,
            output_exceeded=output_exceeded,
        )

    def _json_from_stdout(
        self,
        result: _AttachedResult,
    ) -> dict[str, Any]:
        if (
            result.timed_out
            or result.output_exceeded
            or result.returncode != 0
        ):
            raise GVisorBackendError(
                "runtime probe failed before JSON result"
            )
        try:
            lines = [
                line
                for line in result.stdout.splitlines()
                if line.strip()
            ]
            payload = json.loads(lines[-1])
        except (
            IndexError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise GVisorBackendError(
                "runtime probe returned invalid JSON"
            ) from error
        if not isinstance(payload, dict):
            raise GVisorBackendError(
                "runtime probe JSON must be an object"
            )
        return payload

    def _parse_aggregate(
        self,
        stdout: bytes,
    ) -> dict[str, Any]:
        try:
            lines = [
                line
                for line in stdout.splitlines()
                if line.strip()
            ]
            if len(lines) != 1:
                raise ValueError(
                    "aggregate output must contain exactly one line"
                )
            payload = json.loads(lines[0])
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            raise GVisorBackendError(
                "hidden suite output must be one aggregate JSON object"
            ) from error
        if not isinstance(payload, dict):
            raise GVisorBackendError(
                "hidden suite aggregate must be a JSON object"
            )
        expected = {
            "passed_cases",
            "failed_cases",
            "total_cases",
        }
        if set(payload) != expected:
            raise GVisorBackendError(
                "hidden suite aggregate exposed unexpected fields"
            )
        return payload

    def _require_supported_policy(
        self,
        policy: SandboxPolicyConfig,
    ) -> None:
        if policy.backend != self.backend:
            raise GVisorBackendError(
                "policy backend is not external-hardened"
            )
        if (
            policy.network_enabled
            or policy.host_mounts_enabled
            or policy.secrets_enabled
            or policy.shell_enabled
        ):
            raise GVisorBackendError(
                "policy requests unsupported unsafe capability"
            )

    def _measure_runtime_identity(
        self,
    ) -> dict[str, Any]:
        runsc_path = shutil.which(self.runsc_binary)
        if runsc_path is None:
            raise GVisorBackendError(
                "runsc binary is not installed"
            )
        if shutil.which(self.docker_binary) is None:
            raise GVisorBackendError(
                "docker binary is not installed"
            )

        image_id = self._run_checked(
            [
                self.docker_binary,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                self.runtime_image,
            ]
        ).stdout.strip()
        repo_digests = self._run_checked(
            [
                self.docker_binary,
                "image",
                "inspect",
                "--format",
                "{{json .RepoDigests}}",
                self.runtime_image,
            ]
        ).stdout.strip()
        runsc_version = self._run_checked(
            [self.runsc_binary, "--version"]
        ).stdout.strip()
        docker_version = self._run_checked(
            [
                self.docker_binary,
                "version",
                "--format",
                "{{.Server.Version}}",
            ]
        ).stdout.strip()

        binaries: dict[str, str] = {}
        candidates = [Path(runsc_path)]
        shim = shutil.which("containerd-shim-runsc-v1")
        if shim is not None:
            candidates.append(Path(shim))
        for binary in candidates:
            if binary.is_file():
                binaries[str(binary)] = self._sha256_file(
                    binary
                )

        sidecar_dir = (
            Path(runsc_path).resolve().parent
            / "gvisor-bin"
        )
        if sidecar_dir.is_dir():
            for child in sorted(
                sidecar_dir.rglob("*")
            ):
                if child.is_file():
                    binaries[str(child)] = (
                        self._sha256_file(child)
                    )

        if not image_id.startswith("sha256:"):
            raise GVisorBackendError(
                "runtime image does not have immutable image id"
            )

        return {
            "runtime_image": self.runtime_image,
            "image_id": image_id,
            "repo_digests": repo_digests,
            "runsc_version": runsc_version,
            "docker_server_version": docker_version,
            "binary_sha256": binaries,
        }

    def _assert_runtime_build_stable(self) -> None:
        current = self._measure_runtime_identity()
        if (
            self._identity_hash(current)
            != self.runtime_build_sha256
        ):
            raise GVisorBackendError(
                "runtime build provenance changed"
            )

    @staticmethod
    def _identity_hash(
        identity: dict[str, Any],
    ) -> str:
        payload = json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(
                lambda: handle.read(1024 * 1024),
                b"",
            ):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _cpu_quota(
        policy: SandboxPolicyConfig,
    ) -> float:
        return max(
            0.01,
            policy.cpu_seconds
            / policy.wall_timeout_seconds,
        )

    def _inspect_container(
        self,
        name: str,
    ) -> dict[str, Any]:
        output = self._run_checked(
            [self.docker_binary, "inspect", name]
        ).stdout
        try:
            payload = json.loads(output)
            value = payload[0]
        except (
            json.JSONDecodeError,
            IndexError,
            TypeError,
        ) as error:
            raise GVisorBackendError(
                "unable to inspect evaluator container"
            ) from error
        if not isinstance(value, dict):
            raise GVisorBackendError(
                "invalid Docker inspect response"
            )
        return value

    def _container_exists(self, name: str) -> bool:
        result = subprocess.run(
            [self.docker_binary, "inspect", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    def _require_container_absent(
        self,
        name: str,
    ) -> None:
        if self._container_exists(name):
            raise GVisorBackendError(
                f"evaluator container cleanup failed: {name}"
            )

    def _remove_container(self, name: str) -> None:
        subprocess.run(
            [self.docker_binary, "rm", "-f", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _remove_image(self, image: str) -> None:
        subprocess.run(
            [
                self.docker_binary,
                "image",
                "rm",
                "-f",
                image,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _run_checked(
        self,
        args: list[str],
        *,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (
            OSError,
            subprocess.TimeoutExpired,
        ) as error:
            raise GVisorBackendError(
                f"runtime command failed: {args[0]!r}"
            ) from error
        if result.returncode != 0:
            raise GVisorBackendError(
                "runtime command returned non-zero status: "
                + " ".join(args[:3])
            )
        return result

    @staticmethod
    def _name(kind: str) -> str:
        return (
            f"dse-m18-2-{kind}-"
            f"{uuid.uuid4().hex[:12]}"
        )
