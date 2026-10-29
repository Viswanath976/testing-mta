"""Input validation — one place, reused by the wizard, edit screen, and CLI.

Pure functions (no Qt, no I/O) so they are trivially unit-testable. These are
the guards that stop invalid input from ever producing a bad KumoMTA config.
"""
from __future__ import annotations

import ipaddress
import json
import re

ALLOWED_SMTP_PORTS = {25, 465, 587, 2525, 2436}

# a DNS name: 2+ dot-separated labels, alpha TLD (>=2). Rejects "gmail", "a b.com".
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
# DKIM selector: alphanumeric, may contain . _ - internally.
_DKIM_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._-]{0,61}[a-zA-Z0-9])?$")


# --------------------------------------------------------------------------- #
#  Host / network
# --------------------------------------------------------------------------- #
def sanitize_host(text: str) -> str:
    """'ssh root@198.23.177.154' or 'root@host' -> 'host'. Fixes whole SSH
    commands landing in the host field (the getaddrinfo failure)."""
    t = (text or "").strip()
    if t.lower().startswith("ssh "):
        t = t[4:].strip()
    if "@" in t:
        t = t.split("@", 1)[1].strip()
    return t.split()[0] if t else ""


def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def is_valid_cidr(value: str) -> bool:
    try:
        ipaddress.ip_network(value.strip(), strict=False)
        return True
    except ValueError:
        return False


def is_valid_host(value: str) -> bool:
    """A usable SSH host: a valid IP or a valid domain/hostname (no spaces/@)."""
    v = value.strip()
    return bool(v) and " " not in v and "@" not in v and (is_valid_ip(v) or is_valid_domain(v))


# --------------------------------------------------------------------------- #
#  Names
# --------------------------------------------------------------------------- #
def is_valid_domain(value: str) -> bool:
    return bool(_DOMAIN_RE.match((value or "").strip()))


def is_valid_hostname(value: str) -> bool:
    """A generated EHLO hostname must be a FQDN (label.label…tld), no trailing dot."""
    return is_valid_domain(value)


def is_valid_dkim_selector(value: str) -> bool:
    return bool(_DKIM_RE.match((value or "").strip()))


# --------------------------------------------------------------------------- #
#  Ports / passwords
# --------------------------------------------------------------------------- #
def is_valid_ssh_port(port: int) -> bool:
    return 1 <= int(port) <= 65535


def smtp_port_status(port: int) -> str:
    """'ok' for a standard submission/smtp port, else 'warn' (unusual but allowed)."""
    return "ok" if int(port) in ALLOWED_SMTP_PORTS else "warn"


def password_warning(pw: str | None) -> str | None:
    """Return a human warning for a weak password, or None if acceptable."""
    if not pw:
        return None
    if len(pw) < 8:
        return "Password is short (< 8 chars)."
    if pw.isdigit() or pw.isalpha():
        return "Password is weak (letters or digits only)."
    return None


# --------------------------------------------------------------------------- #
#  Duplicate detection (returns the offending values, empty if clean)
# --------------------------------------------------------------------------- #
def duplicates(items: list[str]) -> list[str]:
    seen: set[str] = set()
    dup: list[str] = []
    for it in items:
        if it in seen and it not in dup:
            dup.append(it)
        seen.add(it)
    return dup


# --------------------------------------------------------------------------- #
#  Policy-file integrity  (sources = queues = auth = listener must all match)
# --------------------------------------------------------------------------- #
# Every IP must be wired through ALL of: sources.toml (egress source),
# queues.toml (tenant -> pool routing), smtp-auth.json (login -> ip) and
# listener_domains.toml (relay authz). If any count drifts, some IPs egress from
# the WRONG IP even though sends still return 250 OK. This is the check that
# caught the 2,027-IP gap on a hand-edited server. The functions take raw file
# CONTENT so they work identically on generated files and files fetched live off
# a server via SSHClient.get_content().
def policy_counts(files: dict[str, str]) -> dict[str, int]:
    """Count the per-IP entries in each policy file's content.

    Keys: sources, queues, auth, listener, dkim_domains. Missing files count 0.
    """
    src = files.get("sources.toml", "") or ""
    q = files.get("queues.toml", "") or ""
    ld = files.get("listener_domains.toml", "") or ""
    dk = files.get("dkim_data.toml", "") or ""
    try:
        auth = json.loads(files.get("smtp-auth.json", "") or "{}")
        auth_n = len(auth.get("users", {}))
    except (ValueError, AttributeError):
        auth_n = 0
    return {
        "sources": len(re.findall(r"(?m)^\s*source_address\s*=", src)),
        "queues": len(re.findall(r"(?m)^\s*\[tenant\.", q)),
        "auth": auth_n,
        # count real username entries; an empty-string "" entry never counts
        "listener": len(re.findall(r'(?m)^\s*"[^"]+"\s*,', ld)),
        "dkim_domains": len(re.findall(r"(?m)^\s*\[domain\.", dk)),
    }


def integrity_errors(files: dict[str, str]) -> list[str]:
    """Return human-readable problems, empty list if the config is consistent.

    Fails on: the per-IP counts not matching across sources/queues/auth/listener,
    or an empty-string back-door present in listener_domains.
    """
    problems: list[str] = []
    c = policy_counts(files)
    per_ip = {k: c[k] for k in ("sources", "queues", "auth", "listener")}
    if len(set(per_ip.values())) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in per_ip.items())
        problems.append(f"Per-IP counts do not match ({detail}) — some IPs are not fully wired.")
    ld = files.get("listener_domains.toml", "") or ""
    if re.search(r'(?m)^\s*""\s*,', ld):
        problems.append('listener_domains.toml contains an empty-string ("") relay authz — open-relay back-door.')
    return problems


def is_consistent(files: dict[str, str]) -> bool:
    return not integrity_errors(files)


# --------------------------------------------------------------------------- #
#  Cross-server uniqueness  (each server needs its own usernames + domains)
# --------------------------------------------------------------------------- #
def cross_server_collisions(servers) -> list[str]:
    """Warn when a SMTP username or sending domain is used on more than one
    server. `servers` is any iterable of objects with .name and .ips (each ip has
    .username and .domain). This keeps every server's identity distinct — no
    reusing e.g. 'tidyjetty'/'dateglowhub.com' across boxes."""
    user_owner: dict[str, str] = {}
    dom_owner: dict[str, str] = {}
    warns: list[str] = []
    for s in servers:
        for ip in getattr(s, "ips", []):
            owner = user_owner.get(ip.username)
            if owner and owner != s.name:
                warns.append(f"Username '{ip.username}' is used on both '{owner}' and '{s.name}'.")
            user_owner.setdefault(ip.username, s.name)
        for d in {ip.domain for ip in getattr(s, "ips", [])}:
            owner = dom_owner.get(d)
            if owner and owner != s.name:
                warns.append(f"Domain '{d}' is used on both '{owner}' and '{s.name}'.")
            dom_owner.setdefault(d, s.name)
    return list(dict.fromkeys(warns))
