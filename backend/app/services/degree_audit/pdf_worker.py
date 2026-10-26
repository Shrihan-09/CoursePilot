"""Positioned text AND status icons from a Degree Navigator audit PDF (Phase 7.4).

Runs in the same disposable, killable child process as every uploaded-PDF
extraction (`app.services.transcripts.extract.isolated_pdf_extractor`). DN
prints requirement and course status as IMAGES, so besides positioned text
this reports, per image drawn: where (user space), its pixel size, and a
SHA-256 of its decoded data. Never the pixels themselves, never an exception
message - only the closed result vocabulary.

Coordinates: Chrome draws text inside a flipped CTM, so a text position is
the text matrix composed with the CTM (as `scheduling.pdf_worker` does); an
image's position is its CTM's translation. Both end up in the page's user
space, where an icon shares its text line's baseline.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging

MAX_FRAGMENTS = 20000
MAX_IMAGES = 2000


def extract_audit_layout(data: bytes, max_pages: int, max_chars: int) -> tuple:
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
            fragments: list[dict] = []
            images: list[dict] = []
            resources = page.get("/Resources") or {}
            xobjects = resources.get("/XObject") or {}
            digests: dict = {}

            def visit(text, cm, tm, font, size, fragments=fragments):
                if not text.strip():
                    return
                if len(fragments) >= MAX_FRAGMENTS:
                    raise ValueError("layout_limit")
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                fragments.append({"x": float(x), "y": float(y), "text": text.strip(),
                                  "font_size": float(size * abs(cm[3] or 1))})

            def before(op, args, cm, tm, xobjects=xobjects, images=images, digests=digests):
                if op != b"Do" or not args:
                    return
                name = args[0]
                try:
                    obj = xobjects[name].get_object()
                except Exception:
                    return                      # a nested form's image: not a DN icon
                if obj.get("/Subtype") != "/Image":
                    return
                if len(images) >= MAX_IMAGES:
                    raise ValueError("layout_limit")
                if name not in digests:
                    try:
                        digests[name] = hashlib.sha256(obj.get_data()).hexdigest()
                    except Exception:
                        digests[name] = None
                images.append({"x": float(cm[4]), "y": float(cm[5]),
                               "width": int(obj.get("/Width", 0)),
                               "height": int(obj.get("/Height", 0)),
                               "drawn_width": float(cm[0]), "drawn_height": float(cm[3]),
                               "digest": digests[name]})

            text = page.extract_text(visitor_text=visit, visitor_operand_before=before) or ""
            total += len(text)
            if total > max_chars:
                return ("error", "PDF_TEXT_TOO_LARGE")
            pages.append(text)
            layouts.append({"width": float(page.mediabox.width),
                            "height": float(page.mediabox.height),
                            "fragments": fragments, "images": images})
        if not any(layout["fragments"] for layout in layouts):
            # Printed through "Microsoft Print to PDF": glyphs are outlines.
            return ("error", "PDF_NO_TEXT_LAYER")
        return ("ok", pages, len(pages), layouts)
    except Exception:
        return ("error", "PDF_UNREADABLE")


def _audit_child(conn, data, max_pages, max_chars):
    from app.services.transcripts.extract import _limit_child

    _limit_child()
    try:
        conn.send(extract_audit_layout(data, max_pages, max_chars))
    except BaseException:
        with contextlib.suppress(BaseException):
            conn.send(("error", "PDF_UNREADABLE"))
    finally:
        conn.close()


def default_audit_extractor():
    from app.core.config import get_settings
    from app.services.transcripts.extract import isolated_pdf_extractor

    settings = get_settings()
    return isolated_pdf_extractor(
        max_pages=settings.transcript_max_pages,
        max_chars=settings.transcript_max_text_chars,
        timeout_seconds=settings.transcript_extraction_timeout_seconds,
        _target=_audit_child,
    )


__all__ = ["default_audit_extractor", "extract_audit_layout"]
