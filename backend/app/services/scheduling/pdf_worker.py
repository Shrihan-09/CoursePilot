"""Positioned text extraction inside the same bounded disposable PDF process."""

from __future__ import annotations

import contextlib
import io
import logging


def extract_layout(data, max_pages, max_chars):
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return ("error", "PDF_ENCRYPTED")
        if not 0 < len(reader.pages) <= max_pages:
            return ("error", "PDF_TOO_MANY_PAGES")
        pages, layouts, total = [], [], 0
        for page in reader.pages:
            if page.rotation % 360:
                return ("error", "UNSUPPORTED_LAYOUT")
            fragments = []

            def visit(text, cm, tm, font, size, fragments=fragments):
                if not text.strip():
                    return
                if len(fragments) >= 20000:
                    raise ValueError("layout_limit")
                # Full affine text->user-space composition; no weekday inference.
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                fragments.append(
                    {"x": float(x), "y": float(y), "text": text.strip(), "font_size": float(size)}
                )

            text = page.extract_text(visitor_text=visit) or ""
            total += len(text)
            if total > max_chars:
                return ("error", "PDF_TEXT_TOO_LARGE")
            pages.append(text)
            layouts.append(
                {
                    "width": float(page.mediabox.width),
                    "height": float(page.mediabox.height),
                    "fragments": fragments,
                }
            )
        if not any(text.strip() for text in pages):
            return ("error", "UNSUPPORTED_LAYOUT")
        return ("ok", pages, len(pages), layouts)
    except Exception:
        return ("error", "PDF_UNREADABLE")


def _layout_child(conn, data, max_pages, max_chars):
    from app.services.transcripts.extract import _limit_child

    _limit_child()
    try:
        conn.send(extract_layout(data, max_pages, max_chars))
    except BaseException:
        with contextlib.suppress(BaseException):
            conn.send(("error", "PDF_UNREADABLE"))
    finally:
        conn.close()


def default_schedule_extractor():
    from app.core.config import get_settings
    from app.services.transcripts.extract import isolated_pdf_extractor

    settings = get_settings()
    return isolated_pdf_extractor(
        max_pages=settings.transcript_max_pages,
        max_chars=settings.transcript_max_text_chars,
        timeout_seconds=settings.transcript_extraction_timeout_seconds,
        _target=_layout_child,
    )
