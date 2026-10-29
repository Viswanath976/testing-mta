"""DNS validation (dnspython). Highlights missing / incorrect records.

Phase 1 = validation only. Phase 2 adds generation (A/PTR/SPF/DKIM/DMARC
with SPF-merge safety).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import dns.resolver
import dns.reversename

from .models import DnsCheck, ServerProfile

_TIMEOUT = 3.0      # seconds per lookup (bounded so a dead resolver can't stall)
_MAX_WORKERS = 24   # concurrent DNS lookups in flight


def _txt(name: str) -> str | None:
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=_TIMEOUT)
        parts = []
        for rdata in answers:
            parts.append("".join(s.decode() if isinstance(s, bytes) else str(s) for s in rdata.strings))
        return " | ".join(parts) if parts else None
    except Exception:  # noqa: BLE001
        return None


def _a(name: str) -> list[str]:
    try:
        return [r.address for r in dns.resolver.resolve(name, "A", lifetime=_TIMEOUT)]
    except Exception:  # noqa: BLE001
        return []


def validate_domain(domain: str, dkim_selector: str = "s1") -> list[DnsCheck]:
    out: list[DnsCheck] = []

    spf = _txt(domain)
    out.append(DnsCheck(record_type="SPF", name=domain, found=spf,
                        status="ok" if (spf and "v=spf1" in spf) else "missing"))

    dmarc = _txt(f"_dmarc.{domain}")
    out.append(DnsCheck(record_type="DMARC", name=f"_dmarc.{domain}", found=dmarc,
                        status="ok" if (dmarc and "v=DMARC1" in dmarc) else "missing"))

    dkim = _txt(f"{dkim_selector}._domainkey.{domain}")
    out.append(DnsCheck(record_type="DKIM", name=f"{dkim_selector}._domainkey.{domain}", found=dkim,
                        status="ok" if (dkim and "v=DKIM1" in dkim) else "missing"))

    try:
        mx = dns.resolver.resolve(domain, "MX", lifetime=_TIMEOUT)
        out.append(DnsCheck(record_type="MX", name=domain,
                            found=", ".join(str(r.exchange) for r in mx), status="ok"))
    except Exception:  # noqa: BLE001
        out.append(DnsCheck(record_type="MX", name=domain, found=None, status="missing"))

    return out


def validate_hostname_a(hostname: str, expected_ip: str) -> DnsCheck:
    found = _a(hostname)
    status = "ok" if expected_ip in found else ("mismatch" if found else "missing")
    return DnsCheck(record_type="A", name=hostname, expected=expected_ip,
                    found=", ".join(found) or None, status=status)


def validate_ptr(ip: str, expected_hostname: str | None = None) -> DnsCheck:
    try:
        rev = dns.reversename.from_address(ip)
        ptr = str(dns.resolver.resolve(rev, "PTR", lifetime=_TIMEOUT)[0]).rstrip(".")
        if expected_hostname and expected_hostname.rstrip(".") != ptr:
            status = "mismatch"
        else:
            status = "ok"
        return DnsCheck(record_type="PTR", name=ip, expected=expected_hostname, found=ptr, status=status)
    except Exception:  # noqa: BLE001
        return DnsCheck(record_type="PTR", name=ip, expected=expected_hostname, found=None, status="missing")


def validate_profile(profile: ServerProfile) -> list[DnsCheck]:
    """Validate every domain + IP concurrently (bulk, not one-by-one). pool.map
    preserves order, so output matches the old serial order: domains, then IPs."""
    sel = profile.dns.dkim_selector
    tasks = [(lambda d=d: validate_domain(d, sel)) for d in profile.unique_domains()]
    tasks += [(lambda ip=ip: [validate_hostname_a(ip.hostname, ip.ip),
                              validate_ptr(ip.ip, ip.hostname)]) for ip in profile.ips]
    if not tasks:
        return []
    checks: list[DnsCheck] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(tasks))) as pool:
        for group in pool.map(lambda fn: fn(), tasks):
            checks.extend(group)
    return checks
