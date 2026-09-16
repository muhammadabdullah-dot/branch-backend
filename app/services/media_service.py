"""Pictures for Items and Parties, stored as files under `settings.media_dir`.

The database only holds the relative path. Files are served back through authenticated endpoints
(never a public static folder), because a Party picture can be a customer's shop or a CNIC scan.
"""
import secrets
from pathlib import Path

from app.core.config import settings

MAX_BYTES = 3 * 1024 * 1024

# Recognised by content, not by the file name the browser sent — a renamed .exe must not become
# a "picture".
_SIGNATURES: list[tuple[bytes, int, str, str]] = [
    (b"\xff\xd8\xff", 0, "jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", 0, "png", "image/png"),
    (b"WEBP", 8, "webp", "image/webp"),
]
_CONTENT_TYPES = {ext: ctype for _, _, ext, ctype in _SIGNATURES}


class MediaError(Exception):
    def __init__(self, message: str):
        self.message = message


def _root() -> Path:
    return Path(settings.media_dir).resolve()


def _kind_of(content: bytes) -> str | None:
    for signature, offset, ext, _ in _SIGNATURES:
        if content[offset:offset + len(signature)] == signature:
            if ext == "webp" and content[:4] != b"RIFF":
                continue
            return ext
    return None


def save_picture(folder: str, owner_id: str, content: bytes, replacing: str | None = None, max_bytes: int = MAX_BYTES) -> str:
    """Store the picture and return its path relative to the media root. Removes the previous
    picture, if any, only after the new one is safely written."""
    if not content:
        raise MediaError("The picture file is empty.")
    if len(content) > max_bytes:
        raise MediaError(f"That picture is {len(content) / 1024 / 1024:.1f} MB. Use one under {max_bytes // (1024 * 1024)} MB.")
    ext = _kind_of(content)
    if not ext:
        raise MediaError("That isn't a JPEG, PNG or WebP picture.")
    relative = f"{folder}/{owner_id}-{secrets.token_hex(4)}.{ext}"
    target = _root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    if replacing:
        remove_picture(replacing)
    return relative


def remove_picture(relative: str | None) -> None:
    if not relative:
        return
    path = (_root() / relative).resolve()
    if _root() in path.parents and path.exists():
        path.unlink()


def picture_file(relative: str | None) -> tuple[Path, str] | None:
    """(path, content type) for a stored picture, or None when there isn't one on disk."""
    if not relative:
        return None
    path = (_root() / relative).resolve()
    if _root() not in path.parents or not path.exists():
        return None
    return path, _CONTENT_TYPES.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
