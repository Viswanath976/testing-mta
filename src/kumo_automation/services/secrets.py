"""Secret storage — OS keyring first, in-memory fallback so the app still runs
where no keyring backend is configured. Secrets are NEVER written to project YAML.
"""
from __future__ import annotations

SERVICE = "kumomta-automation"

try:  # pragma: no cover - environment dependent
    import keyring

    _HAVE_KEYRING = True
except Exception:  # noqa: BLE001
    _HAVE_KEYRING = False

_MEM: dict[str, str] = {}


def set_secret(key: str, value: str) -> None:
    if _HAVE_KEYRING:
        try:
            keyring.set_password(SERVICE, key, value)
            return
        except Exception:  # noqa: BLE001
            pass
    _MEM[key] = value


def get_secret(key: str) -> str | None:
    if _HAVE_KEYRING:
        try:
            val = keyring.get_password(SERVICE, key)
            if val is not None:
                return val
        except Exception:  # noqa: BLE001
            pass
    return _MEM.get(key)


def secret_key(customer: str, server: str, name: str) -> str:
    return f"{customer}::{server}::{name}"
