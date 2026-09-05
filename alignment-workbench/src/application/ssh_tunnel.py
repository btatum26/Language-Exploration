"""Lifecycle-managed OpenSSH tunnel for PostgreSQL access."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from types import TracebackType

from sqlalchemy.engine import make_url

from application.errors import WorkbenchConfigurationError, WorkbenchStartupError


@dataclass(frozen=True, slots=True)
class SshTunnelConfig:
    """OpenSSH process settings plus the local endpoint used for readiness checks."""

    ssh_alias: str
    executable: str
    local_host: str
    local_port: int
    startup_timeout_seconds: float

    @classmethod
    def from_mapping(
        cls,
        database_url: str,
        values: Mapping[str, str | None],
    ) -> SshTunnelConfig:
        try:
            url = make_url(database_url)
        except Exception as exc:
            raise WorkbenchConfigurationError(
                "database URL must be valid before configuring the SSH tunnel"
            ) from exc
        if url.drivername != "postgresql+psycopg":
            raise WorkbenchConfigurationError(
                "database URL must use postgresql+psycopg before configuring the SSH tunnel"
            )
        try:
            local_host = url.host
            local_port = url.port or 5432
        except ValueError as exc:
            raise WorkbenchConfigurationError(
                "database URL must name a valid local tunnel endpoint"
            ) from exc
        if not local_host:
            raise WorkbenchConfigurationError("database URL must name the tunnel's local host")

        ssh_alias = _setting(values, "ALIGNMENT_WORKBENCH_SSH_ALIAS", "registry-db")
        executable = _setting(values, "ALIGNMENT_WORKBENCH_SSH_EXECUTABLE", "ssh")
        timeout_value = _setting(
            values,
            "ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS",
            "5",
        )
        try:
            startup_timeout_seconds = float(timeout_value)
        except ValueError as exc:
            raise WorkbenchConfigurationError(
                "ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS must be a number"
            ) from exc
        if not isfinite(startup_timeout_seconds) or startup_timeout_seconds <= 0:
            raise WorkbenchConfigurationError(
                "ALIGNMENT_WORKBENCH_SSH_STARTUP_TIMEOUT_SECONDS must be positive"
            )

        return cls(
            ssh_alias=ssh_alias,
            executable=executable,
            local_host=local_host,
            local_port=local_port,
            startup_timeout_seconds=startup_timeout_seconds,
        )


def _setting(values: Mapping[str, str | None], name: str, default: str) -> str:
    raw_value = values.get(name)
    value = default if raw_value is None else raw_value.strip()
    if not value:
        raise WorkbenchConfigurationError(f"{name} must not be empty")
    return value


def port_is_open(host: str, port: int) -> bool:
    """Return whether the configured local forwarding endpoint accepts connections."""

    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


class SshTunnel:
    """Own one hidden OpenSSH process for a bounded application or tool lifetime."""

    def __init__(self, config: SshTunnelConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[str] | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> SshTunnel:
        if self.running:
            return self
        if port_is_open(self.config.local_host, self.config.local_port):
            raise WorkbenchStartupError(
                "configured database tunnel endpoint "
                f"{self.config.local_host}:{self.config.local_port} is already in use"
            )

        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._process = subprocess.Popen(
                [
                    self.config.executable,
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-N",
                    self.config.ssh_alias,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=creation_flags,
            )
        except OSError as exc:
            self._process = None
            raise WorkbenchStartupError(
                "could not start the configured SSH tunnel executable"
            ) from exc

        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            process = self._process
            if process is None or process.poll() is not None:
                self.stop()
                raise WorkbenchStartupError(
                    "SSH tunnel exited before opening the configured database endpoint"
                )
            if port_is_open(self.config.local_host, self.config.local_port):
                return self
            time.sleep(0.1)

        self.stop()
        raise WorkbenchStartupError(
            "SSH tunnel did not open the configured database endpoint within "
            f"{self.config.startup_timeout_seconds:g} seconds"
        )

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def __enter__(self) -> SshTunnel:
        return self.start()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
