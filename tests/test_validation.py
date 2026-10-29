"""Phase A QA — validation + duplicate detection (uses the user's exact examples)."""
import pytest

from kumo_automation.core import ipplan, validation
from kumo_automation.core.generators import generate_all
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    IPRange,
    ServerProfile,
    SSHTarget,
)


# ------------------------------- IPs / CIDRs ------------------------------- #
@pytest.mark.parametrize("bad", ["84.33.300.1", "abc.xyz", "84.33.20", ""])
def test_invalid_ip_rejected(bad):
    assert not validation.is_valid_ip(bad)


def test_valid_ip_accepted():
    assert validation.is_valid_ip("84.33.20.129")


@pytest.mark.parametrize("bad", ["84.33.20.128/45", "84.33.20.0/50", "abc/24"])
def test_invalid_cidr_rejected(bad):
    assert not validation.is_valid_cidr(bad)


def test_valid_cidr_accepted():
    assert validation.is_valid_cidr("84.33.20.128/25")


# ------------------------------- domains ----------------------------------- #
@pytest.mark.parametrize("bad", ["nutri flora.com", "gmail", "google", "", "-bad.com"])
def test_invalid_domain_rejected(bad):
    assert not validation.is_valid_domain(bad)


@pytest.mark.parametrize("good", ["nutriflorahub.com", "offers.healthurbancare.com"])
def test_valid_domain_accepted(good):
    assert validation.is_valid_domain(good)


# ------------------------------- misc validators --------------------------- #
def test_dkim_selector():
    assert not validation.is_valid_dkim_selector("#####")
    assert validation.is_valid_dkim_selector("s1")


def test_smtp_port_status():
    for p in (25, 465, 587, 2525):
        assert validation.smtp_port_status(p) == "ok"
    assert validation.smtp_port_status(8587) == "warn"


def test_ssh_port_range():
    assert validation.is_valid_ssh_port(587)
    assert not validation.is_valid_ssh_port(0)
    assert not validation.is_valid_ssh_port(70000)


def test_password_warning():
    assert validation.password_warning("123")           # weak
    assert validation.password_warning("abcdefgh")       # letters only
    assert validation.password_warning("Str0ng#Pass") is None


def test_sanitize_host():
    assert validation.sanitize_host("ssh root@198.23.177.154") == "198.23.177.154"
    assert validation.sanitize_host("root@198.23.177.154") == "198.23.177.154"
    assert validation.sanitize_host("  198.23.177.154 ") == "198.23.177.154"


# ------------------------------- plan-level -------------------------------- #
def _r(cidr, domain, hp="s{n}.{domain}", up="s{n}"):
    return IPRange(cidr=cidr, domain=domain, hostname_pattern=hp, username_pattern=up)


def test_plan_flags_duplicate_ip_ranges():
    issues = ipplan.validate_plan([_r("84.33.20.128/30", "a.com"), _r("84.33.20.128/30", "b.com")])
    assert any("Duplicate IP" in m for m in issues)


def test_plan_flags_empty_domain():
    issues = ipplan.validate_plan([_r("84.33.20.128/30", "")])
    assert any("domain is required" in m for m in issues)


def test_plan_flags_invalid_domain():
    issues = ipplan.validate_plan([_r("84.33.20.128/30", "gmail")])
    assert any("invalid domain" in m for m in issues)


def test_plan_flags_duplicate_hostname_when_pattern_constant():
    # constant pattern -> same hostname/username for every IP
    issues = ipplan.validate_plan([_r("84.33.20.128/30", "a.com", hp="smtp1.{domain}", up="smtp1")])
    assert any("Duplicate hostname" in m for m in issues) or any("Duplicate SMTP username" in m for m in issues)


def test_plan_clean_when_valid():
    assert ipplan.validate_plan([_r("84.33.20.128/30", "a.com"), _r("84.33.30.0/30", "b.com")]) == []


# ------------------------------- generator guard --------------------------- #
def test_generator_rejects_duplicate_usernames():
    prof = ServerProfile(
        name="dup", environment=Environment.testing,
        ssh=SSHTarget(host="h", use_password=False), shared_password="pw", domains=["x.com"],
        ips=[IPEntry(ip="1.1.1.1", hostname="a.x.com", username="smtp1", domain="x.com"),
             IPEntry(ip="2.2.2.2", hostname="b.x.com", username="smtp1", domain="x.com")],
    )
    with pytest.raises(ValueError, match="Duplicate SMTP username"):
        generate_all(prof)
