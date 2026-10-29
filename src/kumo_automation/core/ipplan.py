"""Server-creation planning (Phase 2 redesign).

Turns operator input — CIDR ranges or individual IPs, each assigned to a domain
with a naming pattern — into a concrete list of IPEntry (ip + hostname + username).

Modular by design:
  * expansion       : CIDR -> usable IPs (ipaddress)
  * naming          : pattern-based ({n}/{domain}/{word}) + preset table
  * import          : CSV (headered or plain) and CIDR
  * validation      : returns human prompts instead of raising
  * preview         : totals, per-domain counts, samples, duplicate detection
"""
from __future__ import annotations

import csv
import io
import ipaddress
import random

from . import validation
from .models import IPEntry, IPRange

# --------------------------------------------------------------------------- #
#  Naming presets (hostname_pattern, username_pattern) — add more freely
# --------------------------------------------------------------------------- #
NAMING_PRESETS: dict[str, tuple[str, str]] = {
    "s{n}": ("s{n}.{domain}", "s{n}"),
    "smtp{n}": ("smtp{n}.{domain}", "smtp{n}"),
    "mail{n}": ("mail{n}.{domain}", "mail{n}"),
    "mx{n}": ("mx{n}.{domain}", "mx{n}"),
    "word": ("{word}.{domain}", "{word}"),
}

# 80 x 80 = 6,400 clean adjective+noun combos, enough for thousands of IPs
# (5,080+) before any numeric-suffix fallback is ever needed.
_ADJ = ("vivid polar teal amber coral slate misty brisk lunar solar crisp noble brave swift "
        "quiet bright cobalt ivory olive russet azure cedar frost ember dusky fleet gilded "
        "hazel jade keen mellow opal pearl quartz rustic sable tawny umber velvet wistful bold "
        "calm dewy eager fond glad husky icy jolly lush merry neat posh rapid sleek tidy urban "
        "warm zesty agile balmy clear deft fair grand hardy ideal just lucid mild proud regal "
        "stark true vast witty young zonal fresh prime").split()
_NOUN = ("canyon brook ridge meadow harbor summit willow cove grove delta vale cliff fjord "
         "glade haven isle knoll lagoon marsh oasis pine quarry reef shore thicket tundra vista "
         "wharf yard zenith basin creek dune estuary fen gorge hollow inlet jetty key ledge mesa "
         "notch orchard prairie ravine spring terrace upland wetland arbor bluff channel dell elm "
         "falls garden heath islet lake moor narrows peak river strand trail valley wood acre bay "
         "crest field glen hill lane pond slope vine bend cape").split()


def server_seed(name: str, base: int = 42) -> int:
    """Deterministic per-server seed. Two servers with different names draw from
    different parts of the word stream, so usernames/hostnames never collide
    across servers (e.g. 'tidyjetty' won't appear on two boxes)."""
    import zlib
    return base + (zlib.crc32(name.encode("utf-8")) % 1_000_000)


def word_stream(seed: int = 42):
    """Deterministic stream of unique, DNS-safe words (adjective+noun combos,
    then numeric suffixes if exhausted)."""
    combos = [a + n for a in _ADJ for n in _NOUN]
    random.Random(seed).shuffle(combos)
    for c in combos:
        yield c
    suffix = 0
    while True:
        suffix += 1
        for c in combos:
            yield f"{c}{suffix}"


def fill_pattern(pattern: str, n: int, domain: str, word: str = "") -> str:
    return (pattern.replace("{n}", str(n))
                   .replace("{domain}", domain)
                   .replace("{word}", word))


# --------------------------------------------------------------------------- #
#  Expansion
# --------------------------------------------------------------------------- #
def expand_range(rng: IPRange) -> list[str]:
    if rng.cidr:
        net = ipaddress.ip_network(rng.cidr, strict=False)
        return [str(a) for a in net.hosts()]  # skips network+broadcast; handles /31,/32
    return list(rng.ips)


# --------------------------------------------------------------------------- #
#  Build
# --------------------------------------------------------------------------- #
def build_ip_entries(ranges: list[IPRange], seed: int = 42) -> list[IPEntry]:
    """Expand every range into IPEntries.

    The index is a single running counter across the WHOLE server (not per
    domain), so both hostnames and — crucially — SMTP usernames are globally
    unique. A per-domain counter would produce colliding logins like `s1`
    appearing under two different domains.
    """
    entries: list[IPEntry] = []
    words = word_stream(seed)
    n = ranges[0].start_index if ranges else 1
    for rng in ranges:
        uses_word = "{word}" in (rng.hostname_pattern + rng.username_pattern)
        for ip in expand_range(rng):
            w = next(words) if uses_word else ""
            entries.append(IPEntry(
                ip=ip,
                hostname=fill_pattern(rng.hostname_pattern, n, rng.domain, w),
                username=fill_pattern(rng.username_pattern, n, rng.domain, w),
                domain=rng.domain,
            ))
            n += 1
    return entries


# --------------------------------------------------------------------------- #
#  Environment-aware building
#    testing    -> sample N random IPs per range (placement test)
#    production -> ALL IPs, `ips_per_domain` per domain (1:16 by default)
# --------------------------------------------------------------------------- #
def build_testing(
    ranges: list[IPRange],
    ips_per_range: int = 15,
    *,
    domains: list[str] | None = None,
    seed: int = 42,
) -> list[IPEntry]:
    """Pick `ips_per_range` random IPs from EACH range (or all, if the range has
    fewer). Each range's IPs use the range's own domain; falls back to the
    supplied domains list positionally when a range has no domain."""
    rnd = random.Random(seed)
    words = word_stream(seed)
    entries: list[IPEntry] = []
    for i, rng in enumerate(ranges):
        ips = expand_range(rng)
        pick = ips if len(ips) <= ips_per_range else rnd.sample(ips, ips_per_range)
        pick.sort(key=lambda s: tuple(int(o) for o in s.split(".")))  # stable, readable
        domain = rng.domain or ((domains or [""])[min(i, len(domains or [""]) - 1)])
        for ip in pick:
            w = next(words)
            entries.append(IPEntry(ip=ip, hostname=f"{w}.{domain}", username=w, domain=domain))
    return entries


def build_production(
    ranges: list[IPRange],
    domains: list[str],
    ips_per_domain: int = 16,
    *,
    seed: int = 42,
) -> list[IPEntry]:
    """ALL IPs of every range, assigned `ips_per_domain` per domain in order
    (1:16 by default). The last domain absorbs any remainder if IPs outnumber
    domains * ips_per_domain."""
    if not domains:
        raise ValueError("production build needs at least one domain")
    all_ips = [ip for r in ranges for ip in expand_range(r)]
    words = word_stream(seed)
    entries: list[IPEntry] = []
    for idx, ip in enumerate(all_ips):
        di = min(idx // max(ips_per_domain, 1), len(domains) - 1)
        domain = domains[di]
        w = next(words)
        entries.append(IPEntry(ip=ip, hostname=f"{w}.{domain}", username=w, domain=domain))
    return entries


def build_for_environment(
    ranges: list[IPRange],
    environment: str,
    *,
    domains: list[str] | None = None,
    test_ips_per_range: int = 15,
    ips_per_domain: int = 16,
    seed: int = 42,
) -> list[IPEntry]:
    """Dispatch on environment: 'testing' -> sample N/range, 'production' -> 1:N.
    Anything else falls back to the pattern-based build_ip_entries()."""
    if environment == "testing":
        return build_testing(ranges, test_ips_per_range, domains=domains, seed=seed)
    if environment == "production":
        doms = domains or [r.domain for r in ranges if r.domain]
        return build_production(ranges, doms, ips_per_domain, seed=seed)
    return build_ip_entries(ranges, seed)


# --------------------------------------------------------------------------- #
#  Import
# --------------------------------------------------------------------------- #
def parse_input(
    text: str,
    default_domain: str = "",
    preset: str = "s{n}",
) -> list[IPRange]:
    """Accept a headered CSV (ip|cidr, domain, hostname_pattern, username_pattern)
    OR a plain list of IPs/CIDRs (one per line)."""
    text = text.strip()
    if not text:
        return []
    hp_default, up_default = NAMING_PRESETS.get(preset, NAMING_PRESETS["s{n}"])

    first = text.splitlines()[0].lower()
    rows: list[dict[str, str]] = []
    if any(h in first for h in ("cidr", "ip", "domain")):
        for row in csv.DictReader(io.StringIO(text)):
            rows.append({(k or "").strip().lower(): (v or "").strip() for k, v in row.items()})
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append({("cidr" if "/" in line else "ip"): line})

    ranges: list[IPRange] = []
    for r in rows:
        domain = r.get("domain") or default_domain
        hp = r.get("hostname_pattern") or hp_default
        up = r.get("username_pattern") or up_default
        if r.get("cidr"):
            ranges.append(IPRange(cidr=r["cidr"], domain=domain, hostname_pattern=hp, username_pattern=up))
        elif r.get("ip"):
            ranges.append(IPRange(ips=[r["ip"]], domain=domain, hostname_pattern=hp, username_pattern=up))
    return ranges


# --------------------------------------------------------------------------- #
#  Validation (returns prompts, never raises)
# --------------------------------------------------------------------------- #
def validate_plan(ranges: list[IPRange]) -> list[str]:
    """Return human-readable prompts for everything wrong with the plan
    (invalid/duplicate IPs, bad domains, colliding hostnames/usernames).
    Empty list == safe to build."""
    issues: list[str] = []
    if not ranges:
        return ["No IP ranges / IPs provided."]
    for i, r in enumerate(ranges, 1):
        if not r.domain:
            issues.append(f"Range {i}: domain is required (assign a sending domain).")
        elif not validation.is_valid_domain(r.domain):
            issues.append(f"Range {i}: invalid domain '{r.domain}'.")
        if not r.cidr and not r.ips:
            issues.append(f"Range {i}: provide a CIDR or IP list.")
        if r.cidr and not validation.is_valid_cidr(r.cidr):
            issues.append(f"Range {i}: invalid CIDR '{r.cidr}'.")
        for ip in r.ips:
            if not validation.is_valid_ip(ip):
                issues.append(f"Range {i}: invalid IP '{ip}'.")

    # cross-range: duplicate IPs
    all_ips: list[str] = []
    for r in ranges:
        try:
            all_ips.extend(expand_range(r))
        except ValueError:
            pass
    dup_ips = validation.duplicates(all_ips)
    if dup_ips:
        issues.append("Duplicate IP(s): " + ", ".join(dup_ips[:5]))

    # colliding generated names (only meaningful once inputs are otherwise valid)
    if not issues:
        entries = build_ip_entries(ranges)
        for ip in entries:
            if not validation.is_valid_hostname(ip.hostname):
                issues.append(f"Invalid hostname generated: '{ip.hostname}' "
                              "(check the pattern / domain).")
                break
        dup_h = validation.duplicates([e.hostname for e in entries])
        dup_u = validation.duplicates([e.username for e in entries])
        if dup_h:
            issues.append("Duplicate hostname(s): " + ", ".join(dup_h[:5]))
        if dup_u:
            issues.append("Duplicate SMTP username(s): " + ", ".join(dup_u[:5]))
    return issues


# --------------------------------------------------------------------------- #
#  Preview
# --------------------------------------------------------------------------- #
def _duplicates(items: list[str]) -> list[str]:
    seen: set[str] = set()
    dup: set[str] = set()
    for x in items:
        if x in seen:
            dup.add(x)
        seen.add(x)
    return sorted(dup)


def preview(ranges: list[IPRange], seed: int = 42, sample: int = 8) -> dict:
    entries = build_ip_entries(ranges, seed)
    per_domain: dict[str, int] = {}
    for e in entries:
        per_domain[e.domain] = per_domain.get(e.domain, 0) + 1
    return {
        "total_ips": len(entries),
        "per_domain": per_domain,
        "sample": entries[:sample],
        "duplicate_hostnames": _duplicates([e.hostname for e in entries]),
        "duplicate_usernames": _duplicates([e.username for e in entries]),
        "entries": entries,
    }
