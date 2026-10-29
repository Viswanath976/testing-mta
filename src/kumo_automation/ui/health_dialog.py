"""Standalone health-check view (reachable from the Server Overview)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ServerProfile
from .widgets import CheckRow, h2, muted


class HealthCheckDialog(QDialog):
    def __init__(self, profile: ServerProfile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle(f"Health Check — {profile.name}")
        self.resize(560, 480)
        v = QVBoxLayout(self)
        v.addWidget(h2("Health checks"))
        self.status = muted(f"Connecting to {profile.ssh.username}@{profile.ssh.host} …")
        v.addWidget(self.status)
        self.box = QVBoxLayout()
        wrap = QWidget()
        wrap.setLayout(self.box)
        v.addWidget(wrap)
        v.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        v.addWidget(self.close_btn)

        pw = None
        if profile.ssh.use_password:
            pw, ok = QInputDialog.getText(self, "SSH password",
                                          f"Password for {profile.ssh.host}:",
                                          echo=QLineEdit.Password)
            if not ok:
                self.reject()
                return
        from .workers import HealthWorker
        self.worker = HealthWorker(profile, ssh_password=pw)
        self.worker.log.connect(lambda m: self.status.setText(m))
        self.worker.done.connect(self._done)
        self.worker.start()

    def _done(self, checks):
        while self.box.count():
            item = self.box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        ok = sum(1 for c in checks if c.ok)
        for c in checks:
            row = CheckRow(c.name)
            row.set_state("ok" if c.ok else "fail", c.detail[:70])
            self.box.addWidget(row)
        self.status.setText(f"{ok}/{len(checks)} checks passed")
        self.close_btn.setEnabled(True)
