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

    # Phase 5.10: every record carries the request correlation id, injected
    # by a filter rather than by call sites - so the Degree Engine, the
    # cache and the providers are correlated too, without any of them
    # needing to know an HTTP layer exists.
    from app.core.observability import RequestIdFilter

    handler.addFilter(RequestIdFilter())

    if settings.coursepilot_env is Environment.LOCAL:
        handler.setFormatter(
            logging.Formatter("%(levelname)-8s [%(request_id)s] %(name)s: %(message)s")
        )
    else:
        from pythonjsonlogger import jsonlogger

        handler.setFormatter(
            jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s"
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
