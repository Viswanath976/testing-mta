"""Application theme — a modern, professional stylesheet that adapts to the
OS light/dark preference. Pure presentation; no logic.
"""
from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

# Semantic colors reused across widgets (dashboard cards, status chips, …)
OK = "#2f9e44"
WARN = "#e8590c"
BAD = "#e03131"
ACCENT = "#1c7ed6"
MUTED = "#868e96"


def is_dark(app: QApplication) -> bool:
    c = app.palette().color(QPalette.Window)
    return (c.red() + c.green() + c.blue()) / 3 < 128


def _qss(dark: bool) -> str:
    if dark:
        bg, panel, card, text, sub, border, hover = (
            "#1a1b1e", "#212226", "#2a2c31", "#e9ecef", "#adb5bd", "#3a3d42", "#31343a")
    else:
        bg, panel, card, text, sub, border, hover = (
            "#f4f5f7", "#ffffff", "#ffffff", "#212529", "#6b7177", "#dee2e6", "#eef1f4")
    return f"""
    QWidget {{ color: {text}; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {bg}; }}
    QLabel#H1 {{ font-size: 20px; font-weight: 600; }}
    QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
    QLabel#Muted {{ color: {sub}; }}

    #Sidebar {{ background: {panel}; border-right: 1px solid {border}; }}
    #Card, QGroupBox {{ background: {card}; border: 1px solid {border}; border-radius: 10px; }}
    QGroupBox {{ margin-top: 14px; padding: 14px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {sub}; }}

    QTreeWidget, QTableWidget, QPlainTextEdit, QTextBrowser, QListWidget {{
        background: {panel}; border: 1px solid {border}; border-radius: 8px;
        selection-background-color: {ACCENT}; selection-color: white;
    }}
    QTreeWidget::item, QListWidget::item {{ padding: 5px 4px; }}
    QHeaderView::section {{ background: {hover}; color: {sub}; border: none;
        border-bottom: 1px solid {border}; padding: 6px; font-weight: 600; }}

    QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
        background: {panel}; border: 1px solid {border}; border-radius: 7px; padding: 6px 8px; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}

    QPushButton {{ background: {hover}; border: 1px solid {border}; border-radius: 8px;
        padding: 7px 14px; font-weight: 600; }}
    QPushButton:hover {{ border: 1px solid {ACCENT}; }}
    QPushButton:disabled {{ color: {MUTED}; }}
    QPushButton#Primary {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT}; }}
    QPushButton#Primary:hover {{ background: #1971c2; }}
    QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; text-align: left; }}
    QPushButton#Ghost:hover {{ background: {hover}; }}
    QPushButton#NavActive {{ background: {ACCENT}; color: white; border: none; text-align: left; }}

    QCheckBox, QRadioButton {{ spacing: 8px; padding: 4px 0; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(_qss(is_dark(app)))
