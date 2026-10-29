"""Phase 3 monitoring generators (offline, no SSH)."""
import json

from kumo_automation.core import monitoring
from kumo_automation.core.models import (
    Environment,
    IPEntry,
    MonitoringConfig,
    ServerProfile,
    SSHTarget,
)


def _profile(**kw) -> ServerProfile:
    base = dict(
        name="Prod Server 01",
        environment=Environment.production,
        ssh=SSHTarget(host="203.0.113.9", username="root"),
        ips=[IPEntry(ip="203.0.113.10", hostname="s1.example.com", username="s1", domain="example.com")],
    )
    base.update(kw)
    return ServerProfile(**base)


def test_default_display_falls_back_to_name_and_host():
    assert monitoring.default_display(_profile()) == "Prod Server 01 (203.0.113.9)"


def test_display_override_wins():
    p = _profile(monitoring=MonitoringConfig(enabled=True, display="Client A — Prod"))
    assert monitoring.default_display(p) == "Client A — Prod"


def test_target_filename_is_safe():
    assert monitoring.target_filename(_profile()) == "Prod_Server_01.json"


def test_file_sd_shape_and_labels():
    sd = monitoring.prometheus_file_sd(_profile())
    assert sd[0]["targets"] == ["203.0.113.9:8000"]
    labels = sd[0]["labels"]
    assert labels["server_name"] == "Prod Server 01"
    assert labels["environment"] == "production"
    assert labels["hostname"] == "s1.example.com"
    # valid JSON round-trips
    assert json.loads(monitoring.prometheus_file_sd_json(_profile())) == sd


def test_promtail_config_has_labels_and_loki_url():
    cfg = monitoring.promtail_config(_profile(), "http://hub:3100/loki/api/v1/push")
    assert "http://hub:3100/loki/api/v1/push" in cfg
    assert "server_name: Prod Server 01" in cfg
    assert "__path__: /var/log/kumomta/*.log" in cfg
