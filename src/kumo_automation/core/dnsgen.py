"""DNS record generation (Phase 2, Feature 1).

Turns the report's "missing" rows into ready-to-hand-off records:
  * A     — hostname -> IP
  * PTR   — IP -> hostname (for the IP provider / reverse DNS)
  * SPF   — TXT with the sending IPs, MERGED into any existing record (never clobbers)
  * DKIM  — TXT public key (+ the private key to install on the server)
  * DMARC — TXT policy

DKIM signing wiring into KumoMTA is a follow-up; here we produce the records +
private keys as deliverables.
"""
from __future__ import annotations

from typing import Optional

import dns.resolver

from . import dkim as dkim_mod
from .models import DnsRecord, ServerProfile


# --------------------------------------------------------------------------- #
#  DKIM
# --------------------------------------------------------------------------- #
def generate_dkim_keypair(bits: int = 2048) -> tuple[str, str]:
    """Return (private_key_pem, dkim_public_txt_value)."""
    pem = dkim_mod.generate_private_pem(bits)
    return pem, dkim_mod.public_txt_from_private(pem)


# --------------------------------------------------------------------------- #
#  SPF (with merge-safety)
# --------------------------------------------------------------------------- #
def existing_spf(domain: str) -> Optional[str]:
    try:
        for r in dns.resolver.resolve(domain, "TXT"):
            txt = "".join(s.decode() if isinstance(s, bytes) else str(s) for s in r.strings)
            if txt.lower().startswith("v=spf1"):
                return txt
    except Exception:  # noqa: BLE001
        pass
    return None


def merge_spf(existing: Optional[str], ip4_ranges: list[str]) -> str:
    """Merge ip4 ranges into an existing SPF, preserving every other mechanism
    and the trailing 'all'. Never drops what's already authorised."""
    new_mechs = [f"ip4:{c}" for c in ip4_ranges]
    if not existing:
        return "v=spf1 " + " ".join(new_mechs) + " ~all"

    tokens = existing.split()
    all_token = "~all"
    body: list[str] = []
    for tok in tokens[1:]:  # skip the "v=spf1" prefix
        if len(tok) > 3 and tok.endswith("all") and tok[0] in "~-+?":
            all_token = tok
        else:
            body.append(tok)
    for mech in new_mechs:
        if mech not in body:
            body.append(mech)
    return "v=spf1 " + " ".join(body) + " " + all_token


# --------------------------------------------------------------------------- #
#  Record set
# --------------------------------------------------------------------------- #
def _spf_ranges(profile: ServerProfile) -> list[str]:
    if profile.dns.spf_ip_ranges:
        return profile.dns.spf_ip_ranges
    seen: list[str] = []
    for ip in profile.ips:
        if ip.ip not in seen:
            seen.append(ip.ip)
    return seen


def generate_records(profile: ServerProfile,
                     keys: Optional[dict[str, str]] = None) -> tuple[list[DnsRecord], dict[str, str]]:
    """Return (dns_records, {domain: dkim_private_pem}).

    If `keys` (domain -> private PEM) is provided, the published DKIM public key
    is derived from those persisted keys so it matches what's deployed on the
    server. Otherwise fresh keys are generated (preview/CLI convenience)."""
    records: list[DnsRecord] = []
    dkim_keys: dict[str, str] = {}

    # A + PTR per IP
    for ip in profile.ips:
        records.append(DnsRecord(type="A", name=ip.hostname, value=ip.ip, note="host -> IP"))
        records.append(DnsRecord(type="PTR", name=ip.ip, value=ip.hostname, action="review",
                                 note="reverse DNS — set at the IP provider"))

    individual = getattr(profile.dns, "spf_individual_ips", True)
    all_ranges = _spf_ranges(profile)
    for domain in profile.unique_domains():
        # No MX — these domains send only, they don't receive; MX records are not
        # generated or published (per requirement).

        # Pranay's format: each domain's SPF lists only ITS OWN individual IPs
        # (not the whole /25, not every IP on the server). Falls back to the
        # configured CIDRs/ranges when individual-IP mode is off.
        if individual:
            spf_values = [ip.ip for ip in profile.ips if ip.domain == domain]
        else:
            spf_values = all_ranges
        current = existing_spf(domain)
        records.append(DnsRecord(
            type="TXT", name=domain, value=merge_spf(current, spf_values),
            action="merge" if current else "add",
            note="SPF — this domain's individual IPs, merged" if current else "SPF — this domain's individual IPs"))

        if keys and domain in keys:
            private_pem = keys[domain]
            public_txt = dkim_mod.public_txt_from_private(private_pem)
        else:
            private_pem, public_txt = generate_dkim_keypair()
        dkim_keys[domain] = private_pem
        records.append(DnsRecord(
            type="TXT", name=f"{profile.dns.dkim_selector}._domainkey.{domain}",
            value=public_txt, note="DKIM public key (private key installed on server)"))

        records.append(DnsRecord(
            type="TXT", name=f"_dmarc.{domain}",
            value=f"v=DMARC1; p=none; sp=none; rua=mailto:dmarc@{domain}; adkim=r; aspf=r",
            note="DMARC"))

    return records, dkim_keys


def records_to_csv(records: list[DnsRecord]) -> str:
    lines = ["type,name,value,ttl,action,note"]
    for r in records:
        value = r.value.replace('"', '""')
        note = r.note.replace('"', '""')
        lines.append(f'{r.type},{r.name},"{value}",{r.ttl},{r.action},"{note}"')
    return "\n".join(lines) + "\n"


def records_to_txt(records: list[DnsRecord]) -> str:
    """Aligned, human-readable zone-style text."""
    w_type = max((len(r.type) for r in records), default=4)
    w_name = max((len(r.name) for r in records), default=4)
    lines = [f"{'TYPE':<{w_type}}  {'HOST':<{w_name}}  VALUE"]
    for r in records:
        lines.append(f"{r.type:<{w_type}}  {r.name:<{w_name}}  {r.value}")
    return "\n".join(lines) + "\n"


def split_records(records: list[DnsRecord]) -> tuple[list[DnsRecord], list[DnsRecord]]:
    """Split into (dns_records, ptr_records). PTR is handed off separately (to the
    IP provider / Shashank), everything else — A/MX/SPF/DKIM/DMARC — is the DNS file."""
    dns = [r for r in records if r.type != "PTR"]
    ptr = [r for r in records if r.type == "PTR"]
    return dns, ptr


def write_dns_and_ptr(records: list[DnsRecord], out_dir) -> tuple[str, str]:
    """Write the two deliverables you asked for:
       dns-records.xlsx  — A, MX, SPF, DKIM, DMARC (=none)
       ptr-records.xlsx  — PTR only
    Returns the two paths."""
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dns, ptr = split_records(records)
    dns_path = out / "dns-records.xlsx"
    ptr_path = out / "ptr-records.xlsx"
    records_to_xlsx(dns, dns_path)
    records_to_xlsx(ptr, ptr_path)
    return str(dns_path), str(ptr_path)


def records_to_xlsx(records: list[DnsRecord], path) -> None:
    """Write records to a real .xlsx workbook (requires openpyxl)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "DNS Records"
    headers = ["Type", "Host", "Value", "TTL", "Action", "Note"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in records:
        ws.append([r.type, r.name, r.value, r.ttl, r.action, r.note])
    for col, width in zip("ABCDEF", (8, 40, 70, 8, 10, 40)):
        ws.column_dimensions[col].width = width
    wb.save(str(path))
