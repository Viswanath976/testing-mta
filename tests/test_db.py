"""DB persistence: ORM<->Pydantic round-trip, encrypted secrets, cPanel accounts.
Runs on a temp SQLite file (no Postgres needed); the same code runs on Postgres
via DATABASE_URL."""
from kumo_automation.core.models import (
    Customer,
    DnsConfig,
    Environment,
    IPEntry,
    PasswordStrategy,
    ServerProfile,
    SSHTarget,
    WhitelistEntry,
)


def _store(tmp_path):
    from kumo_automation.db import store
    store.setup(f"sqlite:///{tmp_path / 'test.db'}")
    return store


def _customer() -> Customer:
    srv = ServerProfile(
        name="buf1393",
        environment=Environment.production,
        ssh=SSHTarget(host="23.229.25.210", port=22, username="root"),
        password_strategy=PasswordStrategy.per_ip,
        submission_ports=[2436, 2525],
        hide_received=True,
        firewall_whitelist=[WhitelistEntry(label="mailer", ip="155.138.209.34")],
        dns=DnsConfig(dkim_selector="s1", spf_individual_ips=True),
        ips=[
            IPEntry(ip="69.4.94.1", hostname="a.d.com", username="a", password="pw1", domain="d.com"),
            IPEntry(ip="69.4.94.2", hostname="b.d.com", username="b", password="pw2", domain="d.com"),
        ],
    )
    return Customer(name="Evolutra", notes="fleet", servers=[srv])


def test_customer_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.save_customer(_customer())
    assert "Evolutra" in store.list_customer_names()

    c = store.load_customer("Evolutra")
    assert c and len(c.servers) == 1
    s = c.servers[0]
    assert s.name == "buf1393" and s.ssh.host == "23.229.25.210"
    assert s.submission_ports == [2436, 2525] and s.hide_received is True
    assert s.dns.spf_individual_ips is True
    assert [w.ip for w in s.firewall_whitelist] == ["155.138.209.34"]
    assert len(s.ips) == 2
    # passwords decrypt back to plaintext
    assert {e.username: e.password for e in s.ips} == {"a": "pw1", "b": "pw2"}


def test_save_is_upsert(tmp_path):
    store = _store(tmp_path)
    store.save_customer(_customer())
    c = _customer()
    c.servers[0].ips = c.servers[0].ips[:1]     # now one IP
    store.save_customer(c)                        # replaces, not duplicates
    got = store.load_customer("Evolutra")
    assert len(got.servers[0].ips) == 1
    assert store.list_customer_names() == ["Evolutra"]  # no duplicate customer


def test_delete_customer(tmp_path):
    store = _store(tmp_path)
    store.save_customer(_customer())
    assert store.delete_customer("Evolutra") is True
    assert store.list_customer_names() == []


def test_secrets_encrypted_at_rest(tmp_path):
    store = _store(tmp_path)
    store.save_customer(_customer())
    # read the raw DB — plaintext password must NOT appear
    import sqlite3
    raw = sqlite3.connect(str(tmp_path / "test.db")).execute(
        "SELECT password_enc FROM ips").fetchall()
    blob = " ".join(r[0] or "" for r in raw)
    assert "pw1" not in blob and "pw2" not in blob and blob   # encrypted, non-empty


def test_cpanel_accounts(tmp_path):
    store = _store(tmp_path)
    store.save_cpanel_account("host:2083", "brightne", ["d1.com", "d2.com"], token="TOK123")
    accts = store.list_cpanel_accounts()
    assert accts and accts[0].user == "brightne" and "d1.com" in accts[0].domains
    assert store.cpanel_token("host:2083", "brightne") == "TOK123"   # decrypts
