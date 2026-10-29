"""Phase 3 — centralized monitoring integration.

Registers a KumoMTA server into the central hub (la412):
  * Prometheus  — writes a file_sd target JSON on the hub, then reloads Prometheus
  * Grafana     — a friendly `display` label so the server appears in the dropdown
  * Loki        — generates a promtail config the server uses to ship logs

Pure generators (no SSH) are unit-testable; the register/verify functions take an
already-connected SSHClient to the hub, so the transport stays swappable.
"""
from __future__ import annotations

import json

from .models import HealthCheck, MonitoringHub, ServerProfile
from .ssh import SSHClient


# --------------------------------------------------------------------------- #
#  Pure generators (offline / testable)
# --------------------------------------------------------------------------- #
def default_display(profile: ServerProfile) -> str:
    """Friendly Grafana label. Explicit override wins, else '<name> (<host>)'."""
    if profile.monitoring.display:
        return profile.monitoring.display
    return f"{profile.name} ({profile.ssh.host})"


def safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip("_") or "server"


def target_filename(profile: ServerProfile) -> str:
    return f"{safe_name(profile.name)}.json"


def prometheus_file_sd(profile: ServerProfile) -> list[dict]:
    """One file_sd entry: scrape the box's :8000 metrics endpoint, richly labelled."""
    labels = {
        "server_name": profile.name,
        "display": default_display(profile),
        "environment": profile.environment.value,
        "hostname": profile.ips[0].hostname if profile.ips else profile.ssh.host,
        "ip": profile.ssh.host,
    }
    return [{"targets": [f"{profile.ssh.host}:8000"], "labels": labels}]


def prometheus_file_sd_json(profile: ServerProfile) -> str:
    return json.dumps(prometheus_file_sd(profile), indent=2)


def promtail_config(profile: ServerProfile, loki_push_url: str) -> str:
    """Promtail config the *server* uses to ship KumoMTA logs to central Loki,
    tagged with the same friendly labels as the metrics."""
    return (
        "server:\n"
        "  http_listen_port: 9080\n"
        "  grpc_listen_port: 0\n"
        "positions:\n"
        "  filename: /var/lib/promtail/positions.yaml\n"
        "clients:\n"
        f"  - url: {loki_push_url}\n"
        "scrape_configs:\n"
        "  - job_name: kumomta\n"
        "    static_configs:\n"
        "      - targets: [localhost]\n"
        "        labels:\n"
        "          job: kumomta\n"
        f"          environment: {profile.environment.value}\n"
        f"          server_name: {profile.name}\n"
        f"          display: {default_display(profile)}\n"
        f"          ip: {profile.ssh.host}\n"
        "          __path__: /var/log/kumomta/*.log\n"
    )


# --------------------------------------------------------------------------- #
#  Hub operations (need an SSHClient connected to the HUB)
# --------------------------------------------------------------------------- #
def register_server(profile: ServerProfile, hub: MonitoringHub, hub_ssh: SSHClient) -> HealthCheck:
    """Write/replace this server's file_sd target on the hub and reload Prometheus."""
    hub_ssh.mkdirs(hub.file_sd_dir)
    path = f"{hub.file_sd_dir}/{target_filename(profile)}"
    hub_ssh.put_content(path, prometheus_file_sd_json(profile))
    reloaded = _reload_prometheus(hub, hub_ssh)
    return HealthCheck(name="prometheus-register", ok=reloaded,
                       detail=f"{path} ({'reloaded' if reloaded else 'written; reload failed'})")


def deregister_server(profile: ServerProfile, hub: MonitoringHub, hub_ssh: SSHClient) -> HealthCheck:
    path = f"{hub.file_sd_dir}/{target_filename(profile)}"
    hub_ssh.run(f"rm -f {path}")
    reloaded = _reload_prometheus(hub, hub_ssh)
    return HealthCheck(name="prometheus-deregister", ok=reloaded, detail=path)


def verify_target(profile: ServerProfile, hub: MonitoringHub, hub_ssh: SSHClient) -> HealthCheck:
    """Ask the hub's Prometheus whether this server's target is UP."""
    query = f'up{{server_name="{profile.name}"}}'
    cmd = (
        f"curl -s -G {hub.prometheus_url}/api/v1/query "
        f"--data-urlencode 'query={query}' "
        "| python3 -c 'import sys,json; "
        "r=json.load(sys.stdin)[\"data\"][\"result\"]; "
        "print(\"UP\" if r and r[0][\"value\"][1]==\"1\" else \"DOWN\")'"
    )
    r = hub_ssh.run(cmd)
    out = r.stdout.strip()
    return HealthCheck(name="monitoring-scrape", ok=out == "UP",
                       detail=out or (r.stderr.strip()[:80] or "no response"))


def verify_all(profile: ServerProfile, hub: MonitoringHub, hub_ssh: SSHClient) -> list[HealthCheck]:
    """Full monitoring verification: Prometheus target UP, metrics scraped,
    Loki receiving this server's logs, Grafana reachable."""
    checks: list[HealthCheck] = []
    target = verify_target(profile, hub, hub_ssh)
    checks.append(HealthCheck(name="Prometheus target", ok=target.ok,
                              status="pass" if target.ok else "fail", detail=target.detail))
    # metrics: if the target is up, Prometheus is scraping :8000 successfully
    checks.append(HealthCheck(name="Metrics", ok=target.ok,
                              status="pass" if target.ok else "warn",
                              detail="scraped" if target.ok else "not scraped yet"))

    loki = hub_ssh.run(
        f"curl -s --max-time 8 '{hub.loki_push_url.replace('/loki/api/v1/push', '')}"
        "/loki/api/v1/label/server_name/values'")
    loki_ok = profile.name in (loki.stdout or "")
    checks.append(HealthCheck(name="Loki logs", ok=loki_ok,
                              status="pass" if loki_ok else "warn",
                              detail="receiving logs" if loki_ok else "no logs for this server yet"))

    graf = hub_ssh.run("curl -s -o /dev/null -w '%{http_code}' --max-time 8 "
                       "http://127.0.0.1:3000/api/health")
    graf_ok = graf.stdout.strip() == "200"
    checks.append(HealthCheck(name="Grafana dashboard", ok=graf_ok,
                              status="pass" if graf_ok else "warn",
                              detail="reachable" if graf_ok else f"HTTP {graf.stdout.strip() or '?'}"))
    return checks


def _reload_prometheus(hub: MonitoringHub, hub_ssh: SSHClient) -> bool:
    if hub.reload_method == "http":
        r = hub_ssh.run(
            f"curl -s -o /dev/null -w '%{{http_code}}' -X POST {hub.prometheus_url}/-/reload"
        )
        return r.stdout.strip() in ("200", "204")
    if hub.reload_method == "docker":
        return hub_ssh.run(f"docker kill --signal=SIGHUP {hub.prometheus_container}").ok
    # sighup (bare-metal)
    return hub_ssh.run("pkill -HUP prometheus").ok
