"""KumoMTA install detection + automatic installation on Ubuntu.

The install script is shown to the operator for confirmation BEFORE it runs, and
is based on KumoMTA's documented apt (openrepo) method. Constants at the top are
the bits to verify/adjust against the current KumoMTA install docs.

`detect` and `parse_version` are pure/testable; `install` streams logs over SSH.
"""
from __future__ import annotations

from typing import Callable

from .ssh import SSHClient

KUMOD = "/opt/kumomta/sbin/kumod"
# Latest version this tool has been validated against (for the upgrade hint).
LATEST_SUPPORTED = "2026.08"

# --- KumoMTA repos (verify against https://docs.kumomta.com install guide) --
_REPO_GPG = "https://openrepo.kumomta.com/files/kumomta.gpg"
_REPO_APT = "https://openrepo.kumomta.com/apt/ubuntu"
_REPO_RPM = "https://openrepo.kumomta.com/files/kumomta-rocky.repo"   # RHEL/Rocky/Alma

LogFn = Callable[[str], None]


class InstallInfo:
    def __init__(self, installed: bool, version: str = "", latest: str = LATEST_SUPPORTED):
        self.installed = installed
        self.version = version
        self.latest = latest

    @property
    def upgrade_available(self) -> bool:
        return bool(self.installed and self.version and self.version < self.latest)


def parse_version(text: str) -> str:
    """Extract a version from `kumod --version` output, e.g. 'kumod 2026.05.01'."""
    for token in (text or "").replace("kumod", " ").split():
        t = token.strip().lstrip("v")
        if t and t[0].isdigit():
            return t
    return ""


def detect(ssh: SSHClient) -> InstallInfo:
    if not ssh.exists(KUMOD):
        return InstallInfo(installed=False)
    version = parse_version(ssh.run(f"{KUMOD} --version 2>/dev/null").stdout)
    return InstallInfo(installed=True, version=version)


def install_script() -> str:
    """Bash to install KumoMTA + core dependencies. Detects the package manager
    so it works on both Debian/Ubuntu (apt) and RHEL/Rocky/AlmaLinux (dnf/yum).
    Idempotent-ish. Uses sudo only when not already root."""
    return f"""#!/usr/bin/env bash
set -euo pipefail
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

if command -v apt-get >/dev/null 2>&1; then
  echo '== Debian/Ubuntu (apt) =='
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -y
  echo '== base dependencies =='
  $SUDO apt-get install -y curl gnupg ca-certificates lsb-release python3
  echo '== KumoMTA apt repository =='
  curl -fsSL '{_REPO_GPG}' | $SUDO gpg --dearmor -o /usr/share/keyrings/kumomta-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/kumomta-archive-keyring.gpg] {_REPO_APT} $(lsb_release -cs) main" \
    | $SUDO tee /etc/apt/sources.list.d/kumomta.list
  $SUDO apt-get update -y
  echo '== installing kumomta =='
  $SUDO apt-get install -y kumomta

elif command -v dnf >/dev/null 2>&1; then
  echo '== RHEL/Rocky/AlmaLinux (dnf) =='
  $SUDO dnf -y install dnf-plugins-core curl python3
  echo '== KumoMTA dnf repository =='
  $SUDO dnf config-manager --add-repo '{_REPO_RPM}'
  echo '== installing kumomta =='
  $SUDO dnf install -y kumomta

elif command -v yum >/dev/null 2>&1; then
  echo '== RHEL/CentOS (yum) =='
  $SUDO yum -y install yum-utils curl python3
  echo '== KumoMTA yum repository =='
  $SUDO yum-config-manager --add-repo '{_REPO_RPM}'
  echo '== installing kumomta =='
  $SUDO yum install -y kumomta

else
  echo 'ERROR: no supported package manager (apt-get/dnf/yum) found' >&2
  exit 1
fi

echo '== enabling service at boot =='
$SUDO systemctl enable kumomta || true
echo '== INSTALL DONE =='
{KUMOD} --version || true
"""


def install(ssh: SSHClient, log: LogFn = print) -> tuple[bool, str]:
    """Run the install script, streaming output. Returns (ok, full_log)."""
    script = install_script()
    ssh.put_content("/tmp/kumo-install.sh", script)
    result = ssh.run("bash /tmp/kumo-install.sh 2>&1", timeout=1800)
    full = result.stdout + result.stderr
    for line in full.splitlines():
        log(line)
    ok = ssh.exists(KUMOD) and "INSTALL DONE" in full
    return ok, full


def verify(ssh: SSHClient) -> InstallInfo:
    return detect(ssh)
