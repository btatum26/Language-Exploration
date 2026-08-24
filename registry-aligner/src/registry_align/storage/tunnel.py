"""Lifecycle-managed OpenSSH tunnel for the development PostgreSQL endpoint."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from types import TracebackType

from registry_align.config import SshTunnelConfig
from registry_align.errors import DatabaseError


def port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


class SshTunnel:
    def __init__(self, config: SshTunnelConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> SshTunnel:
        if not self.config.enabled:
            return self
        if port_is_open(self.config.local_host, self.config.local_port):
            raise DatabaseError(
                f"port {self.config.local_port} is already in use; stop the existing tunnel first"
            )
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._process = subprocess.Popen(
                [self.config.executable, "-N", self.config.host],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise DatabaseError(f"could not start SSH tunnel: {exc}") from exc
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                error = self._process.stderr.read().strip() if self._process.stderr else ""
                raise DatabaseError(f"SSH tunnel exited before opening the port: {error}")
            if port_is_open(self.config.local_host, self.config.local_port):
                return self
            time.sleep(0.1)
        self.stop()
        raise DatabaseError(
            f"SSH tunnel did not open port {self.config.local_port} within "
            f"{self.config.startup_timeout_seconds:g} seconds"
        )

    def stop(self) -> None:
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        finally:
            self._process = None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
