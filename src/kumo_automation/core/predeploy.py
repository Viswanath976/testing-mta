"""Pre-deployment validation → 'Ready for deployment' or 'Deployment blocked'.

Structural checks (duplicate IPs/usernames/hostnames/pools/tenants, DNS syntax)
are pure and testable. SSH connectivity + KumoMTA-installed are added by the
caller (they need a live connection).
"""
from __future__ import annotations

from . import validation
from .models import HealthCheck, ServerProfile


def _hc(name: str, status: str, detail: str) -> HealthCheck:
    return HealthCheck(name=name, ok=(status == "pass"), status=status, detail=detail)


def structural_checks(profile: ServerProfile) -> list[HealthCheck]:
    ips = [e.ip for e in profile.ips]
    users = [e.username for e in profile.ips]
    hosts = [e.hostname for e in profile.ips]
    pools = [e.pool for e in profile.ips]
    tenants = [e.tenant for e in profile.ips]

    checks: list[HealthCheck] = []
    for label, values in [("IPs", ips), ("SMTP usernames", users), ("hostnames", hosts),
                          ("pools", pools), ("tenants", tenants)]:
        dup = validation.duplicates(values)
        checks.append(_hc(f"Unique {label}", "fail" if dup else "pass",
                          ("duplicates: " + ", ".join(dup[:5])) if dup else f"{len(values)} unique"))

    # DNS syntax (domains + generated hostnames)
    bad_dom = [d for d in profile.unique_domains() if d and not validation.is_valid_domain(d)]
    bad_host = [h for h in hosts if not validation.is_valid_hostname(h)]
    dns_bad = bad_dom + bad_host
    checks.append(_hc("DNS syntax", "fail" if dns_bad else "pass",
                      ("invalid: " + ", ".join(dns_bad[:5])) if dns_bad else "all valid"))

    if not profile.ips:
        checks.append(_hc("IP addresses", "fail", "no IPs configured"))
    return checks


def verdict(checks: list[HealthCheck]) -> tuple[bool, list[str]]:
    """(ready, blocking_reasons). Ready == no FAIL checks."""
    fails = [c for c in checks if c.state == "fail"]
    return (not fails, [f"{c.name}: {c.detail}" for c in fails])
