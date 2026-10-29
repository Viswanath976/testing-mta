"""Logs Center — browse application / deployment / test logs and reports, and
save a copy of any of them.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .widgets import h2, muted


class LogsDialog(QDialog):
    def __init__(self, logs_dir: Path, reports_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logs Center")
        self.resize(860, 600)
        self.logs_dir = Path(logs_dir)
        self.reports_dir = Path(reports_dir)

        v = QVBoxLayout(self)
        v.addWidget(h2("Logs & reports"))
        v.addWidget(muted("Application, deployment, health, monitoring and SMTP-test logs."))
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMaximumWidth(320)
        self.list.currentItemChanged.connect(self._show)
        body.addWidget(self.list)
        self.viewer = QPlainTextEdit(readOnly=True)
        body.addWidget(self.viewer, 1)
        v.addLayout(body)

        row = QHBoxLayout()
        b_refresh = QPushButton("Refresh")
        b_refresh.clicked.connect(self._populate)
        self.b_save = QPushButton("Save a copy…")
        self.b_save.clicked.connect(self._save)
        row.addWidget(b_refresh)
        row.addWidget(self.b_save)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
        self._populate()

    def _populate(self):
        self.list.clear()
        files = []
        for d in (self.logs_dir, self.reports_dir):
            if d.exists():
                files += sorted(d.glob("*.log")) + sorted(d.glob("*.jsonl")) + sorted(d.glob("*.html"))
        for p in files:
            it = QListWidgetItem(f"{p.parent.name}/{p.name}")
            it.setData(256, str(p))
            self.list.addItem(it)
        if not files:
            self.list.addItem("(no logs yet)")

    def _show(self, cur, _prev=None):
        if not cur:
            return
        path = cur.data(256)
        if not path:
            return
        p = Path(path)
        if p.suffix == ".html":
            self.viewer.setPlainText(f"[HTML report]  {p}\n\nUse 'Save a copy' or open it in a browser.")
        else:
            try:
                self.viewer.setPlainText(p.read_text(encoding="utf-8", errors="replace")[-40000:])
            except Exception as exc:  # noqa: BLE001
                self.viewer.setPlainText(f"Could not read: {exc}")

    def _save(self):
        cur = self.list.currentItem()
        if not cur or not cur.data(256):
            return
        src = Path(cur.data(256))
        dest, _ = QFileDialog.getSaveFileName(self, "Save a copy", src.name)
        if dest:
            shutil.copyfile(src, dest)
            QMessageBox.information(self, "Saved", f"Copied to:\n{dest}")
