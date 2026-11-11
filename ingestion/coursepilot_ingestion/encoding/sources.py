"""Archive official requirement sources with provenance (encoding pipeline, step 1).

One capture = the exact bytes an official page returned to a plain HTTP GET,
saved content-addressed as `<sha256>.<ext>` under ARCHIVE_ROOT (gitignored,
like data/raw/), plus one manifest entry (committed) recording:

    requested URL, final URL after redirects, HTTP status, UTC retrieval time,
    sha256 and byte count of the SAVED bytes, content type, representation,
    and the archive path.

Nothing here parses requirements. A capture that fails (403, timeout) is
recorded as a failure, never replaced silently by another representation:
an extraction-only capture (a reader/text view) must be labelled as such by
whoever adds it (`representation`).

Quotes in an encoding are checked against `source_text()` of the capture they
cite, so a quote that is not on the archived page is caught by validation.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import pathlib
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parents[3]
#: Captures (bytes). Gitignored: large, reproducible from the URL + hash record.
ARCHIVE_ROOT = ROOT / "data" / "source-archive"
#: Legacy capture roots that encodings may also cite (read-only here).
LEGACY_ROOTS = (ROOT / "data" / "raw",)
#: The committed manifest of every capture an encoding may cite.
MANIFEST = ROOT / "data" / "programs" / "rutgers" / "sources.json"
#: A generic client name: no personal contact detail is sent to any site.
USER_AGENT = "CoursePilot/0.1 (Rutgers academic planning; public catalog archival)"
POLITE_DELAY = 1.5


@dataclass(frozen=True, slots=True)
class Capture:
    id: str                    # short stable id an encoding cites, e.g. "eng-degree-req"
    requested_url: str
    final_url: str | None
    status: int | None
    retrieved_at: str          # UTC ISO-8601
    sha256: str | None
    bytes: int | None
    content_type: str | None
    representation: str        # "raw_html" | "raw_pdf" | "extracted_text" | "failed"
    archive_path: str | None   # repository-relative
    note: str | None = None


def load_manifest(path: pathlib.Path = MANIFEST) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {c["id"]: c for c in json.loads(path.read_text("utf-8"))["captures"]}


def save_manifest(captures: dict[str, dict], path: pathlib.Path = MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "_README": [
            ("Every official source an encoding cites. Bytes live under data/source-archive/ "
            "(gitignored); this file is the provenance record: URL, UTC time, exact saved-byte "
            "sha256, representation. Written by coursepilot_ingestion.encoding.sources."),
            ("A capture is evidence of what a page said when fetched, not of its effective date "
            "or the cohorts it governs."),
        ],
        "captures": [captures[k] for k in sorted(captures)],
    }
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", "utf-8")


def _ext(content_type: str | None, url: str) -> str:
    ct = (content_type or "").lower()
    if "pdf" in ct or url.lower().endswith(".pdf"):
        return "pdf"
    if "html" in ct:
        return "html"
    return "txt"


def archive_url(capture_id: str, url: str, *, client=None, now: datetime | None = None,
                archive_root: pathlib.Path = ARCHIVE_ROOT) -> Capture:
    """GET `url` once (plain HTTP, no browser) and archive the exact bytes."""
    import httpx

    retrieved = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT})
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return Capture(capture_id, url, None, None, retrieved, None, None, None, "failed", None,
                       note=f"{type(exc).__name__}")
    finally:
        if own:
            client.close()
    if response.status_code != 200:
        return Capture(capture_id, url, str(response.url), response.status_code, retrieved, None,
                       None, response.headers.get("content-type"), "failed", None,
                       note=f"HTTP {response.status_code}")
    data = response.content
    digest = hashlib.sha256(data).hexdigest()
    ext = _ext(response.headers.get("content-type"), url)
    archive_root.mkdir(parents=True, exist_ok=True)
    target = archive_root / f"{digest}.{ext}"
    if not target.exists():
        target.write_bytes(data)
    return Capture(capture_id, url, str(response.url), 200, retrieved, digest, len(data),
                   response.headers.get("content-type"), f"raw_{ext}" if ext != "txt" else
                   "raw_text", _rel(target))


def _rel(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def archive_many(requests: list[tuple[str, str]], *, delay: float = POLITE_DELAY,
                 manifest: pathlib.Path = MANIFEST) -> list[Capture]:
    """Archive (id, url) pairs sequentially with a polite pause; update the manifest."""
    captures = load_manifest(manifest)
    out = []
    for i, (cid, url) in enumerate(requests):
        if i:
            time.sleep(delay)
        cap = archive_url(cid, url)
        captures[cid] = asdict(cap)
        out.append(cap)
    save_manifest(captures, manifest)
    return out


# --------------------------------------------------------------------------
# reading a capture back
# --------------------------------------------------------------------------

_DROP = re.compile(r"<(script|style|noscript|svg|head)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_BLOCK = re.compile(r"</?(p|div|li|tr|td|th|h[1-6]|br|ul|ol|table|section|article)\b[^>]*>", re.IGNORECASE)


def html_text(page: str) -> str:
    """Visible text of an HTML page; catalog (Coursedog) pages use their prose blocks."""
    from coursepilot_ingestion.catalog_registry import page_prose

    try:
        prose = page_prose(page)
    except Exception:       # noqa: BLE001 - not a Coursedog page
        prose = ""
    body = _BLOCK.sub(" \n ", _DROP.sub(" ", page))
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"\s+", " ", text).strip()
    return (prose + " \n " + text) if prose else text


def resolve(capture: dict) -> pathlib.Path | None:
    if not capture.get("archive_path"):
        return None
    path = ROOT / capture["archive_path"]
    return path if path.exists() else None


def source_text(capture: dict) -> str | None:
    """The capture's readable text, or None when its bytes are not present locally."""
    path = resolve(capture)
    if path is None:
        return None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != capture.get("sha256"):
        raise ValueError(f"{capture['id']}: archived bytes do not match the recorded sha256")
    if path.suffix == ".pdf":
        return _pdf_text(path)
    decoded = data.decode("utf-8", errors="replace")
    return html_text(decoded) if path.suffix == ".html" else decoded


def _pdf_text(path: pathlib.Path) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    return " ".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


__all__ = [
    "ARCHIVE_ROOT",
    "MANIFEST",
    "USER_AGENT",
    "Capture",
    "archive_many",
    "archive_url",
    "html_text",
    "load_manifest",
    "resolve",
    "save_manifest",
    "source_text",
]
