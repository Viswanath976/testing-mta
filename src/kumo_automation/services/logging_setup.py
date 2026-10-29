"""Central logging via loguru with a rotating per-run file."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_dir: Path | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(log_dir / "kumo-automation.log", level="DEBUG",
                   rotation="5 MB", retention=10, enqueue=True)


__all__ = ["setup_logging", "logger"]
