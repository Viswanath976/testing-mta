"""Searchable, paginated IP table — handles thousands of generated IPs without
freezing the UI (only the current page of rows is materialized)."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.models import IPEntry

_DUP_BG = QColor(224, 49, 49, 60)  # translucent red for duplicate cells

_COLS = ["IP", "Hostname", "Username", "Domain", "Pool", "Tenant"]


class IPTable(QWidget):
    entryActivated = Signal(object)  # IPEntry on double-click

    def __init__(self, page_size: int = 100):
        super().__init__()
        self.page_size = page_size
        self._all: list[IPEntry] = []
        self._filtered: list[IPEntry] = []
        self._highlight: set[str] = set()
        self._page = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search IP / hostname / username / domain…")
        self.search.textChanged.connect(self._on_search)
        self.domain_filter = QComboBox()
        self.domain_filter.currentTextChanged.connect(lambda *_: self._apply_filter())
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Domain:"))
        top.addWidget(self.domain_filter)
        v.addLayout(top)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self._page_rows: list[IPEntry] = []
        v.addWidget(self.table)

        nav = QHBoxLayout()
        self.prev = QPushButton("‹ Prev")
        self.prev.clicked.connect(lambda: self._go(self._page - 1))
        self.next = QPushButton("Next ›")
        self.next.clicked.connect(lambda: self._go(self._page + 1))
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        nav.addWidget(self.prev)
        nav.addWidget(self.next)
        nav.addStretch(1)
        nav.addWidget(self.info)
        v.addLayout(nav)

    # ------------------------------------------------------------------ #
    def set_entries(self, entries: list[IPEntry]) -> None:
        self._all = entries
        domains = sorted({e.domain for e in entries if e.domain})
        self.domain_filter.blockSignals(True)
        self.domain_filter.clear()
        self.domain_filter.addItems(["(all)"] + domains)
        self.domain_filter.blockSignals(False)
        self._apply_filter()

    def set_highlight(self, values: set[str]) -> None:
        """Cells whose text is in `values` (duplicate IPs/hostnames/usernames) get a red tint."""
        self._highlight = values or set()
        self._render()

    def _on_double_click(self, row: int, _col: int):
        if 0 <= row < len(self._page_rows):
            self.entryActivated.emit(self._page_rows[row])

    def _on_search(self, *_):
        self._apply_filter()

    def _apply_filter(self):
        q = self.search.text().strip().lower()
        dom = self.domain_filter.currentText()
        rows = self._all
        if dom and dom != "(all)":
            rows = [e for e in rows if e.domain == dom]
        if q:
            rows = [e for e in rows
                    if q in e.ip.lower() or q in e.hostname.lower()
                    or q in e.username.lower() or q in e.domain.lower()]
        self._filtered = rows
        self._page = 0
        self._render()

    def _go(self, page: int):
        pages = max(1, (len(self._filtered) + self.page_size - 1) // self.page_size)
        self._page = max(0, min(page, pages - 1))
        self._render()

    def _render(self):
        pages = max(1, (len(self._filtered) + self.page_size - 1) // self.page_size)
        start = self._page * self.page_size
        chunk = self._filtered[start:start + self.page_size]
        self._page_rows = chunk
        self.table.setRowCount(0)
        for e in chunk:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, val in enumerate([e.ip, e.hostname, e.username, e.domain, e.pool, e.tenant]):
                item = QTableWidgetItem(val)
                if val in self._highlight:
                    item.setBackground(_DUP_BG)
                self.table.setItem(r, c, item)
        self.prev.setEnabled(self._page > 0)
        self.next.setEnabled(self._page < pages - 1)
        shown = f"{start + 1}–{start + len(chunk)}" if chunk else "0"
        self.info.setText(f"Showing {shown} of {len(self._filtered)}  ·  page {self._page + 1}/{pages}")
