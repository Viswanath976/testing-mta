"""Deployment report (HTML). PDF export can be added later via weasyprint."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .. import __version__
from .models import DeployResult, DnsCheck, HealthCheck, ServerProfile
from .templating import render


def build_report_html(
    profile: ServerProfile,
    deploy: DeployResult | None,
    dns: list[DnsCheck],
    health: list[HealthCheck],
) -> str:
    return render(
        "report.html.j2",
        profile=profile,
        deploy=deploy,
        dns=dns,
        health=health,
        generated=datetime.now().isoformat(timespec="seconds"),
        version=__version__,
    )


def save_report(html: str, out_dir: Path, server_name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in server_name)
    path = out_dir / f"report_{safe}_{stamp}.html"
    path.write_text(html, encoding="utf-8")
    return path
