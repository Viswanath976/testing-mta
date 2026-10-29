"""Deployment history for a server (reachable from the Server Overview)."""
from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.models import Customer, ServerProfile
from .widgets import h2, muted


class DeploymentHistoryDialog(QDialog):
    def __init__(self, customer: Customer, server: ServerProfile, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Deployment History — {server.name}")
        self.resize(680, 420)
        v = QVBoxLayout(self)
        v.addWidget(h2(f"{server.name} — deployment history"))

        recs = sorted([r for r in customer.history if r.server == server.name],
                      key=lambda r: r.timestamp, reverse=True)
        if not recs:
            v.addWidget(muted("No deployments recorded yet for this server."))
        else:
            cols = ["When", "Type", "Status", "Operator", "OS user", "Version", "Report"]
            self.table = QTableWidget(0, len(cols))
            self.table.setHorizontalHeaderLabels(cols)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            v.addWidget(self.table)
            self._reports: dict[int, str] = {}
            for r in recs:
                row = self.table.rowCount()
                self.table.insertRow(row)
                status = r.status_label or ("Success" if r.success else "Failed")
                vals = [
                    r.timestamp.strftime("%Y-%m-%d %H:%M"),
                    r.deploy_type or r.environment.value,
                    status,
                    r.operator_name or "—",
                    r.windows_user or "—",
                    r.config_hash or "—",
                    Path(r.report_path).name if r.report_path else "—",
                ]
                for c, val in enumerate(vals):
                    self.table.setItem(row, c, QTableWidgetItem(val))
                if r.report_path:
                    self._reports[row] = r.report_path
            self.table.cellDoubleClicked.connect(self._open_report)
            v.addWidget(muted("Double-click a row to open its HTML report."))

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)

    def _open_report(self, row: int, _col: int):
        path = getattr(self, "_reports", {}).get(row)
        if path and Path(path).exists():
            webbrowser.open(Path(path).as_uri())
