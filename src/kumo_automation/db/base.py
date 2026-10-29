"""Engine, session, and Base for the DB layer.

DATABASE_URL selects the backend:
    postgresql://user:pass@host:5432/kumo_automation   (production)
    sqlite:///./kumo.db                                 (local default / tests)
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001 - dotenv optional
    pass

DEFAULT_URL = "sqlite:///./kumo.db"


class Base(DeclarativeBase):
    pass


_engine = None
_Session: sessionmaker | None = None


def get_engine(url: str | None = None):
    """Return (and cache) the engine. Pass url to override DATABASE_URL (tests)."""
    global _engine, _Session
    if url is not None or _engine is None:
        db_url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        _engine = create_engine(db_url, future=True, connect_args=connect_args)
        _Session = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def current_url() -> str:
    """The URL the engine is actually bound to (sqlite path or postgres DSN)."""
    return get_engine().url.render_as_string(hide_password=False)


def init_db(url: str | None = None) -> None:
    """Create all tables (idempotent). Import models first so they're registered."""
    from . import models  # noqa: F401  (registers tables on Base.metadata)
    get_engine(url)
    Base.metadata.create_all(_engine)


@contextmanager
def session_scope(url: str | None = None):
    """Transactional session: commits on success, rolls back on error."""
    if _Session is None or url is not None:
        get_engine(url)
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
