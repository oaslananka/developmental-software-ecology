import json
import os
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("DSE_GVISOR_INTEGRATION") != "1",
    reason="requires a host with Docker + gVisor runsc configured",
)

IMAGE = os.environ.get("DSE_GVISOR_PROBE_IMAGE", "python:3.12-slim")
PID_PROBE_MEMORY = "2g"
# Keep the control ceiling well above the 64-fork workload so the A/B probe
# isolates PID pressure rather than gVisor/runtime-internal task overhead.
PID_PROBE_CONTROL_LIMIT = 512


def _run(
    args: list[str],
    *,
    timeout: float = 30.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {args!r}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _create_probe_container(
    name: str,
    script: str,
    *,
    memory: str = "128m",
    pids_limit: int = 32,
    tmpfs_size: str = "16m",
) -> None:
    _run(
        [
            "docker",
            "create",
            "--name",
            name,
            "--runtime=runsc",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={pids_limit}",
            f"--memory={memory}",
            f"--memory-swap={memory}",
            "--cpus=0.5",
            "--user=65532:65532",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,noexec,size={tmpfs_size}",
            IMAGE,
            "python",
            "-I",
            "-B",
            "-c",
            script,
        ]
    )


def _remove(name: str) -> None:
    _run(["docker", "rm", "-f", name], check=False)


def _start(
    name: str,
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    result = _run(
        ["docker", "start", "-a", name],
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        inspect = _run(
            ["docker", "inspect", name],
            check=False,
        )
        logs = _run(
            ["docker", "logs", name],
            check=False,
        )
        raise AssertionError(
            f"container {name!r} exited with {result.returncode}\n"
            f"attach stdout={result.stdout}\n"
            f"attach stderr={result.stderr}\n"
            f"inspect={inspect.stdout}\n"
            f"logs stdout={logs.stdout}\n"
            f"logs stderr={logs.stderr}"
        )
    return result


def _run_json_probe(
    name: str,
    script: str,
    *,
    memory: str = "128m",
    pids_limit: int = 32,
    tmpfs_size: str = "16m",
) -> dict:
    _remove(name)
    try:
        _create_probe_container(
            name,
            script,
            memory=memory,
            pids_limit=pids_limit,
            tmpfs_size=tmpfs_size,
        )
        result = _start(name)
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        _remove(name)


def test_gvisor_runtime_configuration_is_fail_closed() -> None:
    runtimes = json.loads(
        _run(
            ["docker", "info", "--format", "{{json .Runtimes}}"]
        ).stdout
    )
    assert "runsc" in runtimes

    name = "dse-gvisor-config-probe"
    _remove(name)
    try:
        _create_probe_container(
            name,
            'import json; print(json.dumps({"started": True}))',
        )
        inspect = json.loads(_run(["docker", "inspect", name]).stdout)[0]
        host = inspect["HostConfig"]

        assert host["Runtime"] == "runsc"
        assert host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True
        assert "ALL" in host["CapDrop"]
        assert "no-new-privileges" in host["SecurityOpt"]
        assert host["PidsLimit"] == 32
        assert host["Memory"] == 128 * 1024 * 1024
        assert host["MemorySwap"] == 128 * 1024 * 1024
        assert host["NanoCpus"] == 500_000_000
        assert inspect["Config"]["User"] == "65532:65532"
        assert "/tmp" in host["Tmpfs"]
        assert "size=16m" in host["Tmpfs"]["/tmp"]
        assert not any(
            mount["Type"] in {"bind", "volume"}
            for mount in inspect["Mounts"]
        )

        measured = json.loads(
            _start(name).stdout.strip().splitlines()[-1]
        )
        assert measured == {"started": True}
    finally:
        _remove(name)


def test_gvisor_network_secret_and_identity_are_measured() -> None:
    measured = _run_json_probe(
        "dse-gvisor-network-identity-probe",
        r"""
import json
import os
import socket

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

print(json.dumps({
    "network_disabled": network_disabled,
    "secret_absent": "DSE_SECRET_CANARY" not in os.environ,
    "unprivileged_uid": os.geteuid() != 0,
}, sort_keys=True))
""",
    )

    assert measured == {
        "network_disabled": True,
        "secret_absent": True,
        "unprivileged_uid": True,
    }


def test_gvisor_tmpfs_disk_limit_is_measured() -> None:
    measured = _run_json_probe(
        "dse-gvisor-disk-probe",
        r"""
import json

limited = False
try:
    with open("/tmp/dse-fill", "wb") as handle:
        block = b"x" * (1024 * 1024)
        for _ in range(32):
            handle.write(block)
            handle.flush()
except OSError:
    limited = True

print(json.dumps({"tmpfs_disk_limited": limited}))
""",
    )

    assert measured == {"tmpfs_disk_limited": True}


def test_gvisor_pid_exhaustion_is_fail_closed_and_sandbox_local() -> None:
    script = r"""
import json
import os
import signal
import time

children = []
limited = False
try:
    for _ in range(64):
        try:
            pid = os.fork()
        except OSError:
            limited = True
            break
        if pid == 0:
            time.sleep(5)
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

print(json.dumps({
    "children_started": len(children),
    "pid_limit_enforced": limited,
}, sort_keys=True))
"""

    control = _run_json_probe(
        "dse-gvisor-pid-control",
        script,
        memory=PID_PROBE_MEMORY,
        pids_limit=PID_PROBE_CONTROL_LIMIT,
    )
    assert control == {
        "children_started": 64,
        "pid_limit_enforced": False,
    }

    limited_name = "dse-gvisor-pid-limited"
    _remove(limited_name)
    try:
        _create_probe_container(
            limited_name,
            script,
            memory=PID_PROBE_MEMORY,
            pids_limit=32,
        )
        result = _run(
            ["docker", "start", "-a", limited_name],
            timeout=30.0,
            check=False,
        )
        inspect = json.loads(
            _run(["docker", "inspect", limited_name]).stdout
        )[0]

        assert inspect["HostConfig"]["PidsLimit"] == 32
        assert inspect["HostConfig"]["Memory"] == 512 * 1024 * 1024
        assert inspect["HostConfig"]["MemorySwap"] == 512 * 1024 * 1024
        assert inspect["State"]["OOMKilled"] is False

        if result.returncode == 0:
            measured = json.loads(
                result.stdout.strip().splitlines()[-1]
            )
            assert measured["pid_limit_enforced"] is True
            assert measured["children_started"] < 64
        else:
            assert inspect["State"]["ExitCode"] == result.returncode
    finally:
        _remove(limited_name)

    recovery = _run_json_probe(
        "dse-gvisor-pid-recovery",
        'import json; print(json.dumps({"recovered": True}))',
        pids_limit=32,
    )
    assert recovery == {"recovered": True}


def test_gvisor_container_filesystem_is_ephemeral_between_containers() -> None:
    first = "dse-gvisor-ephemeral-first"
    second = "dse-gvisor-ephemeral-second"
    _remove(first)
    _remove(second)

    try:
        _create_probe_container(
            first,
            (
                'from pathlib import Path; '
                'Path("/tmp/dse-marker").write_text("present"); '
                'print("written")'
            ),
        )
        _start(first)

        _create_probe_container(
            second,
            (
                'from pathlib import Path; '
                'print("present" if Path("/tmp/dse-marker").exists() else "absent")'
            ),
        )
        result = _start(second)
        assert result.stdout.strip().splitlines()[-1] == "absent"
    finally:
        _remove(first)
        _remove(second)


def test_gvisor_memory_limit_is_actually_enforced() -> None:
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "--runtime=runsc",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--memory=64m",
            "--memory-swap=64m",
            "--cpus=0.5",
            "--user=65532:65532",
            IMAGE,
            "python",
            "-I",
            "-B",
            "-c",
            (
                "chunks=[]\n"
                "for _ in range(32):\n"
                "    chunks.append(bytearray(8 * 1024 * 1024))\n"
                "print(len(chunks))\n"
            ),
        ],
        timeout=30.0,
        check=False,
    )
    assert result.returncode != 0


def test_gvisor_probe_does_not_depend_on_dmesg_runtime_claims() -> None:
    source = Path(__file__).read_text(encoding="utf-8").lower()
    probe_source = source.split(
        "def test_gvisor_probe_does_not_depend_on_dmesg_runtime_claims",
        maxsplit=1,
    )[0]
    assert "dmesg" not in probe_source
