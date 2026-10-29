"""SMTP testing — AUTH/STARTTLS verification and optional test-email send.

History is appended to <logs>/smtp-tests.jsonl and shown at the bottom.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ServerProfile
from .widgets import CheckRow, h2, muted
from .workers import SmtpTestWorker


class SmtpTestDialog(QDialog):
    def __init__(self, profile: ServerProfile, logs_dir: Path, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.logs_dir / "smtp-tests.jsonl"
        self._pw = None
        self.worker: SmtpTestWorker | None = None
        self.setWindowTitle(f"SMTP Test — {profile.name}")
        self.resize(600, 620)

        v = QVBoxLayout(self)
        v.addWidget(h2("SMTP test"))
        form = QFormLayout()
        self.ip_combo = QComboBox()
        for ip in profile.ips:
            self.ip_combo.addItem(f"{ip.ip}  ({ip.username} @ {ip.domain})", ip)
        self.ip_combo.currentIndexChanged.connect(self._credentials_for_selected_ip)
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(profile.smtp_port)
        self.smtp_username = QLineEdit()
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.Password)
        self._credentials_for_selected_ip()
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.smtp_password)
        reveal = QPushButton("Reveal")
        reveal.setCheckable(True)
        reveal.toggled.connect(lambda on: self.smtp_password.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        pw_row.addWidget(reveal)
        pw_widget = self._layout_widget(pw_row)

        self.recipient = QLineEdit()
        self.recipient.setPlaceholderText("recipient@example.com (only for Send Test Email)")
        form.addRow("Sending IP", self.ip_combo)
        form.addRow("SMTP port", self.smtp_port)
        form.addRow("SMTP username", self.smtp_username)
        form.addRow("SMTP password", pw_widget)
        form.addRow("Test recipient", self.recipient)
        v.addLayout(form)

        row = QHBoxLayout()
        self.b_auth = QPushButton("Verify AUTH + STARTTLS")
        self.b_auth.setObjectName("Primary")
        self.b_auth.clicked.connect(lambda: self._run(send=False))
        self.b_send = QPushButton("Send Test Email")
        self.b_send.clicked.connect(lambda: self._run(send=True))
        row.addWidget(self.b_auth)
        row.addWidget(self.b_send)
        row.addStretch(1)
        v.addLayout(row)

        self.status = muted("")
        v.addWidget(self.status)
        self.results = QVBoxLayout()
        v.addLayout(self.results)

        v.addWidget(h2("Recent tests"))
        self.history = QPlainTextEdit(readOnly=True)
        self.history.setMaximumHeight(160)
        v.addWidget(self.history)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)
        self._load_history()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _layout_widget(layout):
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _credentials_for_selected_ip(self):
        ip = self.ip_combo.currentData()
        if not ip:
            return
        self.smtp_username.setText(self.profile.effective_username(ip))
        self.smtp_password.setText(self.profile.effective_password(ip))

    def _password(self):
        if self.profile.ssh.use_password and self._pw is None:
            pw, ok = QInputDialog.getText(self, "SSH password",
                                          f"Password for {self.profile.ssh.host}:",
                                          echo=QLineEdit.Password)
            self._pw = pw if ok else ""
        return self._pw

    def _run(self, send: bool):
        ip = self.ip_combo.currentData()
        if not ip:
            QMessageBox.warning(self, "No IP", "This server has no IPs to test.")
            return
        username = self.smtp_username.text().strip()
        password = self.smtp_password.text()
        if not username:
            QMessageBox.warning(self, "SMTP username needed", "Enter the SMTP username to test.")
            return
        if not password:
            QMessageBox.warning(self, "SMTP password needed", "Enter the SMTP password to test.")
            return
        if send:
            rcpt = self.recipient.text().strip()
            if not rcpt or "@" not in rcpt:
                QMessageBox.warning(self, "Recipient needed", "Enter a valid recipient email address.")
                return
            if QMessageBox.question(
                self, "Send test email",
                f"Send a real test email to {rcpt} via {ip.ip} ({ip.username})?"
            ) != QMessageBox.Yes:
                return
        self.status.setText("Running SMTP test …")
        self.b_auth.setEnabled(False)
        self.b_send.setEnabled(False)
        self.worker = SmtpTestWorker(
            self.profile, ip, self._password(),
            recipient=self.recipient.text().strip() or None,
            send=send,
            smtp_port=self.smtp_port.value(),
            smtp_username=username,
            smtp_password=password,
        )
        self.worker.done.connect(self._done)
        self.worker.start()

    def _clear_results(self):
        while self.results.count():
            it = self.results.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _done(self, res: dict):
        self.b_auth.setEnabled(True)
        self.b_send.setEnabled(True)
        self._clear_results()

        def row(name, value, ok=None):
            r = CheckRow(name)
            state = "ok" if ok else ("fail" if ok is False else "pending")
            r.set_state(state, str(value) if value not in (None, "") else "—")
            self.results.addWidget(r)

        auth_ok = res.get("auth") == "ok"
        row("SMTP AUTH", res.get("auth", "—"), auth_ok)
        row("STARTTLS", res.get("starttls", "—"), res.get("starttls") == "yes")
        row("Sending IP", res.get("sending_ip"))
        row("SMTP host", res.get("smtp_host", "127.0.0.1"))
        row("SMTP port", res.get("smtp_port", self.profile.smtp_port))
        row("SMTP username", res.get("smtp_username", self.smtp_username.text().strip()))
        row("Return-Path / Envelope From", res.get("return_path"))
        if "mail_from" in res:
            row("MAIL FROM", res.get("mail_from"), str(res.get("mail_from", "")).startswith("2"))
            row("RCPT TO", res.get("rcpt_to"), str(res.get("rcpt_to", "")).startswith("2"))
            row("DATA", res.get("data"), str(res.get("data", "")).startswith("2"))
            row("Message-ID", res.get("message_id"))
            row("Delivery response", res.get("delivery"))
        if res.get("error"):
            row("Error", res["error"], False)
        row("Time taken", f"{res.get('time', '?')}s")
        self.status.setText("✓ AUTH OK" if auth_ok else "✗ AUTH failed")
        self._save_history(res)

    def _save_history(self, res: dict):
        try:
            with self.history_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"server": self.profile.name, **res}) + "\n")
        except Exception:  # noqa: BLE001
            pass
        self._load_history()

    def _load_history(self):
        if not self.history_file.exists():
            self.history.setPlainText("(no tests yet)")
            return
        lines = self.history_file.read_text(encoding="utf-8").splitlines()[-10:]
        out = []
        for ln in reversed(lines):
            try:
                d = json.loads(ln)
                out.append(f"{d.get('server')}  auth={d.get('auth')}  tls={d.get('starttls')}  "
                           f"ip={d.get('sending_ip')}  {('sent ' + d.get('message_id','')) if d.get('message_id') else ''}")
            except Exception:  # noqa: BLE001
                pass
        self.history.setPlainText("\n".join(out) or "(no tests yet)")
