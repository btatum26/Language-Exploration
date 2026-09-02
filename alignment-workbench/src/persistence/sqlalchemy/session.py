"""Session-factory construction without global mutable sessions."""

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build an ordinary transactional session factory."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )
