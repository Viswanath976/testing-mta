"""Collect basic facts about a remote server over SSH."""
from __future__ import annotations

from dataclasses import dataclass, field

from .ssh import SSHClient


@dataclass
class ServerInventory:
    hostname: str = ""
    os: str = ""
    kernel: str = ""
    interfaces: list[str] = field(default_factory=list)
    ipv4: list[str] = field(default_factory=list)
    kumomta_installed: bool = False
    kumomta_active: bool = False


def collect(ssh: SSHClient) -> ServerInventory:
    inv = ServerInventory()
    inv.hostname = ssh.run("hostname").stdout.strip()
    inv.os = ssh.run(". /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\"").stdout.strip()
    inv.kernel = ssh.run("uname -r").stdout.strip()
    inv.interfaces = [
        line.strip() for line in ssh.run("ls /sys/class/net").stdout.split() if line.strip()
    ]
    inv.ipv4 = [
        ip for ip in ssh.run(
            "ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1"
        ).stdout.split()
    ]
    inv.kumomta_installed = ssh.exists("/opt/kumomta/sbin/kumod")
    inv.kumomta_active = ssh.run("systemctl is-active kumomta").stdout.strip() == "active"
    return inv
