"""Per-IP details (double-click an IP in the Overview table)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.models import IPEntry, ServerProfile
from .widgets import h2, muted


class IPDetailDialog(QDialog):
    def __init__(self, profile: ServerProfile, ip: IPEntry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"IP details — {ip.ip}")
        self.resize(460, 420)
        v = QVBoxLayout(self)
        v.addWidget(h2(ip.ip))

        form = QFormLayout()
        form.addRow("Hostname", QLabel(ip.hostname))
        form.addRow("SMTP username", QLabel(ip.username))
        form.addRow("Domain", QLabel(ip.domain))
        form.addRow("Pool", QLabel(ip.pool))
        form.addRow("Tenant", QLabel(ip.tenant))

        # password: masked with a reveal toggle (never shown by default)
        pw = profile.effective_password(ip)
        self.pw_field = QLineEdit(pw)
        self.pw_field.setReadOnly(True)
        self.pw_field.setEchoMode(QLineEdit.Password)
        reveal = QPushButton("Reveal")
        reveal.setCheckable(True)
        reveal.toggled.connect(lambda on: self.pw_field.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.pw_field, 1)
        pw_row.addWidget(reveal)
        form.addRow("SMTP password", _wrap(pw_row))
        v.addLayout(form)

        v.addWidget(h2("DNS records for this IP"))
        v.addWidget(muted(f"A     {ip.hostname}  →  {ip.ip}"))
        v.addWidget(muted(f"PTR   {ip.ip}  →  {ip.hostname}"))
        v.addStretch(1)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)


def _wrap(layout):
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    w.setLayout(layout)
    return w
