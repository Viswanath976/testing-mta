"""Phase B (testing N-random / production 1:16) + Phase C (ports, firewall, import)."""
from kumo_automation.core.firewall import CHAIN, render_rules
from kumo_automation.core.importer import detect_ports
from kumo_automation.core.ipplan import (
    build_for_environment,
    build_production,
    build_testing,
)
from kumo_automation.core.models import (
    Environment,
    IPRange,
    ServerProfile,
    SSHTarget,
    WhitelistEntry,
)


# --- Phase B: testing N-random ---------------------------------------------- #
def test_testing_samples_n_per_range():
    ranges = [
        IPRange(cidr="203.0.113.0/24", domain="a.com"),
        IPRange(cidr="198.51.100.0/24", domain="b.com"),
    ]
    entries = build_testing(ranges, ips_per_range=15)
    assert len(entries) == 30                        # 15 per range
    assert sum(1 for e in entries if e.domain == "a.com") == 15
    assert len({e.username for e in entries}) == 30  # globally unique logins


def test_testing_takes_all_when_range_smaller_than_n():
    entries = build_testing([IPRange(ips=["10.0.0.1", "10.0.0.2"], domain="a.com")], ips_per_range=15)
    assert len(entries) == 2


def test_testing_is_deterministic():
    r = [IPRange(cidr="203.0.113.0/24", domain="a.com")]
    assert [e.ip for e in build_testing(r, 15)] == [e.ip for e in build_testing(r, 15)]


# --- Phase B: production 1:16 ----------------------------------------------- #
def test_production_16_per_domain():
    ranges = [IPRange(cidr="203.0.113.0/24", domain="")]        # 254 usable IPs
    domains = [f"d{i}.com" for i in range(16)]
    entries = build_production(ranges, domains, ips_per_domain=16)
    assert len(entries) == 254
    # first 16 -> d0, next 16 -> d1, ...
    assert all(e.domain == "d0.com" for e in entries[:16])
    assert all(e.domain == "d1.com" for e in entries[16:32])
    # remainder past the last domain folds into the last domain
    assert entries[-1].domain == "d15.com"


def test_build_for_environment_dispatch():
    ranges = [IPRange(cidr="203.0.113.0/24", domain="a.com")]
    t = build_for_environment(ranges, "testing", test_ips_per_range=15)
    p = build_for_environment(ranges, "production", domains=["a.com"], ips_per_domain=16)
    assert len(t) == 15 and len(p) == 254


# --- Phase C: ports --------------------------------------------------------- #
def test_effective_ports_default_and_explicit():
    default = ServerProfile(name="x", ssh=SSHTarget(host="1.1.1.1"), smtp_port=587)
    assert default.effective_ports() == [587, 2525]
    explicit = ServerProfile(name="x", ssh=SSHTarget(host="1.1.1.1"),
                             submission_ports=[2436, 2525])
    assert explicit.effective_ports() == [2436, 2525]


# --- Phase C: firewall ------------------------------------------------------ #
def test_firewall_render_allowlist():
    wl = [WhitelistEntry(label="mailer", ip="155.138.209.34"),
          WhitelistEntry(label="mgmt", ip="45.120.56.102")]
    script = render_rules([2436, 2525], wl)
    assert f"iptables -F {CHAIN}" in script                     # idempotent flush
    assert "-i lo -j ACCEPT" in script                          # loopback fix
    assert "155.138.209.34 -j ACCEPT" in script
    assert "45.120.56.102 -j ACCEPT" in script
    assert "--dports 2436,2525 -j DROP" in script               # deny the rest
    assert "iptables-save" in script                            # persisted


# --- Phase C: importer ------------------------------------------------------ #
def test_detect_ports_from_init():
    init = (
        "kumo.start_esmtp_listener { listen = '127.0.0.1:2526', relay_hosts = {} }\n"
        "kumo.start_esmtp_listener { listen = '0.0.0.0:2436' }\n"
        "kumo.start_esmtp_listener { listen = '0.0.0.0:2525' }\n")
    assert detect_ports(init) == [2436, 2525]      # 2526 is localhost-only, excluded
