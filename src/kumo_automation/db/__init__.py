"""PostgreSQL persistence layer for the web app.

Replaces the desktop app's YAML-file + OS-keyring storage with a single database,
so everything (customers, servers, per-IP config, secrets, history) lives in one
place that `pg_dump` backs up whole. Works on Postgres in production and SQLite
locally / in tests — the URL comes from DATABASE_URL.
"""
from .base import Base, get_engine, init_db, session_scope  # noqa: F401

__all__ = ["Base", "get_engine", "init_db", "session_scope"]
