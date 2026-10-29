"""Server-creation planning: CIDR expansion, naming, CSV import, validation, preview."""
from kumo_automation.core import ipplan
from kumo_automation.core.models import IPRange


def test_expand_cidr_skips_network_and_broadcast():
    ips = ipplan.expand_range(IPRange(cidr="203.0.113.0/24", domain="d"))
    assert len(ips) == 254
    assert ips[0] == "203.0.113.1" and ips[-1] == "203.0.113.254"


def test_expand_slash_25():
    ips = ipplan.expand_range(IPRange(cidr="203.0.113.0/25", domain="d"))
    assert len(ips) == 126


def test_naming_patterns():
    ranges = [IPRange(cidr="203.0.113.0/30", domain="example.com",
                      hostname_pattern="smtp{n}.{domain}", username_pattern="smtp{n}")]
    entries = ipplan.build_ip_entries(ranges)
    assert entries[0].hostname == "smtp1.example.com"
    assert entries[0].username == "smtp1"
    assert entries[1].hostname == "smtp2.example.com"


def test_word_pattern_is_unique():
    ranges = [IPRange(cidr="203.0.113.0/24", domain="example.com",
                      hostname_pattern="{word}.{domain}", username_pattern="{word}")]
    entries = ipplan.build_ip_entries(ranges)
    hostnames = [e.hostname for e in entries]
    assert len(set(hostnames)) == len(hostnames)  # all unique


def test_word_pool_big_enough_for_thousands_clean():
    # 80x80 = 6400 clean combos, so the first 5080+ words carry NO numeric suffix
    import itertools
    words = list(itertools.islice(ipplan.word_stream(), 5080))
    assert len(set(words)) == 5080                                # all unique
    assert not any(any(c.isdigit() for c in w) for w in words)    # no s2-style suffix


def test_word_naming_clean_across_many_24s():
    # 5 /24s (1270 IPs) on word naming -> unique, clean (no digits) usernames
    ranges = [IPRange(cidr=f"10.0.{i}.0/24", domain="d.com",
                      hostname_pattern="{word}.{domain}", username_pattern="{word}")
              for i in range(5)]
    entries = ipplan.build_ip_entries(ranges)
    users = [e.username for e in entries]
    assert len(entries) == 254 * 5
    assert len(set(users)) == len(users)                          # globally unique
    assert not any(any(c.isdigit() for c in u) for u in users)    # clean words


def test_multiple_ranges_different_domains():
    ranges = [
        IPRange(cidr="203.0.113.0/30", domain="a.com", hostname_pattern="s{n}.{domain}", username_pattern="s{n}"),
        IPRange(cidr="198.51.100.0/30", domain="b.com", hostname_pattern="s{n}.{domain}", username_pattern="s{n}"),
    ]
    pv = ipplan.preview(ranges)
    assert pv["per_domain"] == {"a.com": 2, "b.com": 2}
    assert pv["total_ips"] == 4
    assert not pv["duplicate_hostnames"]


def test_csv_import_headered():
    csv_text = "cidr,domain,hostname_pattern,username_pattern\n" \
               "203.0.113.0/29,shop.com,mail{n}.{domain},mail{n}\n"
    ranges = ipplan.parse_input(csv_text)
    assert ranges[0].cidr == "203.0.113.0/29"
    assert ranges[0].domain == "shop.com"
    entries = ipplan.build_ip_entries(ranges)
    assert entries[0].hostname == "mail1.shop.com"


def test_csv_import_plain_list():
    ranges = ipplan.parse_input("203.0.113.0/30\n198.51.100.5\n", default_domain="x.com")
    assert ranges[0].cidr == "203.0.113.0/30"
    assert ranges[1].ips == ["198.51.100.5"]


def test_validation_reports_missing_domain():
    issues = ipplan.validate_plan([IPRange(cidr="203.0.113.0/30")])
    assert any("domain is required" in m for m in issues)


def test_validation_bad_cidr():
    issues = ipplan.validate_plan([IPRange(cidr="not-a-cidr", domain="d")])
    assert any("invalid CIDR" in m for m in issues)
