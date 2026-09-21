"""Fetcher for Rutgers catalog program pages.

Network in, raw HTML out. No parsing here - keeping fetch and parse separate
is what lets an archived page be reprocessed months later after a parser fix,
when re-fetching would return a different (or missing) catalog year.

The catalog is a Nuxt SPA whose data is embedded server-side in the HTML, so a
plain HTTP GET is sufficient. No browser engine.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from coursepilot_ingestion.sources.catalog import USER_AGENT, CatalogQuery

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CatalogFetchResult:
    html: str
    url: str
    retrieved_at: datetime
    content_hash: str
    archive_path: pathlib.Path | None
    from_cache: bool

    @property
    def size_bytes(self) -> int:
        return len(self.html.encode("utf-8"))


class CatalogFetcher:
    def __init__(self, cache_dir: pathlib.Path, *, timeout: float = 120.0) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout

    def _archive_path(self, query: CatalogQuery) -> pathlib.Path:
        return self.cache_dir / query.archive_name

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _get(self, url: str) -> httpx.Response:
        with httpx.Client(
            timeout=self.timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = client.get(url)
        response.raise_for_status()
        return response

    def fetch(self, query: CatalogQuery, *, use_cache: bool = True) -> CatalogFetchResult:
        archive = self._archive_path(query)

        if use_cache and archive.exists():
            html = archive.read_text(encoding="utf-8")
            logger.info("using archived catalog page %s", archive.name)
            return CatalogFetchResult(
                html=html,
                url=query.url,
                # The archive's mtime is the real retrieval time; using "now"
                # would backdate provenance to the wrong moment.
                retrieved_at=datetime.fromtimestamp(archive.stat().st_mtime, tz=UTC),
                content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(),
                archive_path=archive,
                from_cache=True,
            )

        logger.info("fetching %s", query.url)
        response = self._get(query.url)
        html = response.text

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        archive.write_text(html, encoding="utf-8")
        logger.info("archived %s bytes -> %s", f"{len(html):,}", archive.name)

        return CatalogFetchResult(
            html=html,
            url=str(response.url),
            retrieved_at=datetime.now(UTC),
            content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            archive_path=archive,
            from_cache=False,
        )
