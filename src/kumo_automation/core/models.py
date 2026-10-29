"""Pydantic data model — the single source of truth the whole app keys off.

Designed for your real workflow:
  * single- or multi-IP servers
  * per-IP hostname + username
  * shared OR per-IP SMTP password
  * Testing / Production profiles (prod => one SMTP user per IP)
  * multiple customers, each owning several servers + deployment history
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  Enums
# --------------------------------------------------------------------------- #
class Environment(str, Enum):
    testing = "testing"
    production = "production"


class PasswordStrategy(str, Enum):
    shared = "shared"      # one common password for every SMTP user
    per_ip = "per_ip"      # each IP/user has its own password


class CredentialStrategy(str, Enum):
    shared = "shared"      # one username/password, rotated across all IPs
    per_ip = "per_ip"      # each IP has its own username/password


class ShapingPreset(str, Enum):
    conservative = "conservative"   # new-IP warmup (gentlest limits)
    balanced = "balanced"           # established IPs
    aggressive = "aggressive"       # warm IPs at volume


# --------------------------------------------------------------------------- #
#  Building blocks
# --------------------------------------------------------------------------- #
class SSHTarget(BaseModel):
    host: str
    port: int = 22
    username: str = "root"
    key_path: Optional[str] = None      # preferred
    use_password: bool = False          # if True, password is fetched from keyring
    use_sudo: bool = False              # elevate remote commands for non-root users


class IPEntry(BaseModel):
    ip: str
    hostname: str                       # unique per IP (EHLO / identity)
    username: str                       # unique per IP (SMTP login)
    password: Optional[str] = None      # None -> profile.shared_password
    domain: str                         # sending / allowed_from domain

    @property
    def pool(self) -> str:
        return f"pool-{self.ip}"

    @property
    def tenant(self) -> str:
        return f"tenant-{self.ip}"


class IPRange(BaseModel):
    """Wizard input: a CIDR (auto-expanded) OR explicit IPs, assigned to a domain,
    with configurable hostname/username naming patterns.
    Patterns support {n} (index), {domain}, and {word} (from the built-in pool)."""
    cidr: Optional[str] = None          # e.g. "203.0.113.0/24"
    ips: list[str] = Field(default_factory=list)
    domain: str = ""
    hostname_pattern: str = "s{n}.{domain}"
    username_pattern: str = "s{n}"
    start_index: int = 1


class CpanelAccount(BaseModel):
    """A cPanel account used to publish DNS. The API token is stored in the
    keyring (under host+user), never here. `domains` are the zones it owns, so a
    record for one of them routes to this account automatically."""
    host: str                            # e.g. "server.host.com:2083"
    user: str
    domains: list[str] = Field(default_factory=list)


class WhitelistEntry(BaseModel):
    """One IP (or CIDR) allowed to reach the submission ports through the
    firewall. label is just a human note ("mailer", "mgmt-divya", "aditya")."""
    label: str = ""
    ip: str                              # a single IP or a CIDR block


class ListenerConfig(BaseModel):
    """One SMTP submission listener the server should open."""
    port: int
    enabled: bool = True
    starttls: bool = True


class MonitoringConfig(BaseModel):
    enabled: bool = False
    loki_url: Optional[str] = None       # central Loki push URL (Phase 3)
    prometheus_hub: Optional[str] = None # central Prometheus host (scrapes :8000)
    display: Optional[str] = None        # friendly Grafana label, e.g. "Prod Server 01 (host | ip)"


class DnsConfig(BaseModel):
    dkim_selector: str = "s1"
    # DKIM signing. On by default (production signs). Turn OFF for bare test
    # boxes that have no DKIM keys/DNS — otherwise signing fails at send time.
    dkim_enabled: bool = True
    # SPF ip4 ranges this server sends from (for validation / later generation)
    spf_ip_ranges: list[str] = Field(default_factory=list)
    # SPF style (Pranay's requirement): True -> each domain's SPF lists only its
    # OWN individual IPs (ip4:a ip4:b …); False -> use spf_ip_ranges / CIDRs.
    spf_individual_ips: bool = True


class ShapingConfig(BaseModel):
    """Per-destination-ISP traffic shaping (connection / rate / delivery limits).

    Enabled by default so a deploy from the UI keeps ISP limits (Gmail, Yahoo,
    Outlook, Apple, …) instead of blasting every mailbox provider unthrottled.
    The generated shaping_custom.toml layers on top of the community
    shaping.toml base that ships with KumoMTA."""
    enabled: bool = True
    preset: ShapingPreset = ShapingPreset.conservative


class MonitoringHub(BaseModel):
    """The central monitoring box (e.g. la412): Prometheus file_sd + Loki.
    Configured once, reused by every customer/server."""
    name: str = "central"
    ssh: SSHTarget
    file_sd_dir: str = "/opt/monitoring/prometheus/targets"
    prometheus_url: str = "http://127.0.0.1:9090"          # as reachable ON the hub
    loki_push_url: str = "http://127.0.0.1:3100/loki/api/v1/push"
    reload_method: Literal["http", "docker", "sighup"] = "http"
    prometheus_container: str = "prometheus"               # for reload_method=docker
    # IP the server's :8000 metrics endpoint must trust (Prometheus scrapes FROM here).
    # Empty -> use the hub SSH host.
    scrape_source: str = ""

    def metrics_source(self) -> str:
        return self.scrape_source.strip() or self.ssh.host


class NamingConvention(BaseModel):
    """Config-driven so hostname/username conventions change without touching code."""
    hostname_pattern: str = "{word}.{domain}"
    username_pattern: str = "{word}"
    word_pool: list[str] = Field(default_factory=list)  # optional custom pool


# --------------------------------------------------------------------------- #
#  Server profile
# --------------------------------------------------------------------------- #
class ServerProfile(BaseModel):
    name: str
    environment: Environment = Environment.testing
    ssh: SSHTarget
    ips: list[IPEntry] = Field(default_factory=list)

    password_strategy: PasswordStrategy = PasswordStrategy.shared
    shared_password: Optional[str] = None
    credential_strategy: CredentialStrategy = CredentialStrategy.per_ip
    shared_username: Optional[str] = None

    # Provisioning knobs:
    #  * testing  -> sample this many random IPs PER RANGE (rest left unused)
    #  * production-> use ALL IPs, this many per domain (1:16 by default)
    test_ips_per_range: int = 15
    ips_per_domain: int = 16

    domains: list[str] = Field(default_factory=list)
    smtp_port: int = 587
    starttls: bool = True

    # Submission listeners to open. Empty -> the legacy default (smtp_port + 2525).
    # Real servers use [2436, 2525]. Firewall opens exactly these ports.
    submission_ports: list[int] = Field(default_factory=list)
    # Firewall allow-list: only these IPs/CIDRs may reach the submission ports;
    # everything else is dropped. Empty -> firewall management is left untouched.
    firewall_whitelist: list[WhitelistEntry] = Field(default_factory=list)

    # Strip the Received header on reception so the server's own hostname and
    # main IP never appear in delivered mail (hides the sending infrastructure).
    # On by default; turn off only if a receiver requires the reception hop.
    hide_received: bool = True

    # TCP MSS clamp (bytes) for routed-IP egress. Routed IPs tunnel through a
    # gateway with an MTU below 1500 and broken PMTUD, which black-holes large
    # inbound TLS certificate segments and stalls STARTTLS to Yahoo/others.
    # Clamping the advertised MSS fixes it. 0 disables (direct/normal-MTU boxes).
    mss_clamp: int = 1360

    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    dns: DnsConfig = Field(default_factory=DnsConfig)
    naming: NamingConvention = Field(default_factory=NamingConvention)
    shaping: ShapingConfig = Field(default_factory=ShapingConfig)

    # workflow progress (drives the guided next-step + dashboard "pending" cards)
    progress: dict[str, bool] = Field(default_factory=dict)

    def effective_password(self, ip: IPEntry) -> str:
        if self.password_strategy is PasswordStrategy.shared:
            return self.shared_password or ""
        return ip.password or ""

    def effective_username(self, ip: IPEntry) -> str:
        if self.credential_strategy is CredentialStrategy.shared:
            return self.shared_username or ""
        return ip.username

    def auth_usernames(self) -> list[str]:
        return list(dict.fromkeys(self.effective_username(ip) for ip in self.ips if self.effective_username(ip)))

    def unique_domains(self) -> list[str]:
        seen: list[str] = list(self.domains)
        for ip in self.ips:
            if ip.domain not in seen:
                seen.append(ip.domain)
        return seen

    def effective_ports(self) -> list[int]:
        """The submission ports to open + firewall. Explicit list if set, else the
        legacy default (smtp_port plus 2525)."""
        if self.submission_ports:
            return list(dict.fromkeys(self.submission_ports))
        ports = [self.smtp_port]
        if 2525 not in ports:
            ports.append(2525)
        return ports


# --------------------------------------------------------------------------- #
#  Customer / project container
# --------------------------------------------------------------------------- #
class DeploymentRecord(BaseModel):
    server: str
    environment: Environment
    timestamp: datetime
    success: bool
    backup_path: Optional[str] = None
    report_path: Optional[str] = None
    app_version: str = ""
    # audit fields
    operator_name: str = ""            # friendly name from Settings
    windows_user: str = ""             # OS login for auditing
    deploy_type: str = ""              # "Dry Run" | "Production" | "Testing"
    status_label: str = ""             # "Success" | "Failed" | "Rolled Back"
    config_hash: str = ""              # sha256 of generated files (version id)


class Customer(BaseModel):
    """One customer project: several servers + their deployment history."""
    name: str
    notes: str = ""
    servers: list[ServerProfile] = Field(default_factory=list)
    history: list[DeploymentRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    def get_server(self, name: str) -> Optional[ServerProfile]:
        return next((s for s in self.servers if s.name == name), None)


# --------------------------------------------------------------------------- #
#  Operation results
# --------------------------------------------------------------------------- #
class StepResult(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class DeployResult(BaseModel):
    server: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    steps: list[StepResult] = Field(default_factory=list)
    success: bool = False
    dry_run: bool = False
    backup_path: Optional[str] = None
    rolled_back: bool = False
    generated_files: list[str] = Field(default_factory=list)


class DnsCheck(BaseModel):
    record_type: str            # A / MX / SPF / DKIM / DMARC / PTR
    name: str
    expected: Optional[str] = None
    found: Optional[str] = None
    status: Literal["ok", "missing", "mismatch", "info"] = "info"


class HealthCheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    status: Optional[str] = None   # "pass" | "warn" | "fail"; None -> derived from ok

    @property
    def state(self) -> str:
        return self.status or ("pass" if self.ok else "fail")


class DnsRecord(BaseModel):
    """A DNS record the server needs — for hand-off to the DNS team / provider."""
    type: str                       # A / MX / TXT (SPF/DKIM/DMARC) / PTR
    name: str                       # FQDN, or IP for PTR
    value: str
    ttl: int = 3600
    action: Literal["add", "merge", "review"] = "add"
    note: str = ""
