"""Pre-Deployment Validation — structural checks + live SSH/KumoMTA checks,
with a clear 'Ready for deployment' / 'Deployment blocked' verdict.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import predeploy
from ..core.models import ServerProfile
from . import theme
from .widgets import CheckRow, h2, muted
from .workers import SetupState, SetupWorker


class ValidationDialog(QDialog):
    def __init__(self, profile: ServerProfile, hub_configured: bool = False, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle(f"Pre-Deployment Validation — {profile.name}")
        self.resize(600, 620)
        self._pw = None
        self.structural: list = predeploy.structural_checks(profile)
        self.ssh_checks: list = []

        v = QVBoxLayout(self)
        self.verdict = QLabel("Checking…")
        self.verdict.setStyleSheet("font-size: 16px; font-weight: 700;")
        v.addWidget(self.verdict)
        v.addWidget(muted("Structural checks run instantly; SSH + KumoMTA checks connect to the server."))

        v.addWidget(h2("Structural"))
        self.struct_box = QVBoxLayout()
        sw = QWidget(); sw.setLayout(self.struct_box)
        v.addWidget(sw)

        v.addWidget(h2("Server (SSH)"))
        self.ssh_box = QVBoxLayout()
        sshw = QWidget(); sshw.setLayout(self.ssh_box)
        v.addWidget(sshw)
        self.ssh_status = muted("Connecting to check SSH + KumoMTA…")
        v.addWidget(self.ssh_status)
        v.addStretch(1)

        row = QVBoxLayout()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        v.addWidget(self.close_btn)

        self._render_structural()
        self._run_ssh()

    # ------------------------------------------------------------------ #
    def _render_structural(self):
        for c in self.structural:
            r = CheckRow(c.name)
            r.set_state(c.state, c.detail)
            self.struct_box.addWidget(r)
        self._update_verdict()

    def _password(self):
        if self.profile.ssh.use_password and self._pw is None:
            pw, ok = QInputDialog.getText(self, "SSH password",
                                          f"Password for {self.profile.ssh.host}:",
                                          echo=QLineEdit.Password)
            self._pw = pw if ok else ""
        return self._pw

    def _run_ssh(self):
        self.worker = SetupWorker(self.profile, self._password(), action="check")
        self.worker.done.connect(self._ssh_done)
        self.worker.start()

    def _ssh_done(self, state: SetupState):
        from ..core.models import HealthCheck
        if state.error:
            self.ssh_status.setText(f"Could not connect: {state.error}")
            self.ssh_checks = [HealthCheck(name="SSH connectivity", ok=False, status="fail",
                                           detail=state.error)]
        else:
            self.ssh_checks = [HealthCheck(name="SSH connectivity", ok=True, status="pass",
                                           detail="connected")]
            info = state.install
            self.ssh_checks.append(HealthCheck(
                name="KumoMTA installed", ok=info.installed,
                status="pass" if info.installed else "fail",
                detail=(f"v{info.version}" if info.version else "installed") if info.installed
                else "not installed — run Server Setup"))
            self.ssh_status.setText("SSH + KumoMTA checks complete.")
        for c in self.ssh_checks:
            r = CheckRow(c.name)
            r.set_state(c.state, c.detail)
            self.ssh_box.addWidget(r)
        self._update_verdict()

    def _update_verdict(self):
        all_checks = self.structural + self.ssh_checks
        ready, reasons = predeploy.verdict(all_checks)
        if not self.ssh_checks:
            self.verdict.setText("Checking server…")
            self.verdict.setStyleSheet(f"font-size:16px;font-weight:700;color:{theme.MUTED};")
        elif ready:
            self.verdict.setText("✓ Ready for deployment")
            self.verdict.setStyleSheet(f"font-size:16px;font-weight:700;color:{theme.OK};")
        else:
            self.verdict.setText(f"✗ Deployment blocked — {len(reasons)} issue(s)")
            self.verdict.setStyleSheet(f"font-size:16px;font-weight:700;color:{theme.BAD};")

    def is_ready(self) -> bool:
        ready, _ = predeploy.verdict(self.structural + self.ssh_checks)
        return ready
