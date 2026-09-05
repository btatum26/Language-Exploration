from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

import pytest

import application.ssh_tunnel as tunnel_module
from application import WorkbenchStartupError
from application.ssh_tunnel import SshTunnel, SshTunnelConfig


@dataclass
class FakeProcess:
    return_code: int | None = None
    terminate_calls: int = 0
    kill_calls: int = 0
    wait_calls: int = 0

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.return_code = -9

    def wait(self, *, timeout: int) -> int:
        del timeout
        self.wait_calls += 1
        self.return_code = 0
        return 0


def config(*, timeout: float = 5.0) -> SshTunnelConfig:
    return SshTunnelConfig(
        ssh_alias="registry-db",
        executable="ssh",
        local_host="127.0.0.1",
        local_port=5433,
        startup_timeout_seconds=timeout,
    )


def test_tunnel_starts_hidden_waits_for_readiness_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    popen_call: dict[str, Any] = {}
    endpoint_states = iter((False, True))

    def popen(command: list[str], **options: Any) -> FakeProcess:
        popen_call["command"] = command
        popen_call.update(options)
        return process

    monkeypatch.setattr(tunnel_module, "port_is_open", lambda _host, _port: next(endpoint_states))
    monkeypatch.setattr(tunnel_module.subprocess, "Popen", popen)
    monkeypatch.setattr(tunnel_module.sys, "platform", "win32")

    tunnel = SshTunnel(config())
    assert tunnel.start() is tunnel
    assert tunnel.running
    assert popen_call["command"] == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        "registry-db",
    ]
    assert popen_call["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert tunnel.start() is tunnel

    tunnel.stop()
    tunnel.stop()
    assert process.terminate_calls == 1
    assert process.wait_calls == 1
    assert not tunnel.running


def test_tunnel_reports_a_missing_ssh_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tunnel_module, "port_is_open", lambda _host, _port: False)

    def missing_executable(*_args: Any, **_kwargs: Any) -> FakeProcess:
        raise FileNotFoundError

    monkeypatch.setattr(tunnel_module.subprocess, "Popen", missing_executable)

    with pytest.raises(WorkbenchStartupError, match="could not start"):
        SshTunnel(config()).start()


def test_tunnel_rejects_an_endpoint_that_is_already_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tunnel_module, "port_is_open", lambda _host, _port: True)

    with pytest.raises(WorkbenchStartupError, match="127.0.0.1:5433 is already in use"):
        SshTunnel(config()).start()


def test_tunnel_reports_early_ssh_exit_and_clears_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(return_code=255)
    monkeypatch.setattr(tunnel_module, "port_is_open", lambda _host, _port: False)
    monkeypatch.setattr(tunnel_module.subprocess, "Popen", lambda *_args, **_kwargs: process)

    tunnel = SshTunnel(config())
    with pytest.raises(WorkbenchStartupError, match="exited before opening"):
        tunnel.start()

    assert not tunnel.running


def test_tunnel_timeout_terminates_the_ssh_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess()
    clock = iter((0.0, 1.0))
    monkeypatch.setattr(tunnel_module, "port_is_open", lambda _host, _port: False)
    monkeypatch.setattr(tunnel_module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(tunnel_module.time, "monotonic", lambda: next(clock))

    tunnel = SshTunnel(config(timeout=0.5))
    with pytest.raises(WorkbenchStartupError, match="within 0.5 seconds"):
        tunnel.start()

    assert process.terminate_calls == 1
    assert process.wait_calls == 1
    assert not tunnel.running
