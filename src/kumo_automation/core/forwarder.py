"""Install + configure the EXISTING production KumoMTA → Loki forwarder.

This module does NOT contain a forwarder implementation. It deploys the real
production forwarder from a staging folder (the source you provide), writes its
config with the correct labels, installs its systemd unit, starts it, and
verifies it — so every new VPS runs the exact same forwarder as production.

Until the source is staged, `available()` is False and installation is blocked;
all the integration points (paths, config, systemd, verify) are ready to use.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .models import MonitoringHub, ServerProfile
from .monitoring import default_display
from .ssh import SSHClient

SERVICE = "kumo-loki-forwarder"
INSTALL_DIR = f"/opt/{SERVICE}"
CONFIG_PATH = f"/etc/{SERVICE}/config.json"
UNIT_PATH = f"/etc/systemd/system/{SERVICE}.service"
POS_FILE = f"/var/lib/{SERVICE}/positions.json"

LogFn = Callable[[str], None]


# --------------------------------------------------------------------------- #
#  Config / labels (pure)
# --------------------------------------------------------------------------- #
def hub_loki_url(hub: MonitoringHub) -> str:
    """Network-reachable Loki push URL for a remote server (the hub's stored URL
    usually points at 127.0.0.1, which only works ON the hub)."""
    url = hub.loki_push_url
    for local in ("127.0.0.1", "localhost"):
        if local in url:
            return url.replace(local, hub.ssh.host)
    return url


def labels(profile: ServerProfile) -> dict[str, str]:
    host = profile.ips[0].hostname if profile.ips else profile.ssh.host
    return {
        "job": "kumomta",
        "environment": profile.environment.value,
        "server_name": profile.name,
        "hostname": host,
        "ip": profile.ssh.host,
        "display": default_display(profile),
    }


def forwarder_config(profile: ServerProfile, hub: MonitoringHub) -> dict:
    """Config written to the server for the forwarder. Field names may be aligned
    to the production forwarder's expected format once its source is provided."""
    return {
        "loki_url": hub_loki_url(hub),
        "labels": labels(profile),
        # KumoMTA local log segments are named "<timestamp>.<nanos>" with NO .log
        # extension, so match every file in the dir (recursively) — a "*.log" glob
        # would match nothing.
        "log_glob": "/var/log/kumomta/**/*",
        "pos_file": POS_FILE,
        "interval": 5,
        "batch": 200,
    }


def default_systemd_unit(entrypoint: str) -> str:
    """Fallback unit used only when the staged source has no *.service file."""
    return f"""[Unit]
Description=KumoMTA Loki Forwarder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={entrypoint}
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
"""


# --------------------------------------------------------------------------- #
#  Staging (the real forwarder source lives here until deployed)
# --------------------------------------------------------------------------- #
def available(source_dir: Path) -> bool:
    """True if a production forwarder source has been staged."""
    p = Path(source_dir)
    return p.is_dir() and any(p.iterdir())


def _entrypoint(files: list[str]) -> str:
    """Best-effort ExecStart for the default unit (only used if no *.service)."""
    for candidate in ("forwarder.py", "main.py", "app.py", "run.py"):
        if candidate in files:
            return f"/usr/bin/python3 {INSTALL_DIR}/{candidate}"
    py = next((f for f in files if f.endswith(".py")), "forwarder.py")
    return f"/usr/bin/python3 {INSTALL_DIR}/{py}"


# --------------------------------------------------------------------------- #
#  Install / verify over SSH
# --------------------------------------------------------------------------- #
def install(ssh: SSHClient, profile: ServerProfile, hub: MonitoringHub,
            source_dir: Path, log: LogFn = print) -> tuple[bool, str]:
    """Deploy the staged production forwarder to the server. Returns (ok, message)."""
    source_dir = Path(source_dir)
    if not available(source_dir):
        return False, ("Forwarder source not provided. Stage the production forwarder "
                       "files first (Monitoring → Import forwarder source).")

    log("Ensuring python3 + pip …")
    # OS-aware: apt on Debian/Ubuntu, dnf/yum on RHEL/Rocky/AlmaLinux
    ssh.run(
        "if command -v apt-get >/dev/null 2>&1; then "
        "  DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-pip; "
        "elif command -v dnf >/dev/null 2>&1; then dnf install -y python3 python3-pip; "
        "elif command -v yum >/dev/null 2>&1; then yum install -y python3 python3-pip; "
        "fi >/dev/null 2>&1 || true")
    log("Creating directories …")
    ssh.run(f"mkdir -p {INSTALL_DIR} /etc/{SERVICE} /var/lib/{SERVICE}")

    # upload every staged file (flat) to the install dir
    uploaded: list[str] = []
    unit_from_source: str | None = None
    for f in sorted(source_dir.iterdir()):
        if not f.is_file():
            continue
        content = f.read_text(encoding="utf-8", errors="replace")
        if f.suffix == ".service":
            unit_from_source = content
            continue
        ssh.put_content(f"{INSTALL_DIR}/{f.name}", content)
        uploaded.append(f.name)
    log(f"Uploaded forwarder source: {', '.join(uploaded) or '(none)'}")

    # optional dependencies
    if "requirements.txt" in uploaded:
        log("Installing Python dependencies …")
        ssh.run(f"python3 -m pip install -r {INSTALL_DIR}/requirements.txt >/dev/null 2>&1 || true")

    # config with the correct labels
    log("Writing forwarder config …")
    ssh.put_content(CONFIG_PATH, json.dumps(forwarder_config(profile, hub), indent=2))

    # systemd unit — prefer the one shipped with the source
    unit = unit_from_source or default_systemd_unit(_entrypoint(uploaded))
    ssh.put_content(UNIT_PATH, unit)

    log("Enabling + starting service …")
    ssh.run(f"systemctl daemon-reload && systemctl enable --now {SERVICE}")
    active = ssh.run(f"systemctl is-active {SERVICE}").stdout.strip()
    log(f"Service status: {active}")
    return (active == "active"), active


def verify_service(ssh: SSHClient):
    from .models import HealthCheck
    active = ssh.run(f"systemctl is-active {SERVICE}").stdout.strip()
    ok = active == "active"
    return HealthCheck(name="Forwarder service", ok=ok, status="pass" if ok else "fail",
                       detail=f"{SERVICE} {active or 'unknown'}")
