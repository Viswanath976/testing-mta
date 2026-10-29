"""Firewall allow-list for the submission ports + SSH brute-force protection.

Only the whitelisted IPs/CIDRs may reach the submission ports; everything else is
dropped. Implemented as a dedicated KUMO-SUBMIT chain that is flushed and
repopulated on every apply, so re-running never stacks duplicate rules
(the failure mode we hit editing INPUT by hand). Loopback is always accepted so
on-box self-tests to 127.0.0.1 work, and a fail2ban sshd jail is installed.

render_rules() is pure (returns the shell script) so it is unit-testable; apply()
runs it over SSH and persists the ruleset.
"""
from __future__ import annotations

from .models import ServerProfile, WhitelistEntry
from .ssh import SSHClient

CHAIN = "KUMO-SUBMIT"


def render_rules(ports: list[int], whitelist: list[WhitelistEntry]) -> str:
    """Return an idempotent shell script that allow-lists `ports` to `whitelist`
    only (drop everything else), accepts loopback, and persists the ruleset."""
    dports = ",".join(str(p) for p in ports)
    lines = [
        "set -e",
        f"iptables -N {CHAIN} 2>/dev/null || true",
        f"iptables -F {CHAIN}",
        # on-box self-tests (swaks to 127.0.0.1) must never be dropped
        f"iptables -A {CHAIN} -i lo -j ACCEPT",
    ]
    for w in whitelist:
        ip = (w.ip or "").strip()
        if ip:
            lines.append(
                f"iptables -A {CHAIN} -p tcp -m multiport --dports {dports} -s {ip} -j ACCEPT"
                f"   # {w.label or 'allow'}")
    # everyone else -> drop, but only for the submission ports (SSH etc. pass through)
    lines.append(f"iptables -A {CHAIN} -p tcp -m multiport --dports {dports} -j DROP")
    # jump from INPUT exactly once (position 1 so it runs before any earlier ACCEPT)
    lines.append(
        f"iptables -C INPUT -j {CHAIN} 2>/dev/null || iptables -I INPUT 1 -j {CHAIN}")
    # persist (Debian rules.v4, or netfilter-persistent, best-effort)
    lines.append(
        "iptables-save > /etc/iptables/rules.v4 2>/dev/null || "
        "netfilter-persistent save 2>/dev/null || true")
    return "\n".join(lines) + "\n"


_FAIL2BAN_JAIL = (
    "[sshd]\nenabled = true\nport = ssh\nmaxretry = 5\n"
    "findtime = 600\nbantime = 3600\n")


def render_fail2ban() -> str:
    """Install fail2ban (apt/dnf/yum) + an sshd jail, idempotently."""
    return (
        "set -e\n"
        "(command -v fail2ban-client >/dev/null 2>&1) || "
        "apt-get install -y fail2ban 2>/dev/null || dnf install -y fail2ban 2>/dev/null || "
        "yum install -y fail2ban 2>/dev/null || true\n"
        "mkdir -p /etc/fail2ban/jail.d\n"
        f"cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'\n{_FAIL2BAN_JAIL}EOF\n"
        "systemctl enable --now fail2ban 2>/dev/null || true\n"
    )


def apply(profile: ServerProfile, ssh: SSHClient) -> tuple[bool, str]:
    """Push the allow-list for this profile's ports + whitelist to the server.
    No-op (returns True) when the profile has no whitelist configured, so we never
    lock a server down unless the operator explicitly listed allowed IPs."""
    if not profile.firewall_whitelist:
        return True, "no whitelist configured — firewall left untouched"
    script = render_rules(profile.effective_ports(), profile.firewall_whitelist)
    r = ssh.run(script)
    ok = r.exit_code == 0
    detail = f"{len(profile.firewall_whitelist)} IP(s) allowed on {profile.effective_ports()}"
    return ok, detail if ok else (r.stderr or r.stdout).strip()[:200]


def apply_fail2ban(ssh: SSHClient) -> tuple[bool, str]:
    r = ssh.run(render_fail2ban())
    return (r.exit_code == 0), ("sshd jail installed" if r.exit_code == 0 else r.stderr[:200])
