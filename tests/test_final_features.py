"""Final feature set: DNS gen/verify/export, predeploy, readiness, SMTP test."""
from kumo_automation.core import dnsgen, dnsverify, predeploy, readiness, smtptest
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    ServerProfile,
    SSHTarget,
)


def _profile(**kw):
    base = dict(
        name="T1", environment=Environment.testing,
        ssh=SSHTarget(host="203.0.113.9", use_password=False),
        shared_password="Str0ng#Pass", domains=["example.com"],
        ips=[IPEntry(ip="203.0.113.10", hostname="s1.example.com", username="s1", domain="example.com"),
             IPEntry(ip="203.0.113.11", hostname="s2.example.com", username="s2", domain="example.com")],
    )
    base.update(kw)
    return ServerProfile(**base)


# ------------------------------- DNS gen/export ---------------------------- #
def test_generate_records_types():
    # MX is intentionally NOT generated (send-only domains); A/TXT/PTR are.
    records, keys = dnsgen.generate_records(_profile())
    types = {r.type for r in records}
    assert {"A", "TXT", "PTR"} <= types
    assert "MX" not in types
    assert keys  # DKIM private key per domain


def test_dns_txt_and_csv_export():
    records, _ = dnsgen.generate_records(_profile())
    txt = dnsgen.records_to_txt(records)
    assert "TYPE" in txt and "example.com" in txt
    csv = dnsgen.records_to_csv(records)
    assert csv.startswith("type,name,value")


def test_dns_xlsx_export(tmp_path):
    records, _ = dnsgen.generate_records(_profile())
    out = tmp_path / "dns.xlsx"
    dnsgen.records_to_xlsx(records, out)
    assert out.exists() and out.stat().st_size > 0


# ------------------------------- DNS verify classifiers -------------------- #
def test_classify_a():
    assert dnsverify.classify_a("h", "1.2.3.4", ["1.2.3.4"]).state == "pass"
    assert dnsverify.classify_a("h", "1.2.3.4", ["9.9.9.9"]).state == "fail"
    assert dnsverify.classify_a("h", "1.2.3.4", []).state == "fail"


def test_classify_ptr():
    assert dnsverify.classify_ptr("1.2.3.4", "h.x.com", "h.x.com").state == "pass"
    assert dnsverify.classify_ptr("1.2.3.4", "h.x.com", "other.com").state == "warn"
    assert dnsverify.classify_ptr("1.2.3.4", "h.x.com", None).state == "fail"


def test_classify_txt():
    assert dnsverify.classify_txt("SPF", "d", "v=spf1 ip4:1.2.3.4 ~all", "v=spf1").state == "pass"
    assert dnsverify.classify_txt("SPF", "d", "junk", "v=spf1").state == "warn"
    assert dnsverify.classify_txt("SPF", "d", None, "v=spf1").state == "fail"


# ------------------------------- pre-deploy -------------------------------- #
def test_predeploy_clean_is_ready():
    checks = predeploy.structural_checks(_profile())
    ready, reasons = predeploy.verdict(checks)
    assert ready and not reasons


def test_predeploy_blocks_on_duplicate_username():
    p = _profile(ips=[
        IPEntry(ip="203.0.113.10", hostname="s1.example.com", username="dup", domain="example.com"),
        IPEntry(ip="203.0.113.11", hostname="s2.example.com", username="dup", domain="example.com"),
    ])
    ready, reasons = predeploy.verdict(predeploy.structural_checks(p))
    assert not ready
    assert any("username" in r.lower() for r in reasons)


def test_predeploy_detects_duplicate_pools():
    # same IP twice -> same pool-<ip> and tenant-<ip>
    p = _profile(ips=[
        IPEntry(ip="203.0.113.10", hostname="a.example.com", username="a", domain="example.com"),
        IPEntry(ip="203.0.113.10", hostname="b.example.com", username="b", domain="example.com"),
    ])
    checks = predeploy.structural_checks(p)
    assert any(c.name == "Unique pools" and c.state == "fail" for c in checks)


# ------------------------------- readiness --------------------------------- #
def test_readiness_zero_then_full():
    p = _profile()
    r0 = readiness.readiness(p)
    assert r0["score"] == 0 and not r0["ready"]
    for k in ("setup", "previewed", "validated", "dns", "dry_run", "health", "smtp", "monitoring"):
        p.progress[k] = True
    r1 = readiness.readiness(p)
    assert r1["score"] == 100 and r1["ready"]


# ------------------------------- SMTP test script -------------------------- #
def test_smtp_script_auth_only():
    s = smtptest.build_script(587, "s1", "pw", send=False, recipient=None, mailfrom="bounces@x.com")
    assert "user='s1'" in s and "pw='pw'" in s
    assert "s.login(user, pw)" in s
    assert "starttls" in s
    assert "send=False" in s


def test_smtp_script_send_includes_transaction():
    s = smtptest.build_script(587, "s1", "pw", send=True, recipient="a@b.com", mailfrom="bounces@x.com")
    assert "s.mail(" in s and "s.rcpt(" in s and "s.data(" in s
    assert "send=True" in s


def test_smtp_run_uses_editable_credentials_and_port():
    class FakeSSH:
        command = ""

        def run(self, command, timeout):
            self.command = command
            return type("Result", (), {
                "stdout": 'SMTPTEST{"auth":"ok","starttls":"yes","ok":true}',
                "stderr": "",
            })()

    ssh = FakeSSH()
    ip = _profile().ips[0]
    result = smtptest.run_smtp_test(
        _profile(), ssh, ip, smtp_port=2525,
        smtp_username="test-user", smtp_password="test-password")

    assert "port=2525" in ssh.command
    assert "user='test-user'" in ssh.command
    assert "pw='test-password'" in ssh.command
    assert result["smtp_port"] == 2525
    assert result["smtp_username"] == "test-user"
