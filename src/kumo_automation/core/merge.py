"""Non-destructive MERGE — fold new IPs into a server's LIVE config without
dropping the IPs already deployed there.

The overwrite-style deploy replaces the whole policy dir. MERGE instead reads the
server's current sources.toml + smtp-auth.json, reconstructs the IPs already
present, unions the new profile's IPs on top (existing IPs are KEPT as-is — never
overwritten), and returns a combined ServerProfile the normal generator renders.
The result is one consistent, complete config that adds only what is new.
"""
from __future__ import annotations

import json
import re

from .models import IPEntry, PasswordStrategy, ServerProfile
from .ssh import SSHClient

POLICY_DIR = "/opt/kumomta/etc/policy"

# sources.toml blocks look like:
#   [source."ip-<ip>"]
#   source_address = "<ip>"
#   ehlo_domain = "<hostname>"
_SRC_RE = re.compile(
    r'source_address\s*=\s*"(?P<ip>[^"]+)"\s*\n\s*ehlo_domain\s*=\s*"(?P<host>[^"]+)"'
)


def parse_sources(text: str) -> dict[str, str]:
    """{ip: hostname} parsed from a sources.toml body."""
    return {m.group("ip"): m.group("host") for m in _SRC_RE.finditer(text or "")}


def parse_smtp_auth(text: str) -> tuple[dict, dict, dict]:
    """Return (users{login:ip}, passwords{login:pw}, domains{login:domain})."""
    try:
        a = json.loads(text or "{}")
    except ValueError:
        a = {}
    return a.get("users", {}), a.get("passwords", {}), a.get("domains", {})


def reconstruct_entries(sources_text: str, auth_text: str) -> list[IPEntry]:
    """Rebuild IPEntry objects for every IP already live on the server."""
    hosts = parse_sources(sources_text)
    users, passwords, domains = parse_smtp_auth(auth_text)
    ip_to_login = {ip: login for login, ip in users.items()}
    entries: list[IPEntry] = []
    for ip, host in hosts.items():
        login = ip_to_login.get(ip, "")
        entries.append(
            IPEntry(
                ip=ip,
                hostname=host,
                username=login or host.split(".")[0],
                password=passwords.get(login),
                domain=domains.get(login, ""),
            )
        )
    return entries


def fetch_live_entries(ssh: SSHClient) -> list[IPEntry]:
    """Read the server's live sources + auth and reconstruct its IPEntry list.
    Returns [] for a fresh server with no config yet."""
    src = ssh.get_content(f"{POLICY_DIR}/sources.toml") or ""
    auth = ssh.get_content(f"{POLICY_DIR}/smtp-auth.json") or ""
    return reconstruct_entries(src, auth)


def merge_entries(
    existing: list[IPEntry], new: list[IPEntry]
) -> tuple[list[IPEntry], int, int]:
    """Union by IP. Existing IPs are KEPT (never overwritten); only genuinely new
    IPs are added. Returns (combined, added, kept)."""
    by_ip: dict[str, IPEntry] = {e.ip: e for e in existing}
    added = 0
    for e in new:
        if e.ip not in by_ip:
            by_ip[e.ip] = e
            added += 1
    return list(by_ip.values()), added, len(existing)


def merged_profile(profile: ServerProfile, ssh: SSHClient) -> tuple[ServerProfile, int, int]:
    """Return a copy of `profile` whose .ips is the union of the server's live IPs
    and the profile's IPs, plus (added, kept) counts.

    Each entry carries its own password and the combined profile uses per_ip
    strategy, so merging never blanks or changes an existing login's credential.
    """
    existing = fetch_live_entries(ssh)
    # Resolve each new entry's password NOW (shared or per-IP) and pin it on the
    # entry, so switching the combined profile to per_ip below preserves it.
    resolved_new = [
        e.model_copy(update={"password": profile.effective_password(e)})
        for e in profile.ips
    ]
    combined, added, kept = merge_entries(existing, resolved_new)
    merged = profile.model_copy(
        update={"ips": combined, "password_strategy": PasswordStrategy.per_ip}
    )
    return merged, added, kept
