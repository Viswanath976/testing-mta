"""QThread workers wrapping the (blocking) core operations so the UI stays
responsive. Workers emit log lines and a final result object.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from ..core import deploy as deploy_mod
from ..core import dns as dns_mod
from ..core import dnsverify as dnsverify_mod
from ..core import forwarder as forwarder_mod
from ..core import healthchecks as hc_mod
from ..core import installer as installer_mod
from ..core import monitoring as mon_mod
from ..core import prereqs as prereqs_mod
from ..core import smtptest as smtptest_mod
from ..core.generators import generate_all
from ..core.models import DeployResult, DnsCheck, HealthCheck, IPEntry, MonitoringHub, ServerProfile
from ..core.ssh import SSHClient

POLICY_DIR = deploy_mod.POLICY_DIR


@dataclass
class DeployOutcome:
    deploy: DeployResult | None
    dns: list[DnsCheck]
    health: list[HealthCheck]
    error: str | None = None


@dataclass
class PreviewResult:
    generated: dict[str, str]              # filename -> generated content
    remote: dict[str, str] | None = None   # filename -> current server content (None if not fetched)
    error: str | None = None


class PreviewWorker(QThread):
    """Generate config locally, then (over SSH) fetch the server's current files
    so the UI can show a side-by-side diff. SSH failure still returns generated."""
    log = Signal(str)
    done = Signal(object)  # PreviewResult

    def __init__(self, profile: ServerProfile, ssh_password: str | None = None,
                 fetch_remote: bool = True) -> None:
        super().__init__()
        self.profile = profile
        self.ssh_password = ssh_password
        self.fetch_remote = fetch_remote

    def run(self) -> None:
        generated = generate_all(self.profile)
        if not self.fetch_remote:
            self.done.emit(PreviewResult(generated=generated, remote=None))
            return
        ssh = SSHClient(
            self.profile.ssh.host, self.profile.ssh.port, self.profile.ssh.username,
            key_path=self.profile.ssh.key_path,
            password=self.ssh_password if self.profile.ssh.use_password else None,
        )
        try:
            self.log.emit(f"Connecting to {self.profile.ssh.host} …")
            ssh.connect()
            remote: dict[str, str] = {}
            for name in generated:
                remote[name] = ssh.get_content(f"{POLICY_DIR}/{name}") or ""
            self.done.emit(PreviewResult(generated=generated, remote=remote))
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"Could not fetch current config: {exc}")
            self.done.emit(PreviewResult(generated=generated, remote=None, error=str(exc)))
        finally:
            ssh.close()


@dataclass
class SetupState:
    prereqs: list
    install: object  # installer.InstallInfo
    error: str | None = None


class SetupWorker(QThread):
    """action='check' → prereqs + KumoMTA detection; action='install' → run installer."""
    log = Signal(str)
    done = Signal(object)  # SetupState (check) or bool (install ok)

    def __init__(self, profile: ServerProfile, ssh_password: str | None, action: str = "check"):
        super().__init__()
        self.profile = profile
        self.ssh_password = ssh_password
        self.action = action

    def run(self) -> None:
        ssh = SSHClient(self.profile.ssh.host, self.profile.ssh.port, self.profile.ssh.username,
                        key_path=self.profile.ssh.key_path,
                        password=self.ssh_password if self.profile.ssh.use_password else None)
        try:
            self.log.emit(f"Connecting to {self.profile.ssh.host} …")
            ssh.connect()
            if self.action == "install":
                ok, _full = installer_mod.install(ssh, self.log.emit)
                info = installer_mod.verify(ssh)
                self.log.emit("Verified: KumoMTA " + (info.version or "installed") if ok else "Install failed")
                self.done.emit(ok)
            else:
                self.log.emit("Checking prerequisites …")
                checks = prereqs_mod.run_prereqs(ssh, self.profile)
                info = installer_mod.detect(ssh)
                self.done.emit(SetupState(prereqs=checks, install=info))
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"ERROR: {exc}")
            if self.action == "install":
                self.done.emit(False)
            else:
                self.done.emit(SetupState(prereqs=[], install=installer_mod.InstallInfo(False),
                                          error=str(exc)))
        finally:
            ssh.close()


class SmtpTestWorker(QThread):
    """SMTP AUTH+STARTTLS test, optionally a full send, using one IP's user."""
    done = Signal(object)  # dict result

    def __init__(self, profile: ServerProfile, ip: IPEntry, ssh_password: str | None,
                 recipient: str | None = None, send: bool = False,
                 smtp_port: int | None = None, smtp_username: str | None = None,
                 smtp_password: str | None = None):
        super().__init__()
        self.profile = profile
        self.ip = ip
        self.ssh_password = ssh_password
        self.recipient = recipient
        self.send = send
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password

    def run(self) -> None:
        ssh = SSHClient(self.profile.ssh.host, self.profile.ssh.port, self.profile.ssh.username,
                        key_path=self.profile.ssh.key_path,
                        password=self.ssh_password if self.profile.ssh.use_password else None)
        try:
            ssh.connect()
            self.done.emit(smtptest_mod.run_smtp_test(
                self.profile, ssh, self.ip, recipient=self.recipient, send=self.send,
                smtp_port=self.smtp_port, smtp_username=self.smtp_username,
                smtp_password=self.smtp_password))
        except Exception as exc:  # noqa: BLE001
            self.done.emit({"ok": False, "error": str(exc), "auth": "fail",
                            "sending_ip": self.ip.ip, "return_path": f"bounces@{self.ip.domain}"})
        finally:
            ssh.close()


class DnsVerifyWorker(QThread):
    """Live DNS verification (A/AAAA/MX/SPF/DKIM/DMARC/PTR/EHLO)."""
    done = Signal(object)  # list[HealthCheck]

    def __init__(self, profile: ServerProfile):
        super().__init__()
        self.profile = profile

    def run(self) -> None:
        try:
            self.done.emit(dnsverify_mod.verify_profile(self.profile))
        except Exception:  # noqa: BLE001
            self.done.emit([])


class MonitorFullWorker(QThread):
    """Full monitoring lifecycle across the server AND the hub.

    action='install' → install the Loki forwarder on the server + register the
    Prometheus target on the hub.
    action='verify'  → forwarder service running (server) + Prometheus target UP,
    metrics, Loki logs, Grafana (hub). Emits PASS/FAIL for every component.
    """
    log = Signal(str)
    done = Signal(object)  # list[HealthCheck]

    def __init__(self, profile: ServerProfile, hub, ssh_password: str | None,
                 hub_password: str | None, action: str = "verify", forwarder_dir=None):
        super().__init__()
        self.profile = profile
        self.hub = hub
        self.ssh_password = ssh_password
        self.hub_password = hub_password
        self.action = action
        self.forwarder_dir = forwarder_dir

    def _server_ssh(self):
        return SSHClient(self.profile.ssh.host, self.profile.ssh.port, self.profile.ssh.username,
                         key_path=self.profile.ssh.key_path,
                         password=self.ssh_password if self.profile.ssh.use_password else None)

    def _hub_ssh(self):
        return SSHClient(self.hub.ssh.host, self.hub.ssh.port, self.hub.ssh.username,
                         key_path=self.hub.ssh.key_path,
                         password=self.hub_password if self.hub.ssh.use_password else None)

    def run(self) -> None:
        checks: list[HealthCheck] = []
        # -- server side (forwarder) -- #
        srv_ssh = self._server_ssh()
        try:
            self.log.emit(f"Connecting to server {self.profile.ssh.host} …")
            srv_ssh.connect()
            if self.action == "install":
                if not forwarder_mod.available(self.forwarder_dir):
                    checks.append(HealthCheck(
                        name="Forwarder installed", ok=False, status="fail",
                        detail="production forwarder source not provided — import it first"))
                else:
                    self.log.emit("Installing the production KumoMTA → Loki forwarder …")
                    ok, msg = forwarder_mod.install(srv_ssh, self.profile, self.hub,
                                                    self.forwarder_dir, self.log.emit)
                    checks.append(HealthCheck(name="Forwarder installed", ok=ok,
                                              status="pass" if ok else "fail",
                                              detail=("installed + started" if ok else msg)))
            checks.append(forwarder_mod.verify_service(srv_ssh))
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"Server error: {exc}")
            checks.append(HealthCheck(name="Forwarder service", ok=False, status="fail", detail=str(exc)))
        finally:
            srv_ssh.close()

        # -- hub side (prometheus / loki / grafana) -- #
        hub_ssh = self._hub_ssh()
        try:
            self.log.emit(f"Connecting to hub {self.hub.ssh.host} …")
            hub_ssh.connect()
            if self.action == "install":
                self.log.emit("Registering Prometheus target …")
                checks.append(mon_mod.register_server(self.profile, self.hub, hub_ssh))
            self.log.emit("Verifying Prometheus / Loki / Grafana …")
            checks.extend(mon_mod.verify_all(self.profile, self.hub, hub_ssh))
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"Hub error: {exc}")
            checks.append(HealthCheck(name="Hub connection", ok=False, status="fail", detail=str(exc)))
        finally:
            hub_ssh.close()

        self.done.emit(checks)


class HealthWorker(QThread):
    """Run the full health-check suite against a server (no deploy)."""
    log = Signal(str)
    done = Signal(object)  # list[HealthCheck]

    def __init__(self, profile: ServerProfile, ssh_password: str | None = None) -> None:
        super().__init__()
        self.profile = profile
        self.ssh_password = ssh_password

    def run(self) -> None:
        ssh = SSHClient(self.profile.ssh.host, self.profile.ssh.port, self.profile.ssh.username,
                        key_path=self.profile.ssh.key_path,
                        password=self.ssh_password if self.profile.ssh.use_password else None)
        try:
            self.log.emit(f"Connecting to {self.profile.ssh.host} …")
            ssh.connect()
            self.log.emit("Running health checks …")
            self.done.emit(hc_mod.run_health_checks(self.profile, ssh))
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"ERROR: {exc}")
            self.done.emit([HealthCheck(name="connection", ok=False, detail=str(exc))])
        finally:
            ssh.close()


class DeployWorker(QThread):
    log = Signal(str)
    done = Signal(object)  # DeployOutcome

    def __init__(self, profile: ServerProfile, ssh_password: str | None = None,
                 dry_run: bool = False, hub: MonitoringHub | None = None,
                 hub_password: str | None = None, dkim_keys: dict | None = None,
                 mode: str = "merge") -> None:
        super().__init__()
        self.profile = profile
        self.ssh_password = ssh_password
        self.dry_run = dry_run
        self.hub = hub
        self.hub_password = hub_password
        self.dkim_keys = dkim_keys
        # "merge" (default) adds IPs without overwriting existing; "replace"
        # overwrites the whole policy dir with only this profile.
        self.mode = mode

    def run(self) -> None:  # noqa: D401 - QThread entry point
        ssh = SSHClient(
            self.profile.ssh.host,
            self.profile.ssh.port,
            self.profile.ssh.username,
            key_path=self.profile.ssh.key_path,
            password=self.ssh_password if self.profile.ssh.use_password else None,
            use_sudo=(self.profile.ssh.use_sudo
                      or self.profile.ssh.username.strip().lower() != "root"),
        )
        try:
            self.log.emit(f"Connecting to {self.profile.ssh.host} …")
            if ssh.use_sudo:
                self.log.emit("Remote deployment commands will use sudo.")
            ssh.connect()
            self.log.emit("Connected.")

            result = deploy_mod.deploy(self.profile, ssh, dry_run=self.dry_run,
                                       mode=self.mode, log=self.log.emit,
                                       dkim_keys=self.dkim_keys)

            self.log.emit("Validating DNS …")
            dns_checks = dns_mod.validate_profile(self.profile)

            health = []
            if not self.dry_run:
                self.log.emit("Running health checks …")
                health = hc_mod.run_health_checks(self.profile, ssh)
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"ERROR: {exc}")
            self.done.emit(DeployOutcome(None, [], [], error=str(exc)))
            ssh.close()
            return
        ssh.close()

        # optional: auto-register with the monitoring hub after a real deploy
        if (not self.dry_run and result.success and self.hub
                and self.profile.monitoring.enabled):
            self._register_monitoring(health)

        self.done.emit(DeployOutcome(result, dns_checks, health))

    def _register_monitoring(self, health: list[HealthCheck]) -> None:
        hub_ssh = SSHClient(self.hub.ssh.host, self.hub.ssh.port, self.hub.ssh.username,
                            key_path=self.hub.ssh.key_path,
                            password=self.hub_password if self.hub.ssh.use_password else None)
        try:
            self.log.emit(f"Connecting to monitoring hub {self.hub.ssh.host} …")
            hub_ssh.connect()
            self.log.emit("Registering Prometheus target …")
            health.append(mon_mod.register_server(self.profile, self.hub, hub_ssh))
            self.log.emit("Verifying scrape …")
            health.append(mon_mod.verify_target(self.profile, self.hub, hub_ssh))
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"Monitoring registration failed: {exc}")
            health.append(HealthCheck(name="monitoring", ok=False, detail=str(exc)))
        finally:
            hub_ssh.close()
