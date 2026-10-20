"""Uploaded bytes -> page text, defensively (Phase 7.0).

An uploaded document is hostile until proven otherwise. Checks, in order:

  1. size - refused above `transcript_max_bytes` (the route already stopped
     reading the body there);
  2. type - the bytes must BE a PDF: `%PDF-` within the first 1024 bytes
     (the PDF specification's tolerance). The client's Content-Type and any
     extension are never consulted;
  3. extraction in a CHILD PROCESS (`pdf_worker`) with a wall-clock timeout:
     a malformed or malicious PDF that loops, recurses or balloons memory
     kills a disposable process, never the API worker. Encrypted PDFs, too
     many pages, too much text and no text layer are refused with fixed
     codes.

Every refusal is an `ExtractionFailed` carrying ONLY a code from a closed
set. No exception text, no document text, crosses this boundary.

Resource limits beyond the timeout (RLIMIT_AS / RLIMIT_CPU) are applied in
the child on POSIX; Windows development has the timeout and the kill.
"""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable
from dataclasses import dataclass

PDF_MAGIC = b"%PDF-"

FAILURE_CODES = frozenset(
    {
        "FILE_EMPTY",
        "FILE_TOO_LARGE",
        "FILE_NOT_PDF",
        "PDF_ENCRYPTED",
        "PDF_TOO_MANY_PAGES",
        "PDF_TEXT_TOO_LARGE",
        "PDF_NO_TEXT_LAYER",
        "PDF_UNREADABLE",
        "PDF_EXTRACTION_TIMEOUT",
        "UNSUPPORTED_LAYOUT",
        "PDF_EXTRACTION_CRASHED",
        "NO_COURSE_ROWS",
        "TOO_MANY_ROWS",
        "PROCESSING_INTERRUPTED",
    }
)


class ExtractionFailed(Exception):
    def __init__(self, code: str) -> None:
        assert code in FAILURE_CODES, code
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractedText:
    pages: list[str]
    page_count: int
    layout: list[dict] | None = None


#: (bytes) -> ExtractedText, raising ExtractionFailed. Injectable for tests.
Extractor = Callable[[bytes], ExtractedText]


def check_document(data: bytes, max_bytes: int) -> None:
    if not data:
        raise ExtractionFailed("FILE_EMPTY")
    if len(data) > max_bytes:
        raise ExtractionFailed("FILE_TOO_LARGE")
    if PDF_MAGIC not in data[:1024]:
        raise ExtractionFailed("FILE_NOT_PDF")


def _limit_child() -> None:  # pragma: no cover - POSIX only
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    except Exception:
        pass


def _child(conn, data, max_pages, max_chars):  # runs in the spawned process
    _limit_child()
    from app.services.transcripts.pdf_worker import extract

    extract(conn, data, max_pages, max_chars)


def isolated_pdf_extractor(
    *, max_pages: int, max_chars: int, timeout_seconds: float, _target=None
) -> Extractor:
    """An Extractor that parses in a fresh, killable child process.

    `_target` replaces the child's entry point - tests only, to prove that a
    hung or crashing parser is contained.
    """
    target = _target or _child

    def run(data: bytes) -> ExtractedText:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=target, args=(child, data, max_pages, max_chars), daemon=True)
        proc.start()
        child.close()
        try:
            if not parent.poll(timeout_seconds):
                raise ExtractionFailed("PDF_EXTRACTION_TIMEOUT")
            try:
                message = parent.recv()
            except (EOFError, OSError):
                raise ExtractionFailed("PDF_EXTRACTION_CRASHED") from None
        finally:
            parent.close()
            if proc.is_alive():
                proc.kill()
            proc.join(5)
        if not isinstance(message, tuple) or not message:
            raise ExtractionFailed("PDF_EXTRACTION_CRASHED")
        if message[0] == "error":
            code = (
                message[1] if len(message) > 1 and message[1] in FAILURE_CODES else "PDF_UNREADABLE"
            )
            raise ExtractionFailed(code)
        _, pages, count = message[:3]
        layout = message[3] if len(message) == 4 else None
        return ExtractedText(pages=list(pages), page_count=int(count), layout=layout)

    return run


def default_extractor() -> Extractor:
    from app.core.config import get_settings

    s = get_settings()
    return isolated_pdf_extractor(
        max_pages=s.transcript_max_pages,
        max_chars=s.transcript_max_text_chars,
        timeout_seconds=s.transcript_extraction_timeout_seconds,
    )


__all__ = [
    "FAILURE_CODES",
    "PDF_MAGIC",
    "ExtractedText",
    "ExtractionFailed",
    "Extractor",
    "check_document",
    "default_extractor",
    "isolated_pdf_extractor",
]
