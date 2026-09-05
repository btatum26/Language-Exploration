from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from application.errors import WorkbenchStartupError
from application.ssh_tunnel import SshTunnel, SshTunnelConfig


@pytest.fixture(scope="session")
def database_tunnel() -> Iterator[None]:
    """Own the SSH tunnel whenever explicit PostgreSQL test URLs are selected."""

    urls = tuple(
        value
        for name in ("TEST_DATABASE_URL", "TEST_RUNTIME_DATABASE_URL")
        if (value := os.getenv(name))
    )
    if not urls:
        yield
        return

    configs = tuple(SshTunnelConfig.from_mapping(url, os.environ) for url in urls)
    endpoints = {(config.local_host, config.local_port) for config in configs}
    if len(endpoints) != 1:
        pytest.fail("PostgreSQL test URLs must use the same local SSH tunnel endpoint")

    tunnel = SshTunnel(configs[0])
    try:
        tunnel.start()
    except WorkbenchStartupError as exc:
        pytest.fail(str(exc))
    try:
        yield
    finally:
        tunnel.stop()
