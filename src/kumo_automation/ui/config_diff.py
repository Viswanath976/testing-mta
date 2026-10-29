"""Config preview with side-by-side diff (Phase 2, Feature 4).

Shows every generated file next to the copy currently on the server:
  * generated instantly (local, no SSH)
  * 'Fetch current from server' pulls the live files over SSH for a real diff

Uses difflib.HtmlDiff for a coloured, side-by-side table.
"""
from __future__ import annotations

import difflib

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from ..core.models import ServerProfile
from .workers import PreviewResult, PreviewWorker

_CSS = """
<style>
  table.diff {font-family: Consolas, monospace; font-size: 12px; border-collapse: collapse; width: 100%;}
  .diff_header {background:#f0f0f0; color:#666; text-align:right; padding:0 6px;}
  td.diff_header {white-space:nowrap;}
  .diff_next {background:#f0f0f0;}
  .diff_add {background:#d3f9d8;}
  .diff_chg {background:#fff3bf;}
  .diff_sub {background:#ffc9c9;}
  td {vertical-align: top; padding: 0 4px; white-space: pre-wrap;}
</style>
"""


class ConfigPreviewDialog(QDialog):
    def __init__(self, profile: ServerProfile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setWindowTitle(f"Config preview — {profile.name}")
        self.resize(1040, 640)
        self._generated: dict[str, str] = {}
        self._remote: dict[str, str] | None = None
        self.worker: PreviewWorker | None = None

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("File:"))
        self.file_combo = QComboBox()
        self.file_combo.currentTextChanged.connect(self._refresh)
        top.addWidget(self.file_combo)
        top.addStretch(1)
        self.status = QLabel("Left: current on server · Right: generated")
        top.addWidget(self.status)
        self.btn_fetch = QPushButton("Fetch current from server")
        self.btn_fetch.clicked.connect(self._fetch_remote)
        top.addWidget(self.btn_fetch)
        v.addLayout(top)

        self.view = QTextBrowser()
        v.addWidget(self.view)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        v.addWidget(close)

        # generate locally right away (no SSH)
        self._start_worker(fetch_remote=False)

    # ------------------------------------------------------------------ #
    def _start_worker(self, *, fetch_remote: bool, password: str | None = None):
        self.btn_fetch.setEnabled(False)
        self.status.setText("Fetching current config…" if fetch_remote else "Generating…")
        self.worker = PreviewWorker(self.profile, ssh_password=password, fetch_remote=fetch_remote)
        self.worker.log.connect(lambda m: self.status.setText(m))
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _fetch_remote(self):
        pw = None
        if self.profile.ssh.use_password:
            pw, ok = QInputDialog.getText(self, "SSH password",
                                          f"Password for {self.profile.ssh.host}:")
            if not ok:
                return
        self._start_worker(fetch_remote=True, password=pw)

    def _on_done(self, res: PreviewResult):
        self._generated = res.generated
        if res.remote is not None:
            self._remote = res.remote
        self.btn_fetch.setEnabled(True)
        if self.file_combo.count() == 0:
            self.file_combo.addItems(sorted(res.generated))
        if res.error:
            self.status.setText(f"Showing generated only — {res.error}")
        elif self._remote is not None:
            self.status.setText("Left: current on server · Right: generated")
        else:
            self.status.setText("Generated (not yet compared with server)")
        self._refresh()

    def _refresh(self, *_):
        name = self.file_combo.currentText()
        if not name or name not in self._generated:
            return
        generated = self._generated.get(name, "")
        current = (self._remote or {}).get(name, "")
        left = current.splitlines()
        right = generated.splitlines()
        table = difflib.HtmlDiff(wrapcolumn=80).make_table(
            left, right,
            fromdesc="current on server" if self._remote is not None else "(not fetched)",
            todesc="generated",
            context=False,
        )
        self.view.setHtml(_CSS + table)
