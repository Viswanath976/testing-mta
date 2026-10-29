"""Loki-forwarder integration (pure parts): config, labels, staging, systemd."""
from kumo_automation.core import forwarder
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    MonitoringConfig,
    MonitoringHub,
    ServerProfile,
    SSHTarget,
)


def _profile():
    return ServerProfile(
        name="Prod 01", environment=Environment.production,
        ssh=SSHTarget(host="84.33.9.9", use_password=False),
        ips=[IPEntry(ip="84.33.9.10", hostname="s1.x.com", username="s1", domain="x.com")],
        shared_password="pw", domains=["x.com"],
        monitoring=MonitoringConfig(enabled=True, display="Prod 01 (la)"))


def _hub():
    return MonitoringHub(ssh=SSHTarget(host="10.0.0.5", use_password=False),
                         loki_push_url="http://127.0.0.1:3100/loki/api/v1/push")


def test_labels_have_production_scheme():
    lb = forwarder.labels(_profile())
    assert lb["environment"] == "production"
    assert lb["server_name"] == "Prod 01"
    assert lb["display"] == "Prod 01 (la)"
    assert set(lb) >= {"environment", "server_name", "hostname", "ip", "display"}


def test_hub_loki_url_is_reachable():
    # 127.0.0.1 (hub-local) must become the hub's reachable IP for a remote server
    assert forwarder.hub_loki_url(_hub()) == "http://10.0.0.5:3100/loki/api/v1/push"


def test_forwarder_config():
    cfg = forwarder.forwarder_config(_profile(), _hub())
    assert cfg["loki_url"] == "http://10.0.0.5:3100/loki/api/v1/push"
    assert cfg["labels"]["server_name"] == "Prod 01"


def test_default_systemd_unit():
    u = forwarder.default_systemd_unit("/usr/bin/python3 /opt/kumo-loki-forwarder/forwarder.py")
    assert "ExecStart=/usr/bin/python3" in u and "WantedBy=multi-user.target" in u


def test_available(tmp_path):
    assert not forwarder.available(tmp_path)          # empty
    (tmp_path / "forwarder.py").write_text("print('x')", encoding="utf-8")
    assert forwarder.available(tmp_path)              # staged


def test_entrypoint_prefers_known_names():
    assert "forwarder.py" in forwarder._entrypoint(["forwarder.py", "util.py"])
    assert "main.py" in forwarder._entrypoint(["util.py", "main.py"])
