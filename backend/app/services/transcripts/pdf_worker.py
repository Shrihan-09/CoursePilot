"""The PDF text extractor that runs in a CHILD process (Phase 7.0).

Imported by the child only; deliberately tiny so the spawned interpreter
starts fast and holds nothing of the application (no settings, no database
handles, no request data beyond the bytes it is handed).

It reports through a pipe with a closed vocabulary - ("ok", pages, count) or
("error", CODE) - and NEVER sends an exception message: a parser exception
can quote document text, and the parent must not be able to log or echo it
by accident. Only the exception's class name is conceivable diagnostic
value, and even that stays here.
"""

from __future__ import annotations

import contextlib


def extract(conn, data: bytes, max_pages: int, max_chars: int) -> None:
    try:
        conn.send(_extract(data, max_pages, max_chars))
    except BaseException:
        with contextlib.suppress(BaseException):
            conn.send(("error", "PDF_UNREADABLE"))
    finally:
        conn.close()


def _extract(data: bytes, max_pages: int, max_chars: int) -> tuple:
    import io
    import logging

    # pypdf warns about malformed structure through logging; a warning can
    # quote object content. Silenced in the child, which owns no handlers.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception:
        return ("error", "PDF_UNREADABLE")
    if reader.is_encrypted:
        # Real Rutgers transcripts (iText-generated) are "encrypted" with an
        # EMPTY user password: permissions-only protection that every viewer
        # opens without a prompt. That is the PDF standard's way of saying "no
        # password", so it is opened the same way. A document that needs an
        # actual password is refused - no password is ever guessed or asked for.
        try:
            opened = reader.decrypt("")
        except Exception:
            return ("error", "PDF_ENCRYPTED")
        if not opened:
            return ("error", "PDF_ENCRYPTED")
    try:
        count = len(reader.pages)
    except Exception:
        return ("error", "PDF_UNREADABLE")
    if count == 0:
        return ("error", "PDF_UNREADABLE")
    if count > max_pages:
        return ("error", "PDF_TOO_MANY_PAGES")
    pages: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            return ("error", "PDF_UNREADABLE")
        total += len(text)
        if total > max_chars:
            return ("error", "PDF_TEXT_TOO_LARGE")
        pages.append(text)
    if not any(p.strip() for p in pages):
        # A scan or a photo: no text layer. OCR is out of scope (and would
        # turn extraction errors into silent academic errors).
        return ("error", "PDF_NO_TEXT_LAYER")
    return ("ok", pages, count)
