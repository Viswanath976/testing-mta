"""Lightweight non-blocking toast notifications (success/info messages).

Errors and confirmations still use blocking dialogs; toasts are only for
'it worked' feedback so the workflow isn't interrupted by popups.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from . import theme


class _Toast(QFrame):
    def __init__(self, parent, message: str, kind: str):
        super().__init__(parent)
        self.setObjectName("Card")
        color = {"success": theme.OK, "info": theme.ACCENT, "warn": theme.WARN}.get(kind, theme.OK)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 16, 10)
        icon = QLabel("✓" if kind == "success" else "ℹ")
        icon.setStyleSheet(f"color:{color}; font-weight:700; font-size:16px;")
        h.addWidget(icon)
        h.addWidget(QLabel(message))
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        QTimer.singleShot(2600, self.close)

    def _place(self):
        p = self.parent()
        if p:
            self.move(max(12, p.width() - self.width() - 24),
                      max(12, p.height() - self.height() - 28))


def show_toast(window, message: str, kind: str = "success") -> None:
    if window is not None:
        _Toast(window, message, kind)
