"""Live DNS verification → PASS / WARN / FAIL per record type.

A/MX/SPF/DKIM/DMARC/PTR are required (FAIL if missing/mismatch). AAAA and EHLO
are advisory (WARN). Network lookups live in the runner; the classify* helpers
are pure and unit-testable.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import dns.resolver
import dns.reversename

from .models import HealthCheck, ServerProfile

# Bulk resolve, not one-by-one: every lookup is bounded so a dead resolver can't
# stall the batch, and independent lookups run concurrently across a thread pool.
_TIMEOUT = 3.0      # seconds per lookup (query + total lifetime)
_MAX_WORKERS = 24   # concurrent DNS lookups in flight


def _hc(name: str, status: str, detail: str) -> HealthCheck:
    return HealthCheck(name=name, ok=(status == "pass"), status=status, detail=detail)


# --------------------------------------------------------------------------- #
#  Pure classifiers
# --------------------------------------------------------------------------- #
def classify_a(hostname: str, expected_ip: str, found: list[str]) -> HealthCheck:
    if expected_ip in found:
        return _hc(f"A {hostname}", "pass", expected_ip)
    if found:
        return _hc(f"A {hostname}", "fail", f"resolves to {', '.join(found)} ≠ {expected_ip}")
    return _hc(f"A {hostname}", "fail", "no A record")


def classify_ptr(ip: str, expected_host: str, ptr: str | None) -> HealthCheck:
    if not ptr:
        return _hc(f"PTR {ip}", "fail", "no reverse DNS")
    if ptr.rstrip(".") == expected_host.rstrip("."):
        return _hc(f"PTR {ip}", "pass", ptr)
    return _hc(f"PTR {ip}", "warn", f"{ptr} ≠ {expected_host}")


def classify_txt(kind: str, domain: str, value: str | None, marker: str) -> HealthCheck:
    if value and marker in value:
        return _hc(f"{kind} {domain}", "pass", value[:60])
    if value:
        return _hc(f"{kind} {domain}", "warn", f"present but no {marker}")
    return _hc(f"{kind} {domain}", "fail", "missing")


# --------------------------------------------------------------------------- #
#  Lookups
# --------------------------------------------------------------------------- #
def _a(name: str, rtype: str = "A") -> list[str]:
    try:
        return [r.address for r in dns.resolver.resolve(name, rtype, lifetime=_TIMEOUT)]
    except Exception:  # noqa: BLE001
        return []


def _mx(domain: str) -> list[str]:
    try:
        return [str(r.exchange).rstrip(".") for r in
                dns.resolver.resolve(domain, "MX", lifetime=_TIMEOUT)]
    except Exception:  # noqa: BLE001
        return []


def _txt(name: str) -> str | None:
    try:
        out = []
        for r in dns.resolver.resolve(name, "TXT", lifetime=_TIMEOUT):
            out.append("".join(s.decode() if isinstance(s, bytes) else str(s) for s in r.strings))
        return " ".join(out) or None
    except Exception:  # noqa: BLE001
        return None


def _ptr(ip: str) -> str | None:
    try:
        rev = dns.reversename.from_address(ip)
        return str(dns.resolver.resolve(rev, "PTR", lifetime=_TIMEOUT)[0]).rstrip(".")
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
#  Per-target work units (each does its own lookups; run concurrently)
# --------------------------------------------------------------------------- #
def _domain_checks(domain: str, sel: str) -> list[HealthCheck]:
    return [
        classify_txt("SPF", domain, _txt(domain), "v=spf1"),
        classify_txt("DKIM", domain, _txt(f"{sel}._domainkey.{domain}"), "v=DKIM1"),
        classify_txt("DMARC", domain, _txt(f"_dmarc.{domain}"), "v=DMARC1"),
        (lambda mx: _hc(f"MX {domain}", "pass" if mx else "fail",
                        ", ".join(mx) if mx else "no MX record"))(_mx(domain)),
    ]


def _ip_checks(ip) -> list[HealthCheck]:
    a = _a(ip.hostname)                       # resolved once, reused for A + EHLO
    aaaa = _a(ip.hostname, "AAAA")
    return [
        classify_a(ip.hostname, ip.ip, a),
        _hc(f"AAAA {ip.hostname}", "pass" if aaaa else "warn",
            ", ".join(aaaa) if aaaa else "no AAAA (optional)"),
        classify_ptr(ip.ip, ip.hostname, _ptr(ip.ip)),
        _hc(f"EHLO {ip.hostname}", "pass" if a else "warn",
            "resolves" if a else "does not resolve yet"),
    ]


def verify_profile(profile: ServerProfile) -> list[HealthCheck]:
    sel = profile.dns.dkim_selector
    # one task per domain and per IP; run them all concurrently. pool.map keeps
    # result order (domains first, then IPs) so output stays deterministic.
    tasks = [(lambda d=d: _domain_checks(d, sel)) for d in profile.unique_domains()]
    tasks += [(lambda ip=ip: _ip_checks(ip)) for ip in profile.ips]
    if not tasks:
        return []
    checks: list[HealthCheck] = []
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(tasks))) as pool:
        for group in pool.map(lambda fn: fn(), tasks):
            checks.extend(group)
    return checks
