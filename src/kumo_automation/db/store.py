"""Bridge the ORM tables to the existing Pydantic domain models, and provide CRUD.

Same surface as the desktop ProjectStore (list / load / save / delete customers),
so the core logic and the API reuse it unchanged — only storage moved to the DB.
Passwords/tokens are encrypted on the way in, decrypted on the way out.
"""
from __future__ import annotations

from ..core.models import (
    Customer,
    DnsConfig,
    IPEntry,
    MonitoringConfig,
    NamingConvention,
    ServerProfile,
    ShapingConfig,
    SSHTarget,
)
from ..core.models import CpanelAccount as PydCpanel
from ..core.models import WhitelistEntry as PydWhitelist
from . import models as m
from .base import init_db, session_scope
from .crypto import decrypt, encrypt


# --------------------------------------------------------------------------- #
#  ORM  <->  Pydantic
# --------------------------------------------------------------------------- #
def _profile_to_orm(p: ServerProfile) -> m.Server:
    s = m.Server(
        name=p.name,
        environment=p.environment.value,
        ssh_host=p.ssh.host, ssh_port=p.ssh.port, ssh_username=p.ssh.username,
        ssh_key_path=p.ssh.key_path, ssh_use_password=p.ssh.use_password,
        password_strategy=p.password_strategy.value,
        shared_password_enc=encrypt(p.shared_password),
        smtp_port=p.smtp_port, starttls=p.starttls,
        submission_ports=list(p.submission_ports),
        hide_received=p.hide_received, mss_clamp=p.mss_clamp,
        test_ips_per_range=p.test_ips_per_range, ips_per_domain=p.ips_per_domain,
        domains=list(p.domains),
        monitoring=p.monitoring.model_dump(mode="json"),
        dns=p.dns.model_dump(mode="json"),
        naming=p.naming.model_dump(mode="json"),
        shaping=p.shaping.model_dump(mode="json"),
        progress=dict(p.progress),
    )
    s.ips = [
        m.IP(ip=e.ip, hostname=e.hostname, username=e.username, domain=e.domain,
             password_enc=encrypt(e.password))
        for e in p.ips
    ]
    s.whitelist = [m.WhitelistEntry(label=w.label, ip=w.ip) for w in p.firewall_whitelist]
    return s


def _orm_to_profile(s: m.Server) -> ServerProfile:
    return ServerProfile(
        name=s.name,
        environment=s.environment,
        ssh=SSHTarget(host=s.ssh_host, port=s.ssh_port, username=s.ssh_username,
                      key_path=s.ssh_key_path, use_password=s.ssh_use_password),
        password_strategy=s.password_strategy,
        shared_password=decrypt(s.shared_password_enc),
        smtp_port=s.smtp_port, starttls=s.starttls,
        submission_ports=list(s.submission_ports or []),
        hide_received=s.hide_received, mss_clamp=s.mss_clamp,
        test_ips_per_range=s.test_ips_per_range, ips_per_domain=s.ips_per_domain,
        domains=list(s.domains or []),
        ips=[IPEntry(ip=e.ip, hostname=e.hostname, username=e.username, domain=e.domain,
                     password=decrypt(e.password_enc)) for e in s.ips],
        firewall_whitelist=[PydWhitelist(label=w.label, ip=w.ip) for w in s.whitelist],
        monitoring=MonitoringConfig(**(s.monitoring or {})),
        dns=DnsConfig(**(s.dns or {})),
        naming=NamingConvention(**(s.naming or {})),
        shaping=ShapingConfig(**(s.shaping or {})),
        progress=dict(s.progress or {}),
    )


# --------------------------------------------------------------------------- #
#  Customer CRUD
# --------------------------------------------------------------------------- #
def setup(url: str | None = None) -> None:
    init_db(url)


def list_customer_names() -> list[str]:
    with session_scope() as db:
        return [c.name for c in db.query(m.Customer).order_by(m.Customer.name).all()]


def load_customer(name: str) -> Customer | None:
    with session_scope() as db:
        row = db.query(m.Customer).filter_by(name=name).one_or_none()
        if not row:
            return None
        return Customer(
            name=row.name, notes=row.notes or "",
            servers=[_orm_to_profile(s) for s in row.servers],
        )


def save_customer(customer: Customer) -> None:
    with session_scope() as db:
        row = db.query(m.Customer).filter_by(name=customer.name).one_or_none()
        if row is None:
            row = m.Customer(name=customer.name)
            db.add(row)
        row.notes = customer.notes or ""
        # replace the server set wholesale (cascade drops old ips/whitelist)
        for s in list(row.servers):
            db.delete(s)
        db.flush()
        for p in customer.servers:
            orm = _profile_to_orm(p)
            orm.customer = row
            db.add(orm)


def delete_customer(name: str) -> bool:
    with session_scope() as db:
        row = db.query(m.Customer).filter_by(name=name).one_or_none()
        if not row:
            return False
        db.delete(row)
        return True


# --------------------------------------------------------------------------- #
#  cPanel accounts
# --------------------------------------------------------------------------- #
def list_cpanel_accounts() -> list[PydCpanel]:
    with session_scope() as db:
        return [PydCpanel(host=a.host, user=a.username, domains=list(a.domains or []))
                for a in db.query(m.CpanelAccount).all()]


def save_cpanel_account(host: str, user: str, domains: list[str], token: str | None) -> None:
    with session_scope() as db:
        row = db.query(m.CpanelAccount).filter_by(host=host, username=user).one_or_none()
        if row is None:
            row = m.CpanelAccount(host=host, username=user)
            db.add(row)
        row.domains = list(domains)
        if token:
            row.token_enc = encrypt(token)


def cpanel_token(host: str, user: str) -> str | None:
    with session_scope() as db:
        row = db.query(m.CpanelAccount).filter_by(host=host, username=user).one_or_none()
        return decrypt(row.token_enc) if row else None
