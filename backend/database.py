"""SQLAlchemy engine / session management.

The engine is created lazily on first use so that tests can set DATABASE_URL
before anything connects.
"""
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Optional[Engine] = None
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,   # transparently replace dropped connections
            pool_recycle=3600,    # avoid MySQL wait_timeout disconnects
        )
        SessionLocal.configure(bind=_engine)
    return _engine


def new_session() -> Session:
    get_engine()
    return SessionLocal()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = new_session()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables that do not exist yet (idempotent)."""
    from backend import models  # noqa: F401  (registers every model on Base.metadata)

    Base.metadata.create_all(get_engine())


def check_connection() -> str:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT VERSION()")).scalar_one()
