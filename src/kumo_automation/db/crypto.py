"""Encrypt secrets (SMTP/SSH passwords, cPanel tokens) at rest.

Passwords are encrypted with a key derived from SECRET_KEY, so the DB — and every
pg_dump backup — never contains plaintext. Set a strong SECRET_KEY in .env;
changing it makes previously stored secrets unreadable (rotate deliberately).
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str | None) -> str | None:
    if not value:
        return value
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str | None) -> str | None:
    if not token:
        return token
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
