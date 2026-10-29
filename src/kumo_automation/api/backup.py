"""Backup & restore the whole database.

Postgres -> pg_dump (custom format) / pg_restore. SQLite -> file copy. One file
captures everything (customers, servers, IPs, encrypted secrets, cPanel accounts,
history), because all data lives in the DB.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _url() -> str:
    # env in production (postgres); else the engine's live URL (sqlite path in tests)
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    from ..db.base import current_url
    return current_url()


def backup_db(out_dir: str, stamp: str) -> str:
    """Write a backup into out_dir; return its path. `stamp` is a caller-supplied
    timestamp string (kept out of here so the function stays deterministic)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    url = _url()
    if url.startswith("sqlite"):
        src = url.split("///", 1)[1]
        dest = out / f"kumo_backup_{stamp}.sqlite"
        shutil.copyfile(src, dest)
        return str(dest)
    dest = out / f"kumo_backup_{stamp}.dump"
    subprocess.run(["pg_dump", url, "-Fc", "-f", str(dest)], check=True)
    return str(dest)


def restore_db(backup_path: str) -> None:
    """Restore from a backup produced by backup_db()."""
    url = _url()
    if url.startswith("sqlite"):
        dest = url.split("///", 1)[1]
        shutil.copyfile(backup_path, dest)
        return
    subprocess.run(["pg_restore", "-d", url, "--clean", "--if-exists", backup_path], check=True)
