"""Import a ServerProfile from a LIVE server.

Reconstructs exactly what is deployed — every IP, hostname, username, password,
domain, the open submission ports and the Received-strip flag — so an existing
server appears in the UI matching reality (same IP count and all). Reuses the
same reconstruction as the merge deploy.
"""
from __future__ import annotations

import re

from .merge import POLICY_DIR, fetch_live_entries
from .models import Environment, PasswordStrategy, ServerProfile, SSHTarget
from .ssh import SSHClient

_PORT_RE = re.compile(r"start_esmtp_listener\s*{\s*listen\s*=\s*'0\.0\.0\.0:(\d+)'")


def detect_ports(init_lua: str) -> list[int]:
    """Submission ports opened in init.lua (0.0.0.0 listeners, excludes the
    localhost-only injector on 2526)."""
    return sorted({int(p) for p in _PORT_RE.findall(init_lua or "")})


def import_profile(
    ssh: SSHClient,
    name: str,
    host: str,
    *,
    ssh_port: int = 22,
    username: str = "root",
) -> ServerProfile:
    """Reconstruct a full ServerProfile from the server's live config."""
    entries = fetch_live_entries(ssh)          # exact IPs + hostnames + logins + passwords
    init_lua = ssh.get_content(f"{POLICY_DIR}/init.lua") or ""
    ports = detect_ports(init_lua) or [587]
    hide_received = "remove_all_named_headers('Received')" in init_lua

    domains: list[str] = []
    for e in entries:
        if e.domain and e.domain not in domains:
            domains.append(e.domain)

    # heuristic: a real production box carries many IPs; a placement box few
    env = Environment.production if len(entries) > 100 else Environment.testing

    return ServerProfile(
        name=name,
        environment=env,
        ssh=SSHTarget(host=host, port=ssh_port, username=username),
        ips=entries,
        password_strategy=PasswordStrategy.per_ip,   # entries carry their own pw
        domains=domains,
        submission_ports=ports,
        hide_received=hide_received,
    )
