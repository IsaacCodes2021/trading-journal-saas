"""Where the bytes of a person's files live (doc 03 section 6), behind one small interface.

The database holds the row (who, which trade, the name, the size); this holds the bytes. Today there is one store, a
folder on the server's disk, which is what a development machine and a host with a persistent disk need. The design's
production store is a private bucket (Cloudflare R2): it is another class with the same four methods, chosen in
`api/app.py`, and nothing above this file changes.

A key is a short relative path such as `u/<user id>/<random>.png`. Keys are made by the server, never by a request, and
`LocalFileStore` still refuses any key that could leave its folder.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

_KEY = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")
MAX_KEY_LENGTH = 120  # a real key is about 77 characters; the limit also keeps a path under Windows' 260


class BadKey(ValueError):
    """A key that is not the plain, relative, lower-case path the server makes."""


class FileMissing(LookupError):
    """The store has nothing under this key."""


@dataclass(frozen=True)
class StoredFile:
    key: str
    size: int
    modified: datetime


class FileStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def list(self, prefix: str = "") -> Iterator[StoredFile]: ...


def check_key(key: str) -> str:
    if len(key) > MAX_KEY_LENGTH or not _KEY.fullmatch(key) or ".." in key:
        raise BadKey(f"not a file key: {key!r}")
    return key


class LocalFileStore:
    """Files in a folder. A write goes to a temporary file in the same folder and is renamed into place, so a reader never
    sees half a file and a crash leaves no partial one under a real key."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / check_key(key)).resolve()
        if self.root not in path.parents:  # defence in depth: the check above already refuses what could escape
            raise BadKey(f"not a file key: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".part-")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise FileMissing(key) from None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        """Remove every file under a folder-like prefix (`u/<user id>/`), and the folder. Returns how many files."""
        folder = self._path(prefix.rstrip("/"))
        if not folder.is_dir():
            return 0
        count = sum(1 for p in folder.rglob("*") if p.is_file())
        for path in sorted(folder.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        folder.rmdir()
        return count

    def list(self, prefix: str = "") -> Iterator[StoredFile]:
        """Every file under the prefix, with its size and modification time (what the orphan job reads)."""
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if not base.is_dir():
            return
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.startswith(".part-"):
                status = path.stat()
                yield StoredFile(
                    key=path.relative_to(self.root).as_posix(),
                    size=status.st_size,
                    modified=datetime.fromtimestamp(status.st_mtime, UTC),
                )
