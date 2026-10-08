import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


_SUITE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class HiddenSuiteError(ValueError):
    """Raised when a private hidden-suite package cannot be loaded safely."""


@dataclass(frozen=True)
class HiddenSuiteBundle:
    suite_id: str
    suite_hash: str
    opportunity_spec_id: str
    opportunity_spec_hash: str
    opportunity_aggregation: str
    total_cases: int
    payload: bytes


class HiddenSuiteStore(Protocol):
    def load(self, suite_id: str) -> HiddenSuiteBundle:
        """Load one private opaque hidden-suite package."""
        ...


class FilesystemHiddenSuiteStore:
    """Load opaque suite bundles from an operator-controlled private directory."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def load(self, suite_id: str) -> HiddenSuiteBundle:
        if not _SUITE_ID.fullmatch(suite_id):
            raise HiddenSuiteError("invalid hidden suite id")

        # Never resolve user-controlled suite entries via Path: open the
        # directory and both leaves relative to already-open descriptors.
        # O_NOFOLLOW rejects symlink substitution on every checked component.
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            with ExitStack() as stack:
                root_fd = os.open(
                    self._root, flags | os.O_DIRECTORY
                )
                stack.callback(os.close, root_fd)
                suite_fd = os.open(
                    suite_id,
                    flags | os.O_DIRECTORY,
                    dir_fd=root_fd,
                )
                stack.callback(os.close, suite_fd)

                payload = self._read_regular_file(suite_fd, "suite.bundle")
                manifest_bytes = self._read_regular_file(
                    suite_fd, "manifest.json"
                )
                metadata = json.loads(manifest_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HiddenSuiteError(
                f"unable to load hidden suite {suite_id!r}"
            ) from error

        if not isinstance(metadata, dict):
            raise HiddenSuiteError("hidden suite manifest must be a JSON object")

        total_cases = metadata.get("total_cases")
        if not isinstance(total_cases, int) or isinstance(total_cases, bool):
            raise HiddenSuiteError("hidden suite total_cases must be an integer")
        if total_cases < 1:
            raise HiddenSuiteError("hidden suite total_cases must be positive")

        opportunity_spec_id = metadata.get("opportunity_spec_id")
        opportunity_spec_hash = metadata.get("opportunity_spec_hash")
        opportunity_aggregation = metadata.get("opportunity_aggregation")
        if (
            not isinstance(opportunity_spec_id, str)
            or not opportunity_spec_id
            or len(opportunity_spec_id) > 160
        ):
            raise HiddenSuiteError(
                "hidden suite opportunity_spec_id is invalid"
            )
        if (
            not isinstance(opportunity_spec_hash, str)
            or len(opportunity_spec_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in opportunity_spec_hash
            )
        ):
            raise HiddenSuiteError(
                "hidden suite opportunity_spec_hash is invalid"
            )
        if opportunity_aggregation != "population_any":
            raise HiddenSuiteError(
                "hidden suite opportunity_aggregation is invalid"
            )

        return HiddenSuiteBundle(
            suite_id=suite_id,
            suite_hash=hashlib.sha256(payload).hexdigest(),
            opportunity_spec_id=opportunity_spec_id,
            opportunity_spec_hash=opportunity_spec_hash,
            opportunity_aggregation=opportunity_aggregation,
            total_cases=total_cases,
            payload=payload,
        )

    @staticmethod
    def _read_regular_file(suite_fd: int, name: str) -> bytes:
        # O_NONBLOCK keeps a malicious FIFO from hanging before fstat can
        # reject it. Regular files ignore this flag.
        flags = (
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        )
        descriptor = os.open(name, flags, dir_fd=suite_fd)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise HiddenSuiteError("hidden suite entry is not a regular file")
            return stream.read()
