"""Desktop entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .services.app_settings import AppSettings
from .services.logging_setup import setup_logging
from .services.project_store import ProjectStore

APP_DIR = Path.home() / ".kumo-automation"
PROJECTS_DIR = APP_DIR / "customers"
REPORTS_DIR = APP_DIR / "reports"
LOG_DIR = APP_DIR / "logs"


def main() -> int:
    setup_logging(LOG_DIR)
    store = ProjectStore(PROJECTS_DIR)
    settings = AppSettings(APP_DIR)   # NOT PROJECTS_DIR — keep settings out of the customers folder

    app = QApplication(sys.argv)
    from .ui.main_window import MainWindow  # imported late so headless/CLI needs no Qt event loop
    from .ui.theme import apply_theme

    apply_theme(app)
    win = MainWindow(store, REPORTS_DIR, settings, logs_dir=LOG_DIR)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
