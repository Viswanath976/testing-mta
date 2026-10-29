"""Monitoring — configure the hub, install the production Loki forwarder, and
verify the whole pipeline (Forwarder / Prometheus / Loki / Grafana) with PASS/FAIL.

The Monitoring step is only 'complete' when the core components pass.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core import forwarder as fwd_mod
from ..core import monitoring as mon_mod
from ..core.models import MonitoringHub, ServerProfile, SSHTarget
from ..services.app_settings import AppSettings
from . import theme
from .widgets import CheckRow, h2, muted
from .workers import MonitorFullWorker

# checks that must PASS for the monitoring step to be considered complete
_CORE = {"Forwarder installed", "Forwarder service", "Prometheus target", "Loki logs"}


class MonitoringDialog(QDialog):
    def __init__(self, profile: ServerProfile, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.settings = settings
        self.worker: MonitorFullWorker | None = None
        self.all_passed = False
        self._srv_pw = None
        self.setWindowTitle(f"Monitoring — {profile.name}")
        self.resize(720, 680)

        v = QVBoxLayout(self)
        hub_box = QGroupBox("Central monitoring hub (shared)")
        f = QFormLayout(hub_box)
        self.host = QLineEdit(); self.user = QLineEdit("root")
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(22)
        self.use_pw = QLineEdit(); self.use_pw.setEchoMode(QLineEdit.Password)
        self.use_pw.setPlaceholderText("(blank = key / saved password)")
        self.file_sd = QLineEdit("/opt/monitoring/prometheus/targets")
        self.prom_url = QLineEdit("http://127.0.0.1:9090")
        self.loki_url = QLineEdit("http://127.0.0.1:3100/loki/api/v1/push")
        self.scrape_source = QLineEdit()
        self.scrape_source.setPlaceholderText("(optional) IP the server's :8000 must trust")
        self.reload = QComboBox(); self.reload.addItems(["http", "docker", "sighup"])
        f.addRow("Hub SSH host", self.host)
        f.addRow("Hub SSH user", self.user)
        f.addRow("Hub SSH port", self.port)
        f.addRow("Hub SSH password", self.use_pw)
        f.addRow("Prometheus file_sd dir", self.file_sd)
        f.addRow("Prometheus URL (on hub)", self.prom_url)
        f.addRow("Loki push URL", self.loki_url)
        f.addRow("Metrics scrape source IP", self.scrape_source)
        f.addRow("Prometheus reload", self.reload)
        v.addWidget(hub_box)

        self.fwd_lbl = muted("")
        v.addWidget(self.fwd_lbl)

        row = QHBoxLayout()
        b_save = QPushButton("Save hub"); b_save.clicked.connect(self._save_hub)
        b_import = QPushButton("Import forwarder source…"); b_import.clicked.connect(self._import_forwarder)
        b_install = QPushButton("Install Monitoring"); b_install.setObjectName("Primary")
        b_install.clicked.connect(self._install)
        b_verify = QPushButton("Verify Monitoring"); b_verify.clicked.connect(self._verify)
        row.addWidget(b_save)
        row.addWidget(b_import)
        row.addStretch(1)
        row.addWidget(b_verify)
        row.addWidget(b_install)
        v.addLayout(row)

        self.verdict = QLabel("")
        self.verdict.setStyleSheet("font-weight: 700;")
        v.addWidget(self.verdict)
        self.checks_box = QVBoxLayout()
        cw = QWidget(); cw.setLayout(self.checks_box)
        v.addWidget(cw)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(150)
        v.addWidget(self.log)
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        v.addWidget(close)

        self._load_existing()
        self._refresh_fwd_label()

    # ------------------------------------------------------------------ #
    def _refresh_fwd_label(self):
        if fwd_mod.available(self.settings.forwarder_dir()):
            files = [p.name for p in self.settings.forwarder_dir().iterdir() if p.is_file()]
            self.fwd_lbl.setText(f"Forwarder source staged: {', '.join(files)}")
        else:
            self.fwd_lbl.setText("⚠ Production forwarder source not staged — "
                                 "click 'Import forwarder source…' before Install Monitoring.")

    def _load_existing(self):
        hub = self.settings.load_hub()
        if not hub:
            return
        self.host.setText(hub.ssh.host); self.user.setText(hub.ssh.username)
        self.port.setValue(hub.ssh.port); self.file_sd.setText(hub.file_sd_dir)
        self.prom_url.setText(hub.prometheus_url); self.loki_url.setText(hub.loki_push_url)
        self.scrape_source.setText(hub.scrape_source); self.reload.setCurrentText(hub.reload_method)

    def _build_hub(self) -> MonitoringHub | None:
        if not self.host.text().strip():
            QMessageBox.warning(self, "Missing", "Hub SSH host is required.")
            return None
        return MonitoringHub(
            ssh=SSHTarget(host=self.host.text().strip(), port=self.port.value(),
                          username=self.user.text().strip() or "root", use_password=True),
            file_sd_dir=self.file_sd.text().strip(), prometheus_url=self.prom_url.text().strip(),
            loki_push_url=self.loki_url.text().strip(), scrape_source=self.scrape_source.text().strip(),
            reload_method=self.reload.currentText())

    def _save_hub(self, silent=False):
        hub = self._build_hub()
        if not hub:
            return None
        self.settings.save_hub(hub, ssh_password=(self.use_pw.text() or None))
        if not silent:
            QMessageBox.information(self, "Saved", "Monitoring hub settings saved.")
        return hub

    def _hub_password(self):
        if self.use_pw.text():
            return self.use_pw.text()
        saved = self.settings.hub_password()
        if saved:
            return saved
        pw, ok = QInputDialog.getText(self, "Hub SSH password",
                                      f"Password for {self.host.text().strip()}:",
                                      echo=QLineEdit.Password)
        return pw if ok else None

    def _server_password(self):
        if self.profile.ssh.use_password and self._srv_pw is None:
            pw, ok = QInputDialog.getText(self, "Server SSH password",
                                          f"Password for {self.profile.ssh.host}:",
                                          echo=QLineEdit.Password)
            self._srv_pw = pw if ok else ""
        return self._srv_pw

    def _import_forwarder(self):
        d = QFileDialog.getExistingDirectory(self, "Select the production forwarder source folder")
        if not d:
            return
        dest = self.settings.forwarder_dir()
        for p in Path(d).iterdir():
            if p.is_file():
                shutil.copyfile(p, dest / p.name)
        QMessageBox.information(self, "Imported",
                               f"Staged forwarder source into:\n{dest}")
        self._refresh_fwd_label()

    def _clear_checks(self):
        while self.checks_box.count():
            it = self.checks_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _run(self, action: str):
        hub = self._save_hub(silent=True)
        if not hub:
            return
        self._clear_checks()
        self.verdict.setText("Working…")
        self.worker = MonitorFullWorker(self.profile, hub, self._server_password(),
                                        self._hub_password(), action=action,
                                        forwarder_dir=self.settings.forwarder_dir())
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _install(self):
        if not fwd_mod.available(self.settings.forwarder_dir()):
            QMessageBox.warning(self, "Forwarder source needed",
                                "Import the production forwarder source first "
                                "(Import forwarder source…).")
            return
        self._run("install")

    def _verify(self):
        self._run("verify")

    def _done(self, checks):
        self._clear_checks()
        for c in checks:
            r = CheckRow(c.name)
            r.set_state(getattr(c, "state", "pass" if c.ok else "fail"), c.detail)
            self.checks_box.addWidget(r)
            mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}.get(getattr(c, "state", None),
                                                                        "OK" if c.ok else "FAIL")
            self.log.appendPlainText(f"[{mark}] {c.name} — {c.detail}")
        core = [c for c in checks if c.name in _CORE]
        self.all_passed = bool(core) and all(c.state == "pass" for c in core)
        if self.all_passed:
            self.verdict.setText("✓ Monitoring verified — all components PASS")
            self.verdict.setStyleSheet(f"font-weight:700; color:{theme.OK};")
        else:
            fails = [c.name for c in checks if getattr(c, "state", None) == "fail"]
            self.verdict.setText("✗ Monitoring incomplete — " + (", ".join(fails) or "not all core checks passed"))
            self.verdict.setStyleSheet(f"font-weight:700; color:{theme.BAD};")
        self.log.appendPlainText("Done.")
