"""
File storage abstraction (Sec. 6/13 + no-paid-services). Local-disk implementation only for
now - the interface is narrow enough (save bytes, read bytes, delete) that an S3-compatible
(e.g. self-hosted MinIO) backend can be added later without touching callers, selected by
settings.storage_backend the same way AI providers are (D007's pattern applied here too).

Security note: `safe_relative_path` below is the ONLY function that turns caller-supplied
identifiers into filesystem paths, specifically to centralize path-traversal prevention
(Sec. 10) - callers must not build storage paths by string-concatenating user input themselves.
"""
import os
import uuid
from pathlib import Path

from app.core.config import get_settings


class StorageError(Exception):
    pass


def safe_relative_path(*, org_id: uuid.UUID, document_id: uuid.UUID, filename: str, version_number: int = 1) -> str:
    """Builds a storage-relative path from trusted UUIDs plus a sanitized filename.
    The filename is never used for directory traversal - only its extension is kept, and
    even that is validated against a small allowlist by the caller (services/knowledge.py).

    `version_number` is included in the path so that re-uploading a document (a new
    DocumentVersion, see services/knowledge.py:create_document_version) writes to a NEW file
    rather than silently overwriting the previous version's file - each DocumentVersion row's
    storage_path must keep pointing at that version's actual content. This was a real bug
    caught while implementing versioning: the original version-less scheme meant a second
    upload of a same-extension file would overwrite the first version's bytes on disk while
    its DB row still claimed to point at "version 1"."""
    ext = Path(filename).suffix.lower()
    if ".." in ext or "/" in ext or "\\" in ext:
        ext = ""
    return f"{org_id}/{document_id}/v{version_number}{ext}"


class LocalFileStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self._root = Path(settings.local_storage_path)
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        resolved = (self._root / relative_path).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            # Defense in depth against a relative_path that somehow contains "..' despite
            # safe_relative_path - reject rather than silently clamping.
            raise StorageError("Resolved path escapes storage root")
        return resolved

    def save(self, relative_path: str, content: bytes) -> None:
        path = self._resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    def read(self, relative_path: str) -> bytes:
        path = self._resolve(relative_path)
        if not path.exists():
            raise StorageError(f"No such file: {relative_path}")
        with open(path, "rb") as f:
            return f.read()

    def delete(self, relative_path: str) -> None:
        path = self._resolve(relative_path)
        if path.exists():
            os.remove(path)


def get_storage() -> LocalFileStorage:
    settings = get_settings()
    if settings.storage_backend != "local":
        raise StorageError(f"Unsupported storage backend: {settings.storage_backend}")
    return LocalFileStorage()
