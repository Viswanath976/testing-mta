"""Dashboard home — an at-a-glance overview across every project/server."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Customer
from . import theme
from .widgets import CheckRow, StatCard, h1, h2, muted


class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.addWidget(h1("Dashboard"))
        v.addWidget(muted("Overview of all projects and servers"))

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = {
            k: StatCard(k, "0", c) for k, c in [
                ("Projects", theme.ACCENT), ("Servers", theme.ACCENT),
                ("Production", theme.ACCENT), ("Testing", theme.ACCENT),
                ("Total IPs", theme.ACCENT), ("Domains", theme.ACCENT),
                ("Healthy", theme.OK), ("Needs Attention", theme.WARN),
                ("Pending Validation", theme.WARN), ("Pending DNS", theme.WARN),
                ("Pending Monitoring", theme.WARN), ("Production Ready", theme.OK),
            ]
        }
        order = ["Projects", "Servers", "Production", "Testing",
                 "Total IPs", "Domains", "Healthy", "Needs Attention",
                 "Pending Validation", "Pending DNS", "Pending Monitoring", "Production Ready"]
        for i, key in enumerate(order):
            grid.addWidget(self.cards[key], i // 4, i % 4)
        v.addLayout(grid)
        self.last_deploy = muted("Last deployment: —")
        v.addWidget(self.last_deploy)

        v.addSpacing(10)
        v.addWidget(h2("Recent Deployments"))
        self.recent = QVBoxLayout()
        wrap = QWidget()
        wrap.setLayout(self.recent)
        v.addWidget(wrap)
        v.addStretch(1)

    def refresh(self, customers: list[Customer]) -> None:
        servers = [(c, s) for c in customers for s in c.servers]
        prod = sum(1 for _c, s in servers if s.environment.value == "production")
        test = sum(1 for _c, s in servers if s.environment.value == "testing")
        total_ips = sum(len(s.ips) for _c, s in servers)
        domains = {d for _c, s in servers for d in s.unique_domains() if d}

        # latest deployment outcome per (customer, server)
        healthy = attention = 0
        for c, s in servers:
            recs = [r for r in c.history if r.server == s.name]
            if not recs:
                continue
            last = max(recs, key=lambda r: r.timestamp)
            if last.success:
                healthy += 1
            else:
                attention += 1

        # workflow-progress derived metrics
        pending_val = sum(1 for _c, s in servers if not s.progress.get("validated"))
        pending_dns = sum(1 for _c, s in servers
                          if not s.progress.get("prod_dns" if s.environment.value == "production" else "dns"))
        pending_mon = sum(1 for _c, s in servers if not s.progress.get("monitoring"))
        prod_ready = sum(1 for _c, s in servers if s.environment.value == "testing"
                         and all(s.progress.get(k) for k in
                                 ("setup", "previewed", "validated", "dns", "dry_run",
                                  "health", "smtp", "monitoring")))

        self.cards["Projects"].set_value(len(customers))
        self.cards["Servers"].set_value(len(servers))
        self.cards["Production"].set_value(prod)
        self.cards["Testing"].set_value(test)
        self.cards["Total IPs"].set_value(f"{total_ips:,}")
        self.cards["Domains"].set_value(len(domains))
        self.cards["Healthy"].set_value(healthy)
        self.cards["Needs Attention"].set_value(attention)
        self.cards["Pending Validation"].set_value(pending_val)
        self.cards["Pending DNS"].set_value(pending_dns)
        self.cards["Pending Monitoring"].set_value(pending_mon)
        self.cards["Production Ready"].set_value(prod_ready)

        all_recs_for_last = [r for c in customers for r in c.history]
        if all_recs_for_last:
            lr = max(all_recs_for_last, key=lambda r: r.timestamp)
            self.last_deploy.setText(
                f"Last deployment: {lr.server} · {lr.deploy_type or lr.environment.value} · "
                f"{lr.status_label or ('success' if lr.success else 'failed')} · "
                f"{lr.timestamp.strftime('%Y-%m-%d %H:%M')}")
        else:
            self.last_deploy.setText("Last deployment: —")

        # recent deployments (across all projects)
        while self.recent.count():
            item = self.recent.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        all_recs = [(c, r) for c in customers for r in c.history]
        all_recs.sort(key=lambda cr: cr[1].timestamp, reverse=True)
        if not all_recs:
            self.recent.addWidget(muted("No deployments yet."))
            return
        for c, r in all_recs[:8]:
            when = r.timestamp.strftime("%Y-%m-%d %H:%M")
            row = CheckRow(f"{r.server}  ·  {c.name}  ·  {r.environment.value}")
            row.set_state("ok" if r.success else "warn", when)
            self.recent.addWidget(row)
