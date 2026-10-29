"""Deployment progress view (item 9) — a step checklist instead of scrolling logs.

Steps light up ✓/✗ as the pipeline runs; the raw log is tucked away in a
collapsible box for when you need the detail.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.models import MonitoringHub, ServerProfile
from .widgets import CheckRow, h2, muted
from .workers import DeployOutcome, DeployWorker

# (row key, keywords that mark it running/done from the log stream)
_STEPS = [
    ("Connect", ["connecting", "connected"]),
    ("Generate config", ["generate"]),
    ("Backup", ["backup"]),
    ("Upload", ["upload"]),
    ("Validate", ["validate"]),
    ("Restart", ["restart"]),
    ("Verify listeners", ["verify-listeners"]),
    ("DNS", ["validating dns"]),
    ("Health checks", ["health"]),
    ("Monitoring", ["prometheus target", "registering prometheus", "verifying scrape"]),
]


class DeployProgressDialog(QDialog):
    def __init__(self, profile: ServerProfile, dry_run: bool, parent=None,
                 hub: MonitoringHub | None = None, hub_password: str | None = None,
                 dkim_keys: dict | None = None):
        super().__init__(parent)
        self.profile = profile
        self.dry_run = dry_run
        self.dkim_keys = dkim_keys
        self.outcome: DeployOutcome | None = None
        self.setWindowTitle(f"Deploy — {profile.name} ({'dry-run' if dry_run else 'LIVE'})")
        self.resize(600, 620)

        v = QVBoxLayout(self)
        v.addWidget(h2(f"{'Dry-run' if dry_run else 'Deploying'}: {profile.name}"))
        v.addWidget(muted(f"Target: {profile.ssh.username}@{profile.ssh.host}"))

        self.rows: dict[str, CheckRow] = {}
        for key, _ in _STEPS:
            # in dry-run only Generate + Validate run
            if dry_run and key not in ("Connect", "Generate config", "Validate", "DNS"):
                continue
            row = CheckRow(key)
            self.rows[key] = row
            v.addWidget(row)

        self.status = QLabel("")
        self.status.setStyleSheet("font-weight: 600;")
        v.addWidget(self.status)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(150)
        self.log.setVisible(False)
        self.toggle = QPushButton("Show raw log")
        self.toggle.setObjectName("Ghost")
        self.toggle.clicked.connect(self._toggle_log)
        v.addWidget(self.toggle)
        v.addWidget(self.log)

        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        v.addWidget(self.close_btn)

        # SSH password prompt (once), then start
        pw = None
        if profile.ssh.use_password:
            pw, ok = QInputDialog.getText(self, "SSH password",
                                          f"Password for {profile.ssh.host}:",
                                          echo=QLineEdit.Password)
            if not ok:
                self.reject()
                return
        self.worker = DeployWorker(profile, ssh_password=pw, dry_run=dry_run,
                                   hub=hub, hub_password=hub_password, dkim_keys=dkim_keys)
        self.worker.log.connect(self._on_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    # ------------------------------------------------------------------ #
    def _toggle_log(self):
        vis = not self.log.isVisible()
        self.log.setVisible(vis)
        self.toggle.setText("Hide raw log" if vis else "Show raw log")

    def _on_log(self, line: str):
        self.log.appendPlainText(line)
        low = line.lower()
        failed = low.startswith("[fail") or "error" in low or "rolling back" in low
        for key, keywords in _STEPS:
            if key not in self.rows:
                continue
            if any(k in low for k in keywords):
                if failed:
                    self.rows[key].set_state("fail", line.strip()[:60])
                else:
                    # a "[OK ] name — detail" line marks completion
                    state = "ok" if low.startswith("[ok") or key == "Connect" else "running"
                    self.rows[key].set_state(state)

    def _on_done(self, outcome: DeployOutcome):
        self.outcome = outcome
        # finalize any still-running rows using the structured result
        if outcome.deploy:
            done = {s.name for s in outcome.deploy.steps if s.ok}
            for key, keywords in _STEPS:
                if key in self.rows and self.rows[key].icon.text() in ("◐", "○"):
                    ok = any(any(kw in s.lower() for kw in keywords) for s in done)
                    self.rows[key].set_state("ok" if ok else "pending")
        for hc in outcome.health:
            for key in ("Health checks", "Monitoring"):
                if key in self.rows:
                    self.rows[key].set_state("ok" if hc.ok else "warn", hc.name)
        if outcome.error:
            self.status.setText(f"❌ Failed: {outcome.error}")
        elif outcome.deploy and outcome.deploy.success:
            self.status.setText("✓ Completed" + (" (dry-run)" if self.dry_run else ""))
        else:
            self.status.setText("❌ Failed (rolled back if applicable)")
        self.close_btn.setEnabled(True)
