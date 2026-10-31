"""Receipt photos on the clinic PC (SHF-7): one file per content hash, nothing else.

A phone photo is evidence for the accountant, so it never leaves the clinic's network and never
replaces the event it belongs to: the event stays the truth and carries the photo's SHA-256 content
hash, and this store holds the bytes under that hash. Content addressing buys three things the clinic
needs: the same photo is stored once, a file can never be edited silently because its name would stop
matching, and a payload can name no path — only a 64-character hash — so no client input ever reaches
the filesystem as a path.

The store is dumb on purpose: it validates the body's size and hash, writes atomically, and answers
"is this hash here?". Who may upload or read a photo is the API's guard, not this module's.
"""

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

MAX_PHOTO_BYTES = 1024 * 1024
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_SUFFIX = ".jpg"


class PhotoError(ValueError):
    """The photo could not be stored or is not one this store holds."""


class PhotoTooLarge(PhotoError):
    """The body is empty or bigger than the clinic accepts for one receipt."""


class PhotoRejected(PhotoError):
    """The hash is not a photo hash this store holds."""


def is_photo_hash(value: object) -> bool:
    """Whether `value` is exactly a lowercase SHA-256 hex digest; the only thing a path may hold."""
    return isinstance(value, str) and SHA256_HEX.fullmatch(value) is not None


class PhotoStore:
    """The app's photo folder: `<dir>/<first two hex>/<hash>.jpg`, immutable and self-checking."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def save(self, data: bytes) -> str:
        """Store `data` under its own hash and return that hash; an existing file is never rewritten."""
        if not data or len(data) > MAX_PHOTO_BYTES:
            raise PhotoTooLarge(f"a receipt photo is 1..{MAX_PHOTO_BYTES} bytes")
        sha = hashlib.sha256(data).hexdigest()
        target = self._path(sha)
        if target.is_file():
            return sha
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".photo-", suffix=".part")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return sha

    def open_path(self, sha256: str) -> Path:
        """The file behind one hash, or `PhotoRejected` when it is malformed or absent."""
        target = self._path(sha256)
        if not target.is_file():
            raise PhotoRejected("photo not found")
        return target

    def exists(self, sha256: str) -> bool:
        """Whether this store holds the photo, without ever touching an unvalidated path."""
        return is_photo_hash(sha256) and self._path(sha256).is_file()

    def _path(self, sha256: object) -> Path:
        if not is_photo_hash(sha256):
            raise PhotoRejected("not a photo hash")
        assert isinstance(sha256, str)
        return self._directory / sha256[:2] / f"{sha256}{_SUFFIX}"


def copy_missing_photos(source_root: Path, target_root: Path) -> int:
    """Copy every photo the target does not hold yet; a missing source folder copies nothing.

    Photos are immutable and content-addressed, so a backup never has to rewrite one: the file is
    copied once, on the first run that sees it, and retention never deletes it — the event log still
    points at it (ADR-008, SHF-7).
    """
    if not source_root.is_dir():
        return 0
    copied = 0
    for source in sorted(source_root.rglob("*.jpg")):
        destination = target_root / source.relative_to(source_root)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    return copied
