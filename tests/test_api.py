"""FastAPI endpoints on a temp SQLite DB (no Postgres needed)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from kumo_automation.db import store
    store.setup(f"sqlite:///{tmp_path / 'api.db'}")   # engine cached before startup
    from kumo_automation.api.app import app
    return TestClient(app)


def _customer_payload():
    return {
        "name": "Evolutra",
        "notes": "fleet",
        "servers": [{
            "name": "buf1393",
            "environment": "production",
            "ssh": {"host": "23.229.25.210", "port": 22, "username": "root"},
            "password_strategy": "per_ip",
            "submission_ports": [2436, 2525],
            "hide_received": True,
            "dns": {"dkim_selector": "s1", "spf_individual_ips": True},
            "ips": [
                {"ip": "69.4.94.1", "hostname": "a.d.com", "username": "a", "password": "pw1", "domain": "d.com"},
                {"ip": "69.4.94.2", "hostname": "b.d.com", "username": "b", "password": "pw2", "domain": "d.com"},
            ],
        }],
    }


def test_health(client):
    assert client.get("/health").json()["ok"] is True


def test_customer_crud(client):
    r = client.post("/customers", json=_customer_payload())
    assert r.status_code == 200 and r.json()["servers"] == 1

    lst = client.get("/customers").json()["customers"]
    assert lst[0]["name"] == "Evolutra" and lst[0]["total_ips"] == 2

    full = client.get("/customers/Evolutra").json()
    assert full["servers"][0]["ips"][0]["password"] == "pw1"   # decrypted back

    assert client.delete("/customers/Evolutra").json()["deleted"] is True
    assert client.get("/customers").json()["customers"] == []


def test_preview_and_dns(client, monkeypatch):
    from kumo_automation.core import dnsgen
    monkeypatch.setattr(dnsgen, "existing_spf", lambda d: None)   # no live DNS
    client.post("/customers", json=_customer_payload())

    files = client.get("/customers/Evolutra/servers/buf1393/preview").json()
    assert "init.lua" in files and "sources.toml" in files
    assert "0.0.0.0:2436" in files["init.lua"]                    # ports flowed through

    dns = client.get("/customers/Evolutra/servers/buf1393/dns").json()["records"]
    spf = next(r for r in dns if r["type"] == "TXT" and r["name"] == "d.com")
    assert "ip4:69.4.94.1" in spf["value"] and "ip4:69.4.94.2" in spf["value"]


def test_cpanel_accounts_api(client):
    client.post("/cpanel-accounts", json={"host": "h:2083", "user": "u", "domains": ["d.com"], "token": "T"})
    accts = client.get("/cpanel-accounts").json()["accounts"]
    assert accts[0]["user"] == "u" and "d.com" in accts[0]["domains"]


def test_backup_sqlite(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    client.post("/customers", json=_customer_payload())
    res = client.post("/backup", params={"stamp": "20260829"}).json()
    import os
    assert os.path.exists(res["path"]) and res["path"].endswith(".sqlite")
