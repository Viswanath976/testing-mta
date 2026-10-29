"""DNS: per-domain individual-IP SPF, 2-file split; per-server uniqueness."""
from kumo_automation.core import dnsgen
from kumo_automation.core.ipplan import server_seed
from kumo_automation.core.models import (
    DnsConfig,
    Environment,
    IPEntry,
    ServerProfile,
    SSHTarget,
)
from kumo_automation.core.validation import cross_server_collisions


def _profile(**kw) -> ServerProfile:
    base = dict(
        name="s1",
        environment=Environment.production,
        ssh=SSHTarget(host="1.2.3.4"),
        ips=[
            IPEntry(ip="69.4.94.1", hostname="a1.d1.com", username="a1", domain="d1.com"),
            IPEntry(ip="69.4.94.2", hostname="a2.d1.com", username="a2", domain="d1.com"),
            IPEntry(ip="69.4.94.17", hostname="b1.d2.com", username="b1", domain="d2.com"),
        ],
    )
    base.update(kw)
    return ServerProfile(**base)


def _spf_for(records, domain):
    return next(r.value for r in records
               if r.type == "TXT" and r.name == domain and r.value.startswith("v=spf1"))


def test_spf_lists_only_that_domains_ips(monkeypatch):
    # isolate from live DNS: pretend nothing is published yet
    monkeypatch.setattr(dnsgen, "existing_spf", lambda d: None)
    # DnsConfig.spf_individual_ips defaults True -> each domain gets ONLY its IPs
    recs, _ = dnsgen.generate_records(_profile())
    d1 = _spf_for(recs, "d1.com")
    d2 = _spf_for(recs, "d2.com")
    assert "ip4:69.4.94.1" in d1 and "ip4:69.4.94.2" in d1
    assert "ip4:69.4.94.17" not in d1          # d2's IP must NOT be in d1's SPF
    assert d2 == "v=spf1 ip4:69.4.94.17 ~all"  # d2 only lists its own IP
    assert d1.endswith("~all")


def test_spf_range_mode_when_individual_off(monkeypatch):
    monkeypatch.setattr(dnsgen, "existing_spf", lambda d: None)
    p = _profile(dns=DnsConfig(spf_individual_ips=False, spf_ip_ranges=["69.4.94.0/25"]))
    recs, _ = dnsgen.generate_records(p)
    assert "ip4:69.4.94.0/25" in _spf_for(recs, "d1.com")


def test_split_into_dns_and_ptr(tmp_path, monkeypatch):
    monkeypatch.setattr(dnsgen, "existing_spf", lambda d: None)
    recs, _ = dnsgen.generate_records(_profile())
    dns, ptr = dnsgen.split_records(recs)
    assert all(r.type != "PTR" for r in dns)
    assert ptr and all(r.type == "PTR" for r in ptr)
    dns_path, ptr_path = dnsgen.write_dns_and_ptr(recs, tmp_path)
    assert dns_path.endswith("dns-records.xlsx") and ptr_path.endswith("ptr-records.xlsx")
    import os
    assert os.path.exists(dns_path) and os.path.exists(ptr_path)


def test_dmarc_is_none(monkeypatch):
    monkeypatch.setattr(dnsgen, "existing_spf", lambda d: None)
    recs, _ = dnsgen.generate_records(_profile())
    dmarc = next(r.value for r in recs if r.name == "_dmarc.d1.com")
    assert "p=none" in dmarc


def test_server_seed_is_unique_per_name():
    assert server_seed("buf1393") != server_seed("mtl232") != server_seed("testing")
    assert server_seed("buf1393") == server_seed("buf1393")   # deterministic


def test_cross_server_collision_detected():
    a = _profile(name="buf1393")
    b = _profile(name="mtl232")   # same usernames/domains -> should warn
    warns = cross_server_collisions([a, b])
    assert any("Username 'a1'" in w for w in warns)
    assert any("Domain 'd1.com'" in w for w in warns)
    # a distinct server produces no warning against itself
    assert cross_server_collisions([a]) == []
