"""Customer project persistence (YAML).

Secrets (SMTP + SSH passwords) are stripped before writing to disk and stored in
the keyring; they are re-hydrated on load. So project files never contain
plaintext passwords.
"""
from __future__ import annotations

from pathlib import Path

import yaml
# Use libyaml's C loader/dumper when available (10-50x faster on large profiles
# with thousands of IPs); fall back to the pure-Python ones otherwise.
try:
    from yaml import CSafeLoader as _YamlLoader, CSafeDumper as _YamlDumper
except ImportError:  # pragma: no cover - libyaml not built
    from yaml import SafeLoader as _YamlLoader, SafeDumper as _YamlDumper

from ..core.models import Customer, ServerProfile
from . import secrets


def _yaml_load(text: str):
    return yaml.load(text, Loader=_YamlLoader)


def _yaml_dump(data) -> str:
    return yaml.dump(data, Dumper=_YamlDumper, sort_keys=False, allow_unicode=True)


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, customer_name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in customer_name)
        return self.root / f"{safe}.yaml"

    # files that are not customer projects (app settings, etc.)
    _IGNORE = {"app-settings.yaml"}

    def list_customers(self) -> list[str]:
        names = []
        for p in sorted(self.root.glob("*.yaml")):
            if p.name in self._IGNORE:
                continue
            try:
                data = _yaml_load(p.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            # a real customer project always has a name + servers key
            if not isinstance(data, dict) or "name" not in data or "servers" not in data:
                continue
            names.append(data["name"])
        return names

    def delete(self, customer_name: str) -> bool:
        path = self._path(customer_name)
        if path.exists():
            path.unlink()
            return True
        return False

    # -- save --------------------------------------------------------------- #
    def save(self, customer: Customer) -> Path:
        data = customer.model_dump(mode="json")
        for srv in data.get("servers", []):
            self._extract_server_secrets(customer.name, srv)
        path = self._path(customer.name)
        path.write_text(_yaml_dump(data), encoding="utf-8")
        return path

    def _extract_server_secrets(self, customer: str, srv: dict) -> None:
        server = srv.get("name", "")
        # shared password
        if srv.get("shared_password"):
            secrets.set_secret(secrets.secret_key(customer, server, "shared_password"),
                               srv["shared_password"])
            srv["shared_password"] = None
        # per-ip passwords
        for ip in srv.get("ips", []):
            if ip.get("password"):
                secrets.set_secret(secrets.secret_key(customer, server, f"ip::{ip['ip']}"),
                                   ip["password"])
                ip["password"] = None
        # ssh password flag only; password itself is stored via keyring elsewhere
        srv.get("ssh", {}).pop("password", None)

    # -- load --------------------------------------------------------------- #
    def load(self, customer_name: str) -> Customer:
        path = self._path(customer_name)
        data = _yaml_load(path.read_text(encoding="utf-8")) or {}
        customer = Customer(**data)
        self._hydrate_secrets(customer)
        return customer

    def _hydrate_secrets(self, customer: Customer) -> None:
        for srv in customer.servers:
            self._hydrate_server(customer.name, srv)

    def _hydrate_server(self, customer: str, srv: ServerProfile) -> None:
        if srv.shared_password is None:
            srv.shared_password = secrets.get_secret(
                secrets.secret_key(customer, srv.name, "shared_password"))
        for ip in srv.ips:
            if ip.password is None:
                ip.password = secrets.get_secret(
                    secrets.secret_key(customer, srv.name, f"ip::{ip.ip}"))
