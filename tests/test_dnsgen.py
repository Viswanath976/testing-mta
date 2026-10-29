"""Phase 2 Feature 1 — DNS generation (headless, no network)."""
from kumo_automation.core import dnsgen
from kumo_automation.core.models import (
    DnsConfig,
    Environment,
    IPEntry,
    ServerProfile,
    SSHTarget,
)


def _profile() -> ServerProfile:
    return ServerProfile(
        name="T",
        environment=Environment.production,
        ssh=SSHTarget(host="1.2.3.4"),
        domains=["example.com"],
        dns=DnsConfig(dkim_selector="s1", spf_ip_ranges=["203.0.113.0/24"]),
        ips=[
            IPEntry(ip="203.0.113.10", hostname="a.example.com", username="a", domain="example.com"),
            IPEntry(ip="203.0.113.11", hostname="b.example.com", username="b", domain="example.com"),
        ],
    )


def test_dkim_keypair_shape():
    priv, pub = dnsgen.generate_dkim_keypair(bits=1024)  # small = fast test
    assert "PRIVATE KEY" in priv
    assert pub.startswith("v=DKIM1; k=rsa; p=")


def test_spf_merge_preserves_existing():
    merged = dnsgen.merge_spf("v=spf1 +a +mx ip4:198.51.100.0/24 ~all", ["203.0.113.0/24"])
    assert "+a" in merged and "+mx" in merged
    assert "ip4:198.51.100.0/24" in merged   # existing kept
    assert "ip4:203.0.113.0/24" in merged     # new added
    assert merged.endswith("~all")


def test_spf_merge_no_existing():
    assert dnsgen.merge_spf(None, ["203.0.113.0/24"]) == "v=spf1 ip4:203.0.113.0/24 ~all"


def test_generate_records_covers_everything():
    records, dkim = dnsgen.generate_records(_profile())
    types = {(r.type, r.name) for r in records}
    assert ("A", "a.example.com") in types
    assert ("PTR", "203.0.113.10") in types
    assert ("TXT", "example.com") in types                       # SPF
    assert ("TXT", "s1._domainkey.example.com") in types         # DKIM
    assert ("TXT", "_dmarc.example.com") in types                # DMARC
    assert "example.com" in dkim and "PRIVATE KEY" in dkim["example.com"]


def test_csv_export():
    records, _ = dnsgen.generate_records(_profile())
    csv = dnsgen.records_to_csv(records)
    assert csv.splitlines()[0] == "type,name,value,ttl,action,note"
    assert "203.0.113.10" in csv
