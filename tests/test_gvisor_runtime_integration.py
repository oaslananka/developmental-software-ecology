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


def _create_probe_container(name: str, script: str) -> None:
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
            "--pids-limit=32",
            "--memory=128m",
            "--memory-swap=128m",
            "--cpus=0.5",
            "--user=65532:65532",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m",
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


def test_gvisor_runtime_is_configured_and_executes_measured_probes() -> None:
    runtimes = json.loads(
        _run(
            ["docker", "info", "--format", "{{json .Runtimes}}"]
        ).stdout
    )
    assert "runsc" in runtimes

    name = "dse-gvisor-runtime-probe"
    _remove(name)
    script = r"""
import json
import os
import signal
import socket
import time

result = {}

sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("1.1.1.1", 53))
except OSError:
    result["network_disabled"] = True
else:
    result["network_disabled"] = False
finally:
    sock.close()

result["secret_absent"] = "DSE_SECRET_CANARY" not in os.environ
result["unprivileged_uid"] = os.geteuid() != 0

disk_limited = False
try:
    with open("/tmp/dse-fill", "wb") as handle:
        block = b"x" * (1024 * 1024)
        for _ in range(32):
            handle.write(block)
            handle.flush()
except OSError:
    disk_limited = True
result["tmpfs_disk_limited"] = disk_limited

children = []
pid_limited = False
try:
    for _ in range(64):
        try:
            pid = os.fork()
        except OSError:
            pid_limited = True
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
result["pid_limit_enforced"] = pid_limited

with open("/tmp/dse-ephemeral-marker", "w", encoding="utf-8") as handle:
    handle.write("marker")

print(json.dumps(result, sort_keys=True))
"""

    try:
        _create_probe_container(name, script)
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

        started = _run(["docker", "start", "-a", name], timeout=30.0)
        measured = json.loads(started.stdout.strip().splitlines()[-1])

        assert measured == {
            "network_disabled": True,
            "pid_limit_enforced": True,
            "secret_absent": True,
            "tmpfs_disk_limited": True,
            "unprivileged_uid": True,
        }
    finally:
        _remove(name)


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
        _run(["docker", "start", "-a", first])

        _create_probe_container(
            second,
            (
                'from pathlib import Path; '
                'print("present" if Path("/tmp/dse-marker").exists() else "absent")'
            ),
        )
        result = _run(["docker", "start", "-a", second])
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
    assert "docker dmesg" not in source
    assert " dmesg" not in source
