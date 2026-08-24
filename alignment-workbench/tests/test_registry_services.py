from __future__ import annotations

import os

from registry_align.config import AppConfig

import alignment_workbench.services.registry as registry_module
from alignment_workbench.services.registry import RegistryServices


def test_workbench_env_is_loaded_before_registry_configuration(
    tmp_path, monkeypatch
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "REGISTRY_ALIGN_DATABASE_URL=postgresql+psycopg://app:secret@127.0.0.1:5433/db\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("REGISTRY_ALIGN_DATABASE_URL", raising=False)
    observed: list[str | None] = []

    def load_config(_path):
        observed.append(os.getenv("REGISTRY_ALIGN_DATABASE_URL"))
        return AppConfig()

    monkeypatch.setattr(registry_module, "load_config", load_config)
    services = RegistryServices.from_environment(dotenv_path=dotenv)

    assert observed == ["postgresql+psycopg://app:secret@127.0.0.1:5433/db"]
    services.close()


def test_one_database_runtime_is_kept_until_service_shutdown(monkeypatch) -> None:
    events: list[str] = []
    engine = object()

    class Runtime:
        def __init__(self, _config) -> None:
            events.append("created")

        def __enter__(self):
            events.append("entered")
            return engine

        def __exit__(self, *_args) -> None:
            events.append("closed")

    class Backend:
        pass

    monkeypatch.setattr(registry_module, "DatabaseRuntime", Runtime)
    monkeypatch.setattr(registry_module, "PostgresUnitOfWorkFactory", lambda value: value)
    monkeypatch.setattr(registry_module, "AlignmentService", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(registry_module, "RegistryWorkbenchService", lambda _alignment: Backend())
    services = RegistryServices(config=AppConfig())

    assert services._service() is services._service()
    assert events == ["created", "entered"]
    services.close()
    services.close()
    assert events == ["created", "entered", "closed"]


def test_failed_tunnel_start_can_be_retried(monkeypatch) -> None:
    attempts = 0

    class Runtime:
        def __init__(self, _config) -> None:
            pass

        def __enter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("tunnel unavailable")
            return object()

        def __exit__(self, *_args) -> None:
            pass

    monkeypatch.setattr(registry_module, "DatabaseRuntime", Runtime)
    monkeypatch.setattr(registry_module, "PostgresUnitOfWorkFactory", lambda value: value)
    monkeypatch.setattr(registry_module, "AlignmentService", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        registry_module,
        "RegistryWorkbenchService",
        lambda _alignment: object(),
    )
    services = RegistryServices(config=AppConfig())

    try:
        services._service()
    except RuntimeError as exc:
        assert str(exc) == "tunnel unavailable"
    else:
        raise AssertionError("the first tunnel start should fail")
    services._service()
    assert attempts == 2
    services.close()


def test_failed_database_check_rebuilds_the_tunnel_on_retry(monkeypatch) -> None:
    events: list[str] = []
    check_attempts = 0

    class Runtime:
        def __init__(self, _config) -> None:
            events.append("created")

        def __enter__(self):
            events.append("entered")
            return object()

        def __exit__(self, *_args) -> None:
            events.append("closed")

    def check_database(_engine):
        nonlocal check_attempts
        check_attempts += 1
        if check_attempts == 1:
            raise RuntimeError("connection dropped")
        return {"usable": True}

    monkeypatch.setattr(registry_module, "DatabaseRuntime", Runtime)
    monkeypatch.setattr(registry_module, "PostgresUnitOfWorkFactory", lambda value: value)
    monkeypatch.setattr(registry_module, "AlignmentService", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        registry_module,
        "RegistryWorkbenchService",
        lambda _alignment: object(),
    )
    monkeypatch.setattr(registry_module, "check_database", check_database)
    monkeypatch.setattr(
        registry_module,
        "schema_status",
        lambda _engine: {"current_is_head": True},
    )
    services = RegistryServices(config=AppConfig())

    try:
        services.check_connection()
    except RuntimeError as exc:
        assert str(exc) == "connection dropped"
    else:
        raise AssertionError("the first database check should fail")
    assert services.check_connection()["usable"] is True
    assert events == ["created", "entered", "closed", "created", "entered"]
    services.close()
