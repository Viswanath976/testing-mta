"""Server Setup — make a brand-new Ubuntu VPS ready for KumoMTA.

Runs prerequisite checks (PASS/WARN/FAIL), detects whether KumoMTA is installed
and its version, and can install it automatically. The install script is shown
for review and requires an explicit confirmation before it runs.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import installer as installer_mod
from ..core.models import ServerProfile
from .widgets import CheckRow, h2, muted
from .workers import SetupState, SetupWorker


class ServerSetupDialog(QDialog):
    def __init__(self, profile: ServerProfile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.worker: SetupWorker | None = None
        self.setWindowTitle(f"Server Setup — {profile.name}")
        self.resize(640, 640)
        v = QVBoxLayout(self)
        v.addWidget(h2("Server prerequisites"))
        self.status = muted("Connecting …")
        v.addWidget(self.status)
        self.checks_box = QVBoxLayout()
        wrap = QWidget(); wrap.setLayout(self.checks_box)
        v.addWidget(wrap)

        v.addWidget(h2("KumoMTA"))
        self.kumo_lbl = QLabel("—")
        v.addWidget(self.kumo_lbl)

        row = QHBoxLayout()
        self.b_recheck = QPushButton("Re-check")
        self.b_recheck.clicked.connect(self._check)
        self.b_script = QPushButton("View install script")
        self.b_script.clicked.connect(self._show_script)
        self.b_install = QPushButton("Install KumoMTA")
        self.b_install.setObjectName("Primary")
        self.b_install.setEnabled(False)
        self.b_install.clicked.connect(self._install)
        row.addWidget(self.b_recheck)
        row.addWidget(self.b_script)
        row.addStretch(1)
        row.addWidget(self.b_install)
        v.addLayout(row)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(180)
        v.addWidget(self.log)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)

        self._pw = None
        self._check()

    # ------------------------------------------------------------------ #
    def _password(self):
        if self.profile.ssh.use_password and self._pw is None:
            pw, ok = QInputDialog.getText(self, "SSH password",
                                          f"Password for {self.profile.ssh.host}:",
                                          echo=QLineEdit.Password)
            self._pw = pw if ok else ""
        return self._pw

    def _clear_checks(self):
        while self.checks_box.count():
            it = self.checks_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _check(self):
        self.status.setText("Running prerequisite checks …")
        self.b_install.setEnabled(False)
        self.worker = SetupWorker(self.profile, self._password(), action="check")
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.done.connect(self._checked)
        self.worker.start()

    def _checked(self, state: SetupState):
        self._clear_checks()
        if state.error:
            self.status.setText(f"Could not connect: {state.error}")
            return
        fails = sum(1 for c in state.prereqs if c.state == "fail")
        warns = sum(1 for c in state.prereqs if c.state == "warn")
        passed = sum(1 for c in state.prereqs if c.state == "pass")
        for c in state.prereqs:
            row = CheckRow(c.name)
            row.set_state(c.state, c.detail)
            self.checks_box.addWidget(row)
        if fails:
            verdict = f"⚠ {fails} to fix before deploying"
        elif warns:
            verdict = f"⚠ {warns} warning(s) to review"
        else:
            verdict = "✓ Ready to deploy"
        self.status.setText(f"{passed} Passed · {fails} Failed · {warns} Warnings     {verdict}")

        info = state.install
        if info.installed:
            # show the clean version only (hide the git-hash suffix from clients)
            ver = (info.version or "?").split("-")[0]
            if info.upgrade_available:
                self.kumo_lbl.setText(
                    f"KumoMTA — Installed ✓      Installed version: {ver}"
                    f"      Latest supported: {info.latest}      ⬆ Upgrade available")
                self.b_install.setText("Upgrade KumoMTA")
            else:
                self.kumo_lbl.setText(
                    f"KumoMTA — Installed ✓      Version: {ver}      (up to date)")
                self.b_install.setText("Reinstall KumoMTA")
        else:
            self.kumo_lbl.setText("KumoMTA — Not installed.  Click 'Install KumoMTA' to set it up.")
        self.b_install.setEnabled(True)

    def _show_script(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("KumoMTA install script")
        dlg.resize(680, 480)
        lv = QVBoxLayout(dlg)
        lv.addWidget(muted("This exact script will run on the server (with sudo). Review before installing."))
        box = QPlainTextEdit(installer_mod.install_script())
        box.setReadOnly(True)
        lv.addWidget(box)
        b = QPushButton("Close"); b.clicked.connect(dlg.accept)
        lv.addWidget(b)
        dlg.exec()

    def _install(self):
        if QMessageBox.question(
            self, "Install KumoMTA",
            f"Run the install script on {self.profile.ssh.host} (uses sudo)?\n\n"
            "Click 'View install script' first if you want to review it."
        ) != QMessageBox.Yes:
            return
        self.status.setText("Installing KumoMTA … (this can take a few minutes)")
        self.b_install.setEnabled(False)
        self.worker = SetupWorker(self.profile, self._password(), action="install")
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.done.connect(self._installed)
        self.worker.start()

    def _installed(self, ok: bool):
        self.b_install.setEnabled(True)
        if ok:
            self.status.setText("✓ KumoMTA installed. Re-checking …")
            self._check()
        else:
            self.status.setText("✗ Installation failed — see the log above.")
            QMessageBox.warning(self, "Install failed",
                                "KumoMTA installation did not complete. Review the log for details.")
