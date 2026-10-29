"""Main window — professional layout.

Left  : Dashboard button + Projects tree  (Project → Environment → Servers)
Center: stacked  [ Dashboard | Server detail ]

All heavy work lives in core/ + the dialogs; this file only wires them together.
"""
from __future__ import annotations

import getpass
import hashlib
import webbrowser
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Customer, DeploymentRecord, Environment, ServerProfile
from ..core.report import build_report_html, save_report
from ..services.app_settings import AppSettings
from ..services.project_store import ProjectStore
from . import theme
from .dashboard import Dashboard
from .ip_table import IPTable
from .toast import show_toast
from .widgets import ENV_EMOJI, STATUS_EMOJI, StatCard, h1, h2, muted

_ENV_ORDER = ["testing", "production"]
_ENV_LABEL = {"testing": "Testing", "production": "Production"}

# The guided lifecycle (drives the next-step banner + progress tracking)
TESTING_STEPS = [
    ("setup", "Server Setup (prerequisites + KumoMTA)"),
    ("previewed", "Preview Generated Configuration"),
    ("validated", "Pre-Deployment Validation"),
    ("dns", "Generate Draft DNS"),
    ("dry_run", "Dry Run Deployment"),
    ("health", "Server Health"),
    ("smtp", "SMTP Testing"),
    ("monitoring", "Monitoring Setup"),
    ("promote", "Promote to Production"),
]
PRODUCTION_STEPS = [
    ("prod_dns", "Generate Production DNS"),
    ("deployed", "Production Deployment"),
]

# short, professional label for the guided "next step" button
NEXT_ACTION = {
    "setup": "Start Setup",
    "previewed": "Preview Config",
    "validated": "Run Validation",
    "dns": "Generate DNS",
    "dry_run": "Start Dry Run",
    "health": "Run Health Check",
    "smtp": "Run SMTP Test",
    "monitoring": "Set Up Monitoring",
    "prod_dns": "Generate Production DNS",
    "deployed": "Deploy to Production",
}

# tooltips for the overview action buttons (UI polish)
TIPS = {
    "Server Setup": "Check prerequisites and install KumoMTA on the server",
    "Preview Generated Configuration": "View init.lua / sources / queues / ip_map / smtp-auth without SSH",
    "Pre-Deployment Validation": "Verify uniqueness, DNS syntax, SSH and KumoMTA — Ready or Blocked",
    "DNS Records": "Generate & verify A/MX/SPF/DKIM/DMARC/PTR; export CSV/Excel/TXT + DKIM keys",
    "Server Health": "CPU, memory, disk, SMTP AUTH, queue, ports, return-path",
    "SMTP Test": "Verify AUTH+STARTTLS and optionally send a test email",
    "Monitoring Setup": "Register with the hub and verify Prometheus/Loki/Grafana",
    "Download Center": "Export config, DNS, DKIM keys, reports and logs",
    "Server Settings": "Edit server, IPs, domains and credentials",
    "Deployment History": "Every deploy with operator, type, status and version",
}


class MainWindow(QMainWindow):
    def __init__(self, store: ProjectStore, reports_dir: Path, settings: AppSettings | None = None,
                 logs_dir: Path | None = None):
        super().__init__()
        self.store = store
        self.reports_dir = Path(reports_dir)
        self.logs_dir = Path(logs_dir) if logs_dir else self.reports_dir.parent / "logs"
        self.settings = settings or AppSettings(reports_dir)
        from ..core.dkim import KeyStore
        self.dkim_store = KeyStore(self.reports_dir.parent / "dkim")
        self.customers: dict[str, Customer] = {}
        self.setWindowTitle("KumoMTA Automation")
        self.resize(1180, 720)
        self._build_ui()
        self.reload()
        self._ensure_operator()

    # -- operator / settings ------------------------------------------------ #
    def _ensure_operator(self):
        if not self.settings.get_operator():
            name, ok = QInputDialog.getText(
                self, "Welcome", "Your name (recorded in deployment history):")
            if ok and name.strip():
                self.settings.set_operator(name.strip())

    def _open_settings(self):
        current = self.settings.get_operator()
        name, ok = QInputDialog.getText(self, "Settings", "Operator name:", text=current)
        if ok:
            self.settings.set_operator(name.strip())
            show_toast(self, "Settings saved")

    # -- layout ------------------------------------------------------------- #
    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # sidebar
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(280)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 16, 14, 16)
        sv.addWidget(h2("KumoMTA Automation"))
        self.b_dash = QPushButton("  🏠  Dashboard")
        self.b_dash.setObjectName("NavActive")
        self.b_dash.clicked.connect(self._show_dashboard)
        sv.addWidget(self.b_dash)
        b_logs = QPushButton("  📄  Logs Center")
        b_logs.setObjectName("Ghost")
        b_logs.clicked.connect(lambda: self.logs_center())
        sv.addWidget(b_logs)
        sv.addWidget(muted("PROJECTS"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        sv.addWidget(self.tree, 1)
        self.b_new = QPushButton("＋ New Testing Server")
        self.b_new.setObjectName("Primary")
        self.b_new.clicked.connect(self._new_server)
        sv.addWidget(self.b_new)
        row_b = QHBoxLayout()
        b_del = QPushButton("Delete Selected")
        b_del.clicked.connect(self._delete_selected)
        b_settings = QPushButton("⚙ Settings")
        b_settings.clicked.connect(self._open_settings)
        row_b.addWidget(b_del)
        row_b.addWidget(b_settings)
        sv.addLayout(row_b)
        root.addWidget(side)

        # main stack
        self.stack = QStackedWidget()
        self.dashboard = Dashboard()
        self.detail = _ServerDetail(self)
        self.stack.addWidget(self.dashboard)   # 0
        self.stack.addWidget(self.detail)      # 1
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

    # -- data --------------------------------------------------------------- #
    def reload(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self.customers.clear()
        for name in self.store.list_customers():
            try:
                cust = self.store.load(name)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Load error", f"{name}: {exc}")
                continue
            self.customers[cust.name] = cust
            proj = QTreeWidgetItem([f"📁  {cust.name}"])
            proj.setData(0, Qt.UserRole, ("project", cust.name))
            # group servers by environment
            by_env: dict[str, list[ServerProfile]] = {}
            for srv in cust.servers:
                by_env.setdefault(srv.environment.value, []).append(srv)
            for env in _ENV_ORDER:
                if env not in by_env:
                    continue
                env_node = QTreeWidgetItem([f"{ENV_EMOJI.get(env, '')} {_ENV_LABEL[env]}"])
                env_node.setData(0, Qt.UserRole, ("env", cust.name, env))
                for srv in by_env[env]:
                    leaf = QTreeWidgetItem([f"🖥  {srv.name}  ·  {len(srv.ips)} IP"])
                    leaf.setData(0, Qt.UserRole, ("server", cust.name, srv.name))
                    env_node.addChild(leaf)
                proj.addChild(env_node)
            self.tree.addTopLevelItem(proj)
            proj.setExpanded(True)
            for i in range(proj.childCount()):
                proj.child(i).setExpanded(True)
        if not self.customers:
            hint = QTreeWidgetItem(["No projects yet — click ＋ New Testing Server"])
            hint.setFlags(Qt.ItemIsEnabled)
            self.tree.addTopLevelItem(hint)
        self.tree.blockSignals(False)
        self.dashboard.refresh(list(self.customers.values()))

    def _current_server(self) -> tuple[Customer, ServerProfile] | None:
        item = self.tree.currentItem()
        if not item:
            return None
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "server":
            return None
        cust = self.customers.get(data[1])
        srv = cust.get_server(data[2]) if cust else None
        return (cust, srv) if (cust and srv) else None

    def _on_select(self, *_):
        pair = self._current_server()
        if pair:
            self.detail.show_server(*pair)
            self.stack.setCurrentIndex(1)
        # project/env nodes just keep whatever is shown

    def _show_dashboard(self):
        self.dashboard.refresh(list(self.customers.values()))
        self.stack.setCurrentIndex(0)

    # -- actions ------------------------------------------------------------ #
    def _selected_project(self) -> str | None:
        item = self.tree.currentItem()
        if not item:
            return None
        data = item.data(0, Qt.UserRole)
        return data[1] if data and data[0] in ("project", "env", "server") else None

    def _new_server(self):
        from .server_wizard import ServerWizard

        existing = list(self.customers)
        existing_servers = {name: [s.name for s in c.servers] for name, c in self.customers.items()}
        wiz = ServerWizard(existing, self, preset_project=self._selected_project(),
                           existing_servers=existing_servers)
        if wiz.exec() != QDialog.Accepted or not wiz.result_profile:
            return
        proj_name = wiz.result_project
        srv = wiz.result_profile
        cust = self.customers.get(proj_name) or Customer(name=proj_name)
        cust.servers = [s for s in cust.servers if s.name != srv.name] + [srv]
        self.store.save(cust)
        self.reload()
        # Phase B: land directly on the new server's Overview page
        self.select_server(proj_name, srv.name)
        show_toast(self, f"Server '{srv.name}' created in {proj_name}")

    def select_server(self, project: str, server: str) -> None:
        """Find the server leaf in the tree and select it → shows its Overview."""
        for i in range(self.tree.topLevelItemCount()):
            proj = self.tree.topLevelItem(i)
            for j in range(proj.childCount()):
                env = proj.child(j)
                for k in range(env.childCount()):
                    leaf = env.child(k)
                    data = leaf.data(0, Qt.UserRole)
                    if data == ("server", project, server):
                        self.tree.setCurrentItem(leaf)
                        return

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "server":
            return
        cust = self.customers.get(data[1])
        srv = cust.get_server(data[2]) if cust else None
        if not (cust and srv):
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        acts = {
            "Server Setup": lambda: self.setup(cust, srv),
            "Deploy": lambda: self.deploy(cust, srv, self.detail.chk_dry.isChecked()),
            "Pre-Deployment Validation": lambda: self.validate(cust, srv),
            "Preview Generated Configuration": lambda: self.preview_config(cust, srv),
            "Server Health": lambda: self.health_check(cust, srv),
            "SMTP Test": lambda: self.smtp_test(cust, srv),
            "DNS Records": lambda: self.dns(cust, srv),
            "Publish DNS (cPanel)": lambda: self.publish_dns(),
            "Monitoring Setup": lambda: self.monitoring(cust, srv),
            "Server Settings": lambda: self.edit_server(cust, srv),
            "Clone": lambda: self.clone_server(cust, srv),
            "Export server": lambda: self.export_server(cust, srv),
            "Download Center": lambda: self.download_center(srv),
            "Deployment History": lambda: self.history(cust, srv),
            "Delete": lambda: self.delete_server(cust, srv),
        }
        if srv.environment == Environment.testing:
            acts["Promote to Production"] = lambda: self.promote(cust, srv)
        for label, slot in acts.items():
            menu.addAction(label, slot)
            if label in ("Monitoring Setup", "Download Center"):
                menu.addSeparator()
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def clone_server(self, cust: Customer, srv: ServerProfile):
        base = f"{srv.name} (copy)"
        name, ok = QInputDialog.getText(self, "Clone server", "New server name:", text=base)
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(s.name == name for s in cust.servers):
            QMessageBox.warning(self, "Name in use", f"'{name}' already exists in this project.")
            return
        clone = srv.model_copy(deep=True)
        clone.name = name
        cust.servers.append(clone)
        self.store.save(cust)
        self.reload()
        self.select_server(cust.name, name)
        show_toast(self, f"Cloned to '{name}'")

    def export_server(self, cust: Customer, srv: ServerProfile):
        from PySide6.QtWidgets import QFileDialog
        import yaml

        path, _ = QFileDialog.getSaveFileName(self, "Export server", f"{srv.name}.yaml",
                                              "YAML (*.yaml)")
        if not path:
            return
        # export without secrets (same policy as on-disk storage)
        data = srv.model_dump(mode="json")
        data["shared_password"] = None
        for ip in data.get("ips", []):
            ip["password"] = None
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                              encoding="utf-8")
        show_toast(self, f"Exported {srv.name}")

    def _delete_selected(self):
        item = self.tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        if data[0] == "server":
            cust = self.customers.get(data[1])
            if cust and QMessageBox.question(
                self, "Delete server", f"Delete server '{data[2]}' from '{cust.name}'?"
            ) == QMessageBox.Yes:
                cust.servers = [s for s in cust.servers if s.name != data[2]]
                self.store.save(cust)
                self.reload()
                self._show_dashboard()
        elif data[0] == "project":
            if QMessageBox.question(
                self, "Delete project", f"Delete project '{data[1]}' and all its servers?"
            ) == QMessageBox.Yes:
                self.store.delete(data[1])
                self.reload()
                self._show_dashboard()

    # -- operations invoked from the detail panel --------------------------- #
    def edit_server(self, cust: Customer, srv: ServerProfile):
        from .server_edit import ServerEditDialog

        dlg = ServerEditDialog(srv, self)
        if dlg.exec() != QDialog.Accepted or not dlg.result_profile:
            return
        new = dlg.result_profile
        cust.servers = [s for s in cust.servers if s.name != dlg.original_name] + [new]
        self.store.save(cust)
        self.reload()
        self.select_server(cust.name, new.name)
        show_toast(self, "Changes saved")

    def dkim_keys(self, srv: ServerProfile) -> dict:
        """Stable per-domain DKIM private keys for this server (persisted)."""
        from ..core.dkim import keys_for
        return keys_for(srv, self.dkim_store, srv.name)

    def _apply_metrics_binding(self, cust: Customer, srv: ServerProfile) -> None:
        """Make the generated init.lua expose :8000 to the monitoring hub by setting
        the server's prometheus_hub from the configured hub. Without this the metrics
        port stays 127.0.0.1 and Prometheus can't scrape it."""
        if not srv.monitoring.enabled:
            return
        hub = self.settings.load_hub()
        if not hub:
            return
        source = hub.metrics_source()
        if srv.monitoring.prometheus_hub != source:
            srv.monitoring.prometheus_hub = source
            self.store.save(cust)

    def setup(self, cust: Customer, srv: ServerProfile):
        from .setup_dialog import ServerSetupDialog

        ServerSetupDialog(srv, self).exec()
        self.mark_progress(cust, srv, "setup")

    def preview_config(self, cust: Customer, srv: ServerProfile):
        from .config_diff import ConfigPreviewDialog

        self._apply_metrics_binding(cust, srv)
        ConfigPreviewDialog(srv, self).exec()
        self.mark_progress(cust, srv, "previewed")

    def monitoring(self, cust: Customer, srv: ServerProfile):
        from .monitoring_dialog import MonitoringDialog

        self._apply_metrics_binding(cust, srv)
        dlg = MonitoringDialog(srv, self.settings, self)
        dlg.exec()
        # only mark complete when every core monitoring component verified PASS
        if getattr(dlg, "all_passed", False):
            self.mark_progress(cust, srv, "monitoring")

    def dns(self, cust: Customer, srv: ServerProfile):
        from .dns_dialog import DnsDialog

        DnsDialog(srv, self, keys=self.dkim_keys(srv)).exec()
        self.mark_progress(cust, srv, "prod_dns" if srv.environment == Environment.production else "dns")

    def publish_dns(self):
        from .dns_publish_dialog import DnsPublishDialog

        DnsPublishDialog(self.settings, self).exec()

    def validate(self, cust: Customer, srv: ServerProfile):
        from .validation_dialog import ValidationDialog

        ValidationDialog(srv, hub_configured=self.settings.load_hub() is not None, parent=self).exec()
        self.mark_progress(cust, srv, "validated")

    def health_check(self, cust: Customer, srv: ServerProfile):
        from .health_dialog import HealthCheckDialog

        HealthCheckDialog(srv, self).exec()
        self.mark_progress(cust, srv, "health")

    def smtp_test(self, cust: Customer, srv: ServerProfile):
        from .smtp_test_dialog import SmtpTestDialog

        SmtpTestDialog(srv, self.logs_dir, self).exec()
        self.mark_progress(cust, srv, "smtp")

    def logs_center(self):
        from .logs_dialog import LogsDialog

        LogsDialog(self.logs_dir, self.reports_dir, self).exec()

    def download_center(self, srv: ServerProfile):
        from .download_dialog import DownloadDialog

        DownloadDialog(srv, self.reports_dir, self.logs_dir, self, keys=self.dkim_keys(srv)).exec()

    def history(self, cust: Customer, srv: ServerProfile):
        from .history_dialog import DeploymentHistoryDialog

        DeploymentHistoryDialog(cust, srv, self).exec()

    def delete_server(self, cust: Customer, srv: ServerProfile):
        if QMessageBox.question(
            self, "Delete server", f"Delete server '{srv.name}' from '{cust.name}'?"
        ) != QMessageBox.Yes:
            return
        cust.servers = [s for s in cust.servers if s.name != srv.name]
        self.store.save(cust)
        self.reload()
        self._show_dashboard()

    def server_status(self, cust: Customer, srv: ServerProfile) -> tuple[str, str]:
        """(label, state) from the latest deployment record for this server."""
        recs = [r for r in cust.history if r.server == srv.name]
        if not recs:
            return "Not deployed", "pending"
        last = max(recs, key=lambda r: r.timestamp)
        return ("Healthy", "ok") if last.success else ("Needs Attention", "warn")

    # -- workflow progress & guidance --------------------------------------- #
    def mark_progress(self, cust: Customer, srv: ServerProfile, step: str) -> None:
        if srv.progress.get(step):
            return
        srv.progress[step] = True
        self.store.save(cust)
        if self.detail.srv is srv:
            self.detail.show_server(cust, srv)
        self.dashboard.refresh(list(self.customers.values()))

    def next_step(self, srv: ServerProfile) -> tuple[str | None, str]:
        steps = TESTING_STEPS if srv.environment == Environment.testing else PRODUCTION_STEPS
        for key, label in steps:
            if not srv.progress.get(key):
                return key, label
        return None, "All steps complete ✓"

    def promote(self, cust: Customer, srv: ServerProfile) -> None:
        if srv.environment != Environment.testing:
            return
        if QMessageBox.question(
            self, "Promote to Production",
            f"Promote '{srv.name}' to Production?\n\nThe environment changes in place "
            "(the server is NOT duplicated), and production DNS + deployment become available."
        ) != QMessageBox.Yes:
            return
        srv.environment = Environment.production
        srv.progress["promote"] = True
        self.store.save(cust)
        self.reload()
        self.select_server(cust.name, srv.name)
        show_toast(self, f"{srv.name} promoted to Production")

    @staticmethod
    def config_hash(srv: ServerProfile) -> str:
        from ..core.generators import generate_all
        try:
            files = generate_all(srv)
            blob = "".join(f"{k}:{v}" for k, v in sorted(files.items()))
            return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
        except Exception:  # noqa: BLE001
            return ""

    def deploy(self, cust: Customer, srv: ServerProfile, dry_run: bool):
        from .deploy_progress import DeployProgressDialog

        if not dry_run and QMessageBox.question(
            self, "Deploy to Server",
            f"Deploy the configuration to {srv.ssh.host} now?\n\n"
            "The current config is backed up first and the server is restarted. "
            "If anything fails, it rolls back automatically."
        ) != QMessageBox.Yes:
            return
        self._apply_metrics_binding(cust, srv)
        hub = self.settings.load_hub() if srv.monitoring.enabled else None
        hub_pw = self.settings.hub_password() if hub else None
        dlg = DeployProgressDialog(srv, dry_run, self, hub=hub, hub_password=hub_pw,
                                   dkim_keys=self.dkim_keys(srv))
        dlg.exec()
        if dlg.outcome and dlg.outcome.deploy:
            dep = dlg.outcome.deploy
            html = build_report_html(srv, dep, dlg.outcome.dns, dlg.outcome.health)
            report = save_report(html, self.reports_dir, srv.name)

            # record every deploy (incl. dry-run) with audit fields
            if dep.rolled_back:
                status = "Rolled Back"
            elif dep.success:
                status = "Success"
            else:
                status = "Failed"
            deploy_type = "Dry Run" if dep.dry_run else (
                "Production" if srv.environment == Environment.production else "Testing")
            cust.history.append(DeploymentRecord(
                server=srv.name, environment=srv.environment,
                timestamp=datetime.now(), success=dep.success,
                backup_path=dep.backup_path, report_path=str(report),
                operator_name=self.settings.get_operator(),
                windows_user=getpass.getuser(),
                deploy_type=deploy_type, status_label=status,
                config_hash=self.config_hash(srv)))
            # progress markers
            if dep.success and dep.dry_run:
                srv.progress["dry_run"] = True
            elif dep.success and not dep.dry_run:
                srv.progress["deployed"] = True
            self.store.save(cust)
            self.reload()
            self.select_server(cust.name, srv.name)  # refresh Overview status + Dashboard
            if dep.success:
                show_toast(self, f"{deploy_type} deploy: {status}")
            if QMessageBox.question(
                self, "Report saved", f"Report saved to:\n{report}\n\nOpen it now?"
            ) == QMessageBox.Yes:
                webbrowser.open(report.as_uri())


class _ServerDetail(QWidget):
    """Server Overview — the daily working screen: identity + status + every action."""

    def __init__(self, win: MainWindow):
        super().__init__()
        self.win = win
        self.cust: Customer | None = None
        self.srv: ServerProfile | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)

        head = QHBoxLayout()
        self.title = h1("Server")
        head.addWidget(self.title)
        head.addStretch(1)
        self.breadcrumb = muted("")
        head.addWidget(self.breadcrumb)
        v.addLayout(head)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = {k: StatCard(k) for k in ["Environment", "IPs", "Domains", "SMTP Users", "Status"]}
        self.cards["SSH"] = StatCard("SSH")
        for k in ["Environment", "SSH", "IPs", "Domains", "SMTP Users", "Status"]:
            cards.addWidget(self.cards[k])
        v.addLayout(cards)

        self.last_deploy = muted("")
        v.addWidget(self.last_deploy)

        # guided next-step banner
        banner = QFrame()
        banner.setObjectName("Card")
        bl = QHBoxLayout(banner)
        bl.setContentsMargins(14, 10, 14, 10)
        self.next_label = QLabel("")
        self.next_label.setStyleSheet("font-weight: 600;")
        self.b_next = QPushButton("Do it →")
        self.b_next.setObjectName("Primary")
        self.b_next.clicked.connect(self._do_next)
        bl.addWidget(self.next_label, 1)
        bl.addWidget(self.b_next)
        v.addWidget(banner)

        # action row 1 (renamed for clarity)
        row1 = QHBoxLayout()
        for text, slot in [
            ("Server Setup", self._setup),
            ("Preview Generated Configuration", self._preview),
            ("Pre-Deployment Validation", self._validate),
            ("DNS Records", self._dns), ("Server Health", self._health),
            ("SMTP Test", self._smtp), ("Monitoring Setup", self._monitoring),
        ]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setToolTip(TIPS.get(text, ""))
            row1.addWidget(b)
        row1.addStretch(1)
        v.addLayout(row1)

        row1b = QHBoxLayout()
        for text, slot in [
            ("Deployment History", self._history), ("Server Settings", self._edit),
            ("Download Center", self._download),
        ]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setToolTip(TIPS.get(text, ""))
            row1b.addWidget(b)
        row1b.addStretch(1)
        # Production readiness score
        self.readiness_lbl = QLabel("")
        self.readiness_lbl.setStyleSheet("font-weight: 700;")
        row1b.addWidget(self.readiness_lbl)
        v.addLayout(row1b)

        # action row 2 — promote + deploy line
        row2 = QHBoxLayout()
        self.b_delete = QPushButton("Delete Server")
        self.b_delete.clicked.connect(self._delete)
        row2.addWidget(self.b_delete)
        self.b_promote = QPushButton("⭐ Promote to Production")
        self.b_promote.clicked.connect(self._promote)
        row2.addWidget(self.b_promote)
        row2.addStretch(1)
        self.chk_dry = QCheckBox("Dry-run (preview only, no changes)")
        self.chk_dry.setChecked(True)
        self.chk_dry.toggled.connect(self._sync_deploy_label)
        row2.addWidget(self.chk_dry)
        self.b_deploy = QPushButton("Run Dry Run  →")
        self.b_deploy.setObjectName("Primary")
        self.b_deploy.clicked.connect(self._deploy)
        row2.addWidget(self.b_deploy)
        v.addLayout(row2)

        v.addWidget(h2("IP addresses  —  double-click a row for details"))
        self.ips = IPTable()
        self.ips.entryActivated.connect(self._ip_detail)
        v.addWidget(self.ips, 1)

    def show_server(self, cust: Customer, srv: ServerProfile):
        self.cust, self.srv = cust, srv
        env = srv.environment.value
        self.title.setText(srv.name)
        self.breadcrumb.setText(f"{cust.name}  ›  {ENV_EMOJI.get(env, '')} {_ENV_LABEL.get(env, env)}")
        self.cards["Environment"].set_value(f"{ENV_EMOJI.get(env, '')} {_ENV_LABEL.get(env, env)}")
        self.cards["SSH"].value.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.cards["SSH"].set_value(srv.ssh.host or "—")
        self.cards["IPs"].set_value(len(srv.ips))
        self.cards["Domains"].set_value(len(srv.unique_domains()))
        self.cards["SMTP Users"].set_value(len(srv.ips))
        label, state = self.win.server_status(cust, srv)
        color = {"ok": theme.OK, "warn": theme.WARN, "pending": theme.MUTED}.get(state, theme.MUTED)
        self.cards["Status"].value.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {color};")
        self.cards["Status"].set_value(f"{STATUS_EMOJI.get(state, '')} {label}")
        recs = [r for r in cust.history if r.server == srv.name]
        if recs:
            last = max(recs, key=lambda r: r.timestamp)
            self.last_deploy.setText(
                f"Last deployment: {last.timestamp.strftime('%Y-%m-%d %H:%M')} — "
                f"{last.deploy_type or last.environment.value} "
                f"{last.status_label or ('success' if last.success else 'failed')}")
        else:
            self.last_deploy.setText("Last deployment: never")

        # guided next step + promote visibility
        _key, label = self.win.next_step(srv)
        done = _key is None
        self.next_label.setText(("✓ " if done else "Next step:  ") + label)
        self.b_next.setText((NEXT_ACTION.get(_key, "Continue")) + "  →")
        self.b_next.setVisible(not done)
        self.b_promote.setVisible(srv.environment == Environment.testing)

        # production readiness score
        from ..core.readiness import readiness as _readiness
        rd = _readiness(srv)
        missing = [n for n, ok in rd["items"] if not ok]
        color = theme.OK if rd["score"] >= 100 else (theme.WARN if rd["score"] >= 50 else theme.BAD)
        tail = "  ·  Production Ready" if rd["ready"] else ("  ·  needs: " + ", ".join(missing[:4]))
        self.readiness_lbl.setStyleSheet(f"font-weight:700; color:{color};")
        self.readiness_lbl.setText(f"Readiness {rd['score']}%{tail}")

        self.ips.set_entries(srv.ips)
        self._sync_deploy_label()

    def _sync_deploy_label(self):
        self.b_deploy.setText("Run Dry Run  →" if self.chk_dry.isChecked()
                              else "Deploy to Server  →")

    def _do_next(self):
        if not (self.cust and self.srv):
            return
        key, _ = self.win.next_step(self.srv)
        c, s = self.cust, self.srv
        dispatch = {
            "setup": lambda: self.win.setup(c, s),
            "previewed": lambda: self.win.preview_config(c, s),
            "validated": lambda: self.win.validate(c, s),
            "dns": lambda: self.win.dns(c, s),
            "dry_run": lambda: self.win.deploy(c, s, True),
            "health": lambda: self.win.health_check(c, s),
            "smtp": lambda: self.win.smtp_test(c, s),
            "monitoring": lambda: self.win.monitoring(c, s),
            "promote": lambda: self.win.promote(c, s),
            "prod_dns": lambda: self.win.dns(c, s),
            "deployed": lambda: self.win.deploy(c, s, False),
        }
        fn = dispatch.get(key)
        if fn:
            fn()

    def _ip_detail(self, entry):
        if self.srv:
            from .ip_detail_dialog import IPDetailDialog
            IPDetailDialog(self.srv, entry, self).exec()

    def _dns(self):
        if self.cust and self.srv:
            self.win.dns(self.cust, self.srv)

    def _edit(self):
        if self.cust and self.srv:
            self.win.edit_server(self.cust, self.srv)

    def _setup(self):
        if self.cust and self.srv:
            self.win.setup(self.cust, self.srv)

    def _preview(self):
        if self.cust and self.srv:
            self.win.preview_config(self.cust, self.srv)

    def _monitoring(self):
        if self.cust and self.srv:
            self.win.monitoring(self.cust, self.srv)

    def _validate(self):
        if self.cust and self.srv:
            self.win.validate(self.cust, self.srv)

    def _health(self):
        if self.cust and self.srv:
            self.win.health_check(self.cust, self.srv)

    def _smtp(self):
        if self.cust and self.srv:
            self.win.smtp_test(self.cust, self.srv)

    def _download(self):
        if self.srv:
            self.win.download_center(self.srv)

    def _promote(self):
        if self.cust and self.srv:
            self.win.promote(self.cust, self.srv)

    def _history(self):
        if self.cust and self.srv:
            self.win.history(self.cust, self.srv)

    def _delete(self):
        if self.cust and self.srv:
            self.win.delete_server(self.cust, self.srv)

    def _deploy(self):
        if self.cust and self.srv:
            self.win.deploy(self.cust, self.srv, self.chk_dry.isChecked())
