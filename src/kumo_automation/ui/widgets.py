"""Reusable UI building blocks + small helpers (professional look & feel)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.validation import sanitize_host  # noqa: F401  (re-exported for UI imports)
from . import theme

# Environment colour coding (tree + overview) — Testing & Production only
ENV_EMOJI = {"testing": "🟢", "production": "🔴"}
ENV_LABEL = {"testing": "Testing", "production": "Production"}
STATUS_EMOJI = {"ok": "🟢", "warn": "🟡", "fail": "🔴", "pending": "⚪"}


# --------------------------------------------------------------------------- #
#  Small widgets
# --------------------------------------------------------------------------- #
class StatCard(QFrame):
    """A dashboard metric tile: big number + caption, optional accent color."""

    def __init__(self, caption: str, value: str = "0", accent: str = theme.ACCENT):
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumWidth(150)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        self.value = QLabel(value)
        self.value.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {accent};")
        cap = QLabel(caption)
        cap.setObjectName("Muted")
        v.addWidget(self.value)
        v.addWidget(cap)

    def set_value(self, value) -> None:
        self.value.setText(str(value))


class Stepper(QListWidget):
    """Vertical step indicator for the wizard."""

    def __init__(self, steps: list[str]):
        super().__init__()
        self._steps = steps
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QListWidget.NoSelection)
        self.setFixedWidth(210)
        self._render(0)

    def _render(self, current: int) -> None:
        self.clear()
        for i, s in enumerate(self._steps):
            mark = "●" if i == current else ("✓" if i < current else "○")
            item = QListWidgetItem(f"  {mark}  {i + 1}. {s}")
            if i == current:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.addItem(item)

    def set_current(self, i: int) -> None:
        self._render(i)


class CheckRow(QWidget):
    """A single ✓ / ⚠ / ○ status line for validation & deploy-progress views."""

    def __init__(self, label: str):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(2, 3, 2, 3)
        self.icon = QLabel("○")
        self.icon.setFixedWidth(22)
        self.icon.setStyleSheet(f"color: {theme.MUTED}; font-size: 15px;")
        self.text = QLabel(label)
        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        h.addWidget(self.icon)
        h.addWidget(self.text)
        h.addStretch(1)
        h.addWidget(self.detail)

    def set_state(self, state: str, detail: str = "") -> None:
        # state: pending | running | ok | warn | fail
        mark, color = {
            "pending": ("○", theme.MUTED),
            "running": ("◐", theme.ACCENT),
            "ok": ("✓", theme.OK),
            "warn": ("⚠", theme.WARN),
            "fail": ("✗", theme.BAD),
        }.get(state, ("○", theme.MUTED))
        self.icon.setText(mark)
        self.icon.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: 700;")
        if detail:
            self.detail.setText(detail)


def h1(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("H1")
    return lbl


def h2(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("H2")
    return lbl


def muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    return lbl
