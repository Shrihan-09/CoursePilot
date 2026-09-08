"""Structured logging setup.

JSON in deployed environments (so logs are queryable), plain text locally
(so they are readable). Configured once from `app.main`.
"""

from __future__ import annotations

import logging
import sys

from app.core.config import Environment, Settings


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stdout)

    if settings.coursepilot_env is Environment.LOCAL:
        handler.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
    else:
        from pythonjsonlogger import jsonlogger

        handler.setFormatter(
            jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
