"""Server prerequisite / dependency checks for a (possibly brand-new) Ubuntu VPS.

Each check returns a HealthCheck with status pass|warn|fail. The *parsers* take
raw command output and are pure (unit-testable); `run_prereqs` wires them to SSH.
"""
from __future__ import annotations

from .models import HealthCheck, ServerProfile
from .ssh import SSHClient

# KumoMTA is installed from apt; Ubuntu and Debian are both supported.
SUPPORTED_OS = {"ubuntu": (20, 4), "debian": (11, 0)}
MIN_DISK_MB = 2000
WARN_DISK_MB = 800
MIN_MEM_MB = 1024
WARN_MEM_MB = 512


def _hc(name: str, status: str, detail: str = "") -> HealthCheck:
    return HealthCheck(name=name, ok=(status == "pass"), status=status, detail=detail)


def _human(mb: int) -> str:
    """MB -> a client-friendly size (GB once it's large enough)."""
    return f"{mb / 1024:.0f} GB" if mb >= 1024 else f"{mb} MB"


# --------------------------------------------------------------------------- #
#  Pure parsers
# --------------------------------------------------------------------------- #
def parse_os(os_id: str, version_id: str) -> HealthCheck:
    """Operating System check. Ubuntu and Debian are both supported by KumoMTA."""
    os_id = (os_id or "").strip().lower()
    ver = (version_id or "").strip().strip('"')
    pretty = (f"{os_id.title()} {ver}".strip()) or "Unknown OS"
    if os_id not in SUPPORTED_OS:
        return _hc("Operating System", "warn",
                   f"{pretty} — not officially supported (Ubuntu/Debian recommended)")
    floor = SUPPORTED_OS[os_id]
    try:
        major, minor = (int(x) for x in (ver.split(".") + ["0"])[:2])
    except ValueError:
        return _hc("Operating System", "pass", f"{pretty} — Supported")
    if (major, minor) >= floor:
        return _hc("Operating System", "pass", f"{pretty} — Supported")
    return _hc("Operating System", "warn",
               f"{pretty} — below recommended {os_id.title()} {floor[0]}.{floor[1]:02d}")


# backward-compat alias (older callers/tests used parse_ubuntu)
parse_ubuntu = parse_os


def _mb(value_kb: str) -> int:
    try:
        return int(value_kb.strip() or "0") // 1024
    except ValueError:
        return 0


def parse_disk(free_kb: str) -> HealthCheck:
    mb = _mb(free_kb)
    status = "pass" if mb >= MIN_DISK_MB else ("warn" if mb >= WARN_DISK_MB else "fail")
    return _hc("Disk Space", status, f"{_human(mb)} free on /opt")


def parse_memory(memtotal_kb: str) -> HealthCheck:
    mb = _mb(memtotal_kb)
    status = "pass" if mb >= MIN_MEM_MB else ("warn" if mb >= WARN_MEM_MB else "fail")
    return _hc("Memory", status, f"{_human(mb)} RAM")


def parse_cpu(nproc: str) -> HealthCheck:
    try:
        n = int((nproc or "0").strip())
    except ValueError:
        n = 0
    return _hc("CPU", "pass" if n >= 1 else "warn", f"{n} vCPU" + ("s" if n != 1 else ""))


def parse_internet(http_code: str) -> HealthCheck:
    """curl returns '000' when it cannot connect at all. ANY real HTTP response
    (2xx/3xx/4xx/5xx) proves the server reached the internet."""
    code = (http_code or "").strip()
    reachable = bool(code) and code != "000"
    return _hc("Internet Connectivity", "pass" if reachable else "fail",
               "Connected" if reachable else "No response — check internet / DNS")


def parse_hostname(fqdn: str) -> HealthCheck:
    h = (fqdn or "").strip()
    if not h:
        return _hc("Hostname", "warn", "Not set")
    return _hc("Hostname", "pass" if "." in h else "warn", h)


def parse_firewall(ufw_status: str) -> HealthCheck:
    s = (ufw_status or "").strip().lower()
    if "inactive" in s or not s:
        return _hc("Firewall", "pass", "Not blocking")
    if "active" in s:
        return _hc("Firewall", "warn", "Active — ensure ports 25 / 587 are allowed")
    return _hc("Firewall", "pass", s[:40])


def parse_timesync(synced: str) -> HealthCheck:
    s = (synced or "").strip().lower()
    return _hc("Time Sync", "pass" if s in ("yes", "true") else "warn",
               "Synchronized" if s in ("yes", "true") else "Not synchronized")


def parse_port_free(port: int, listener_count: str) -> HealthCheck:
    try:
        n = int((listener_count or "0").strip())
    except ValueError:
        n = 0
    # A listener already bound is expected once KumoMTA is installed; report clearly.
    return _hc(f"Port {port}", "pass", "In use (a mail service is listening)" if n else "Available")


# --------------------------------------------------------------------------- #
#  SSH runner
# --------------------------------------------------------------------------- #
def run_prereqs(ssh: SSHClient, profile: ServerProfile) -> list[HealthCheck]:
    checks: list[HealthCheck] = [_hc("SSH", "pass", "Connected")]

    r = ssh.run(". /etc/os-release 2>/dev/null; printf '%s\\n%s' \"$ID\" \"$VERSION_ID\"")
    lines = r.stdout.splitlines()
    checks.append(parse_os(lines[0] if lines else "", lines[1] if len(lines) > 1 else ""))

    checks.append(parse_disk(ssh.run("df -Pk /opt 2>/dev/null | awk 'NR==2{print $4}'").stdout))
    checks.append(parse_memory(ssh.run("awk '/MemTotal/{print $2}' /proc/meminfo").stdout))
    checks.append(parse_cpu(ssh.run("nproc").stdout))
    checks.append(parse_internet(ssh.run(
        "curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://openrepo.kumomta.com").stdout))
    checks.append(parse_hostname(ssh.run("hostname -f 2>/dev/null || hostname").stdout))
    checks.append(parse_firewall(ssh.run("ufw status 2>/dev/null | head -1").stdout))
    checks.append(parse_timesync(ssh.run(
        "timedatectl show -p NTPSynchronized --value 2>/dev/null").stdout))
    for port in (25, profile.smtp_port):
        checks.append(parse_port_free(port, ssh.run(f"ss -ltn | grep -c ':{port} '").stdout))
    return checks
