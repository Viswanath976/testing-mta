"""App-level settings (not per-customer): the shared monitoring hub config.

Stored as YAML next to the customer projects. The hub SSH password is kept in
the keyring, never on disk — same policy as customer secrets.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..core.models import MonitoringHub
from . import secrets

_HUB_PW_KEY = "monitoring-hub::ssh_password"


class AppSettings:
    def __init__(self, root: Path):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "app-settings.yaml"

    def load_hub(self) -> MonitoringHub | None:
        if not self.path.exists():
            return None
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        hub_data = data.get("monitoring_hub")
        if not hub_data:
            return None
        hub = MonitoringHub(**hub_data)
        if hub.ssh.use_password:
            # password lives in the keyring; callers fetch it via hub_password()
            pass
        return hub

    def save_hub(self, hub: MonitoringHub, ssh_password: str | None = None) -> None:
        if ssh_password:
            secrets.set_secret(_HUB_PW_KEY, ssh_password)
        data = {}
        if self.path.exists():
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        data["monitoring_hub"] = hub.model_dump(mode="json")
        self.path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                             encoding="utf-8")

    def hub_password(self) -> str | None:
        return secrets.get_secret(_HUB_PW_KEY)

    # -- forwarder staging (the real production forwarder source) ------------ #
    def forwarder_dir(self) -> Path:
        d = self.path.parent / "forwarder"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- operator name (for deployment history) ----------------------------- #
    def get_operator(self) -> str:
        if not self.path.exists():
            return ""
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return data.get("operator_name", "")

    def set_operator(self, name: str) -> None:
        data = {}
        if self.path.exists():
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        data["operator_name"] = name
        self.path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                             encoding="utf-8")

    # -- cPanel accounts (host/user/domains on disk; token in keyring) ------- #
    def load_cpanel_accounts(self) -> list:
        from ..core.models import CpanelAccount
        if not self.path.exists():
            return []
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return [CpanelAccount(**a) for a in data.get("cpanel_accounts", [])]

    def save_cpanel_accounts(self, accounts) -> None:
        """Persist the account list (host/user/domains). Tokens are NOT written
        here — store those via dnspublish.store_token (keyring)."""
        data = {}
        if self.path.exists():
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        data["cpanel_accounts"] = [a.model_dump(mode="json") for a in accounts]
        self.path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                             encoding="utf-8")
