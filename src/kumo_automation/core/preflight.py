"""Pre-flight blockers — catch the problems that repeatedly bite real deploys
BEFORE we touch config: kumod installed, routed IPs locally bindable,
outbound port 25 reachable, policy dir present, disk space.
"""
from __future__ import annotations

from .models import HealthCheck, ServerProfile
from .ssh import SSHClient

POLICY_DIR = "/opt/kumomta/etc/policy"


def run_preflight(profile: ServerProfile, ssh: SSHClient) -> list[HealthCheck]:
    checks: list[HealthCheck] = []

    installed = ssh.exists("/opt/kumomta/sbin/kumod")
    checks.append(HealthCheck(name="kumomta-installed", ok=installed,
                              detail="/opt/kumomta/sbin/kumod" if installed else "not found"))

    checks.append(HealthCheck(name="policy-dir", ok=ssh.exists(POLICY_DIR), detail=POLICY_DIR))

    # each configured IP must be locally bindable (routed blocks are NOT by default)
    for ip in profile.ips:
        r = ssh.run(f"ip route get {ip.ip} 2>/dev/null | head -1")
        local = "dev lo" in r.stdout or f"src {ip.ip}" in r.stdout
        checks.append(HealthCheck(
            name=f"bindable:{ip.ip}", ok=local,
            detail="local" if local else "via gateway — may need 'ip route add local'"))

    # outbound port 25 (spot check from the first IP)
    if profile.ips:
        test_ip = profile.ips[0].ip
        r = ssh.run(
            "python3 - <<'PY'\n"
            "import socket\n"
            "s=socket.socket(); s.settimeout(6)\n"
            f"try:\n    s.bind(('{test_ip}',0)); s.connect(('gmail-smtp-in.l.google.com',25)); print('ok')\n"
            "except Exception as e:\n    print('fail', e)\n"
            "finally:\n    s.close()\nPY"
        )
        ok = r.stdout.strip().startswith("ok")
        checks.append(HealthCheck(name="outbound-port-25", ok=ok, detail=r.stdout.strip()[:80]))

    df = ssh.run("df -Pk /opt | awk 'NR==2{print $4}'")
    try:
        free_mb = int(df.stdout.strip()) // 1024
        checks.append(HealthCheck(name="disk-free", ok=free_mb > 500, detail=f"{free_mb} MB free on /opt"))
    except ValueError:
        checks.append(HealthCheck(name="disk-free", ok=True, detail="unknown"))

    return checks
