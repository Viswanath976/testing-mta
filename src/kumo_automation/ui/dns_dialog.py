"""Generated DNS records view — A / MX / SPF / DKIM / DMARC / PTR with Copy & Export.

DKIM keys are RSA-generated (slow), so generation runs off the UI thread.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import dnsgen
from ..core.models import ServerProfile
from .widgets import h2, muted


class _DnsWorker(QThread):
    done = Signal(object)  # (records, keys) or Exception

    def __init__(self, profile: ServerProfile, keys: dict | None = None):
        super().__init__()
        self.profile = profile
        self.keys = keys

    def run(self):
        try:
            self.done.emit(dnsgen.generate_records(self.profile, keys=self.keys))
        except Exception as exc:  # noqa: BLE001
            self.done.emit(exc)


class DnsDialog(QDialog):
    def __init__(self, profile: ServerProfile, parent=None, keys: dict | None = None):
        super().__init__(parent)
        self.profile = profile
        self.keys = keys
        self.records = []
        self.keys: dict[str, str] = {}
        self.setWindowTitle(f"DNS records — {profile.name}")
        self.resize(920, 600)
        is_prod = profile.environment.value == "production"
        v = QVBoxLayout(self)
        v.addWidget(h2(("Production DNS records" if is_prod else "Draft DNS records (Testing)")))
        if not is_prod:
            v.addWidget(muted("These are DRAFT records for a testing server. "
                              "Promote to Production to generate final DNS."))
        self.status = muted("Generating (DKIM keys take a moment)…")
        v.addWidget(self.status)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Action", "Type", "Name", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.b_copy = QPushButton("Copy all")
        self.b_copy.clicked.connect(self._copy)
        self.b_export = QPushButton("Export (CSV + Excel + TXT + DKIM)…")
        self.b_export.clicked.connect(self._export)
        self.b_verify = QPushButton("Verify live DNS")
        self.b_verify.clicked.connect(self._verify)
        self.b_copy.setEnabled(False)
        self.b_export.setEnabled(False)
        row.addWidget(self.b_copy)
        row.addWidget(self.b_export)
        row.addWidget(self.b_verify)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)

        self.worker = _DnsWorker(profile, keys=self.keys)
        self.worker.done.connect(self._done)
        self.worker.start()

    def _done(self, result):
        if isinstance(result, Exception):
            self.status.setText(f"Failed: {result}")
            return
        self.records, self.keys = result
        for r in self.records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for c, val in enumerate([r.action, r.type, r.name, r.value]):
                self.table.setItem(row, c, QTableWidgetItem(val))
        self.status.setText(f"{len(self.records)} records · {len(self.keys)} DKIM key(s). "
                            "Records marked 'merge' must be combined with existing DNS.")
        self.b_copy.setEnabled(True)
        self.b_export.setEnabled(True)

    def _copy(self):
        lines = [f"{r.type}\t{r.name}\t{r.value}" for r in self.records]
        QApplication.clipboard().setText("\n".join(lines))
        self.status.setText("Copied to clipboard.")

    def _export(self):
        d = QFileDialog.getExistingDirectory(self, "Export to folder")
        if not d:
            return
        out = Path(d)
        (out / "dns-records.csv").write_text(dnsgen.records_to_csv(self.records), encoding="utf-8")
        (out / "dns-records.txt").write_text(dnsgen.records_to_txt(self.records), encoding="utf-8")
        try:
            dnsgen.records_to_xlsx(self.records, out / "dns-records.xlsx")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Excel export", f"Could not write .xlsx: {exc}")
        for domain, pem in self.keys.items():
            (out / f"dkim-{domain}.key").write_text(pem, encoding="utf-8")
        QMessageBox.information(self, "Exported",
                               f"Wrote CSV + Excel + TXT + {len(self.keys)} DKIM key(s) to:\n{out}")

    def _verify(self):
        from .workers import DnsVerifyWorker

        self.status.setText("Verifying live DNS (A/AAAA/MX/SPF/DKIM/DMARC/PTR/EHLO) …")
        self.b_verify.setEnabled(False)
        self._vworker = DnsVerifyWorker(self.profile)
        self._vworker.done.connect(self._verified)
        self._vworker.start()

    def _verified(self, checks):
        self.b_verify.setEnabled(True)
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(["Result", "Check", "", "Detail"])
        p = w = f = 0
        for c in checks:
            r = self.table.rowCount()
            self.table.insertRow(r)
            mark = {"pass": "✓ PASS", "warn": "⚠ WARN", "fail": "✗ FAIL"}.get(c.state, c.state)
            p += c.state == "pass"; w += c.state == "warn"; f += c.state == "fail"
            for col, val in enumerate([mark, c.name, "", c.detail]):
                self.table.setItem(r, col, QTableWidgetItem(val))
        self.status.setText(f"Verification: {p} pass · {w} warn · {f} fail")
