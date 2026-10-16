"""Where an uploaded transcript lives while it is being read (Phase 7.0).

A deliberately small seam, not an infrastructure decision. CoursePilot does
not RETAIN uploaded documents: a document is stored, extracted, and deleted
in the same request, success or failure. The seam exists so that a later
asynchronous extractor (or an encrypted object store) can replace the local
implementation without the import service changing - and so the lifecycle
"stored -> deleted" is explicit and testable rather than implied.

## Local storage rules

  * The key is server-generated (`uuid4().hex`). No client value - there is
    no file name; the document arrives as the request body - ever becomes
    part of a path, so traversal is impossible by construction, and the
    resolved path is still checked to sit under the base directory.
  * Files are created exclusively (`O_EXCL`) with mode 0600.
  * The base directory defaults to `data/uploads/transcripts/`, which is
    git-ignored.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import threading
import uuid
from typing import Protocol

_KEY = frozenset("0123456789abcdef")


class StorageError(Exception):
    pass


class TranscriptStorage(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def stored_keys(self) -> list[str]: ...


def _check_key(key: str) -> str:
    if len(key) != 32 or not set(key) <= _KEY:
        raise StorageError("invalid storage key")
    return key


class LocalTranscriptStorage:
    def __init__(self, base_dir: str | os.PathLike) -> None:
        self.base = pathlib.Path(base_dir).resolve()

    def _path(self, key: str) -> pathlib.Path:
        path = (self.base / _check_key(key)).resolve()
        if path.parent != self.base:
            raise StorageError("storage path escapes the base directory")
        return path

    def put(self, data: bytes) -> str:
        self.base.mkdir(parents=True, exist_ok=True)
        key = uuid.uuid4().hex
        fd = os.open(self._path(key), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(key).unlink()

    def stored_keys(self) -> list[str]:
        if not self.base.exists():
            return []
        return sorted(p.name for p in self.base.iterdir()
                      if p.is_file() and len(p.name) == 32 and set(p.name) <= _KEY)


class MemoryTranscriptStorage:
    """Tests and single-process development."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def put(self, data: bytes) -> str:
        key = uuid.uuid4().hex
        with self._lock:
            self._data[key] = bytes(data)
        return key

    def get(self, key: str) -> bytes:
        with self._lock:
            return self._data[_check_key(key)]

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(_check_key(key), None)

    def stored_keys(self) -> list[str]:
        with self._lock:
            return sorted(self._data)


_default: TranscriptStorage | None = None


def get_transcript_storage() -> TranscriptStorage:
    """The configured storage (process-wide). A FastAPI dependency, so tests
    substitute it without touching settings."""
    global _default
    if _default is None:
        from app.core.config import get_settings

        _default = LocalTranscriptStorage(get_settings().transcript_storage_dir)
    return _default


__all__ = ["LocalTranscriptStorage", "MemoryTranscriptStorage", "StorageError",
           "TranscriptStorage", "get_transcript_storage"]
