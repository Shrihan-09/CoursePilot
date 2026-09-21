"""Fetcher: network in, raw bytes out.

Responsibilities, and nothing else:
  * make the HTTP request (politely, with retries)
  * archive the raw response verbatim
  * compute a content hash for change detection

It does NOT parse. Keeping fetch and parse separate is what makes it possible
to re-run the parser against an archived payload months later, after the
source has changed and re-fetching the original is impossible.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from coursepilot_ingestion.sources.soc import USER_AGENT, SocQuery

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Raw payload plus everything needed to record its provenance."""

    content: bytes
    url: str
    retrieved_at: datetime
    content_hash: str
    archive_path: pathlib.Path | None
    from_cache: bool

    @property
    def size_bytes(self) -> int:
        return len(self.content)


class SocFetcher:
    """Fetches SOC payloads and archives them.

    `cache_dir` doubles as the raw archive. On a cache hit we skip the network
    entirely — the SOC courses payload is ~21 MB, and re-downloading it on
    every development run is both slow and discourteous to Rutgers.
    """

    def __init__(
        self,
        cache_dir: pathlib.Path,
        *,
        timeout: float = 180.0,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.user_agent = user_agent

    def _archive_path(self, query: SocQuery) -> pathlib.Path:
        return self.cache_dir / f"soc_courses_{query.year}_{query.term}_{query.campus}.json"

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _get(self, url: str) -> httpx.Response:
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        ) as client:
            response = client.get(url)
        response.raise_for_status()
        return response

    def fetch_courses(self, query: SocQuery, *, use_cache: bool = True) -> FetchResult:
        archive = self._archive_path(query)

        if use_cache and archive.exists():
            content = archive.read_bytes()
            logger.info("using archived payload %s (%s bytes)", archive, f"{len(content):,}")
            return FetchResult(
                content=content,
                url=query.courses_url,
                # The archive's mtime is when we actually retrieved it. Using
                # "now" here would silently backdate provenance to the wrong
                # moment and make staleness undetectable.
                retrieved_at=datetime.fromtimestamp(archive.stat().st_mtime, tz=UTC),
                content_hash=hashlib.sha256(content).hexdigest(),
                archive_path=archive,
                from_cache=True,
            )

        if not query.is_verified:
            logger.warning(
                "%s uses term/campus codes this project has not verified; "
                "results may be empty or unexpected. See docs/DATA_SOURCES.md",
                query,
            )

        logger.info("fetching %s", query.courses_url)
        response = self._get(query.courses_url)
        content = response.content

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(content)
        logger.info("archived %s bytes -> %s", f"{len(content):,}", archive)

        return FetchResult(
            content=content,
            url=str(response.url),
            retrieved_at=datetime.now(UTC),
            content_hash=hashlib.sha256(content).hexdigest(),
            archive_path=archive,
            from_cache=False,
        )
