"""Prerequisite parsers + KumoMTA installer logic (pure, no SSH)."""
from kumo_automation.core import installer, prereqs


# ------------------------------- prereqs ----------------------------------- #
def test_ubuntu_pass():
    c = prereqs.parse_os("ubuntu", "22.04")
    assert c.state == "pass" and c.name == "Operating System" and "Supported" in c.detail


def test_ubuntu_old_warns():
    assert prereqs.parse_os("ubuntu", "18.04").state == "warn"


def test_debian_is_supported():
    # Debian is supported by KumoMTA — must NOT be a failure.
    c = prereqs.parse_os("debian", "13")
    assert c.state == "pass" and "Debian 13" in c.detail and "Supported" in c.detail
    assert prereqs.parse_os("debian", "12").state == "pass"


def test_unsupported_os_warns_not_fails():
    assert prereqs.parse_os("rocky", "9").state == "warn"


def test_disk_thresholds():
    assert prereqs.parse_disk(str(5_000_000)).state == "pass"      # ~4.7 GB
    assert prereqs.parse_disk(str(1_000_000)).state == "warn"      # ~1 GB
    assert prereqs.parse_disk(str(100_000)).state == "fail"        # ~100 MB


def test_memory_thresholds():
    assert prereqs.parse_memory(str(2_000_000)).state == "pass"
    assert prereqs.parse_memory(str(700_000)).state == "warn"
    assert prereqs.parse_memory(str(100_000)).state == "fail"


def test_internet():
    assert prereqs.parse_internet("200").state == "pass"
    # a 404 still proves the internet was reached — must be a PASS, not a failure
    assert prereqs.parse_internet("404").state == "pass"
    assert prereqs.parse_internet("500").state == "pass"
    assert prereqs.parse_internet("200").detail == "Connected"
    assert prereqs.parse_internet("000").state == "fail"   # curl could not connect
    assert prereqs.parse_internet("").state == "fail"


def test_hostname_fqdn():
    assert prereqs.parse_hostname("mail.example.com").state == "pass"
    assert prereqs.parse_hostname("localhost").state == "warn"


def test_firewall_active_warns():
    assert prereqs.parse_firewall("Status: active").state == "warn"
    assert prereqs.parse_firewall("Status: inactive").state == "pass"


def test_timesync():
    assert prereqs.parse_timesync("yes").state == "pass"
    assert prereqs.parse_timesync("no").state == "warn"


# ------------------------------- installer --------------------------------- #
def test_parse_version():
    assert installer.parse_version("kumod 2026.05.01") == "2026.05.01"
    assert installer.parse_version("kumod v2026.08") == "2026.08"
    assert installer.parse_version("") == ""


def test_upgrade_available():
    info = installer.InstallInfo(installed=True, version="2026.05", latest="2026.08")
    assert info.upgrade_available
    assert not installer.InstallInfo(installed=True, version="2026.08", latest="2026.08").upgrade_available
    assert not installer.InstallInfo(installed=False).upgrade_available


def test_install_script_shape():
    s = installer.install_script()
    # Debian/Ubuntu path
    assert "apt-get install -y kumomta" in s
    # RHEL/Rocky/AlmaLinux path
    assert "dnf install -y kumomta" in s
    assert "command -v dnf" in s
    # common tail
    assert "systemctl enable kumomta" in s
    assert "INSTALL DONE" in s
