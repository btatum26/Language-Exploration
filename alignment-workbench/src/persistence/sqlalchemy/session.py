"""Engine and session-factory construction without import-time connections."""

from sqlalchemy import Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker


def build_engine(
    database_url: str,
    *,
    pool_size: int = 2,
    max_overflow: int = 3,
) -> Engine:
    """Build a PostgreSQL engine; callers own its lifecycle."""

    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("database URL must use the postgresql+psycopg dialect")
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build an ordinary transactional session factory."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )
