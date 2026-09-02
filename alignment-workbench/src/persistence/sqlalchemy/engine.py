"""Conservative synchronous PostgreSQL engine construction."""

from sqlalchemy import Engine, create_engine, make_url


def build_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 2,
    pool_timeout_seconds: float = 10.0,
    connection_timeout_seconds: int = 10,
    application_name: str = "alignment-workbench",
) -> Engine:
    """Build a PostgreSQL engine without connecting or managing an SSH tunnel."""

    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("database URL must use the postgresql+psycopg dialect")
    if pool_size < 1 or max_overflow < 0:
        raise ValueError("pool_size must be positive and max_overflow must be nonnegative")
    if pool_timeout_seconds <= 0 or connection_timeout_seconds <= 0:
        raise ValueError("connection and pool timeouts must be positive")
    if not application_name:
        raise ValueError("application_name must not be empty")
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "application_name": application_name,
            "connect_timeout": connection_timeout_seconds,
        },
    )
