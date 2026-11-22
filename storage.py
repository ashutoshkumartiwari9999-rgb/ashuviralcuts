"""Quota-aware storage helpers. User media is never automatically removed."""
from __future__ import annotations

import os
import shutil
import json
import time
from pathlib import Path

MIB = 1024 * 1024
DEFAULT_BUDGET = 800 * MIB
DEFAULT_RESERVE = 96 * MIB
DEFAULT_UPLOAD_LIMIT = 300 * MIB


class StorageLimitError(Exception):
    """Raised before an operation could consume the safety reserve or quota."""


class _RequestTooLarge(Exception):
    pass


class UploadRequestLimitMiddleware:
    """Reject oversized multipart bodies before Starlette spools them to disk."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/upload":
            limit = upload_limit_bytes() + 2 * MIB
            too_large_message = f"Upload request exceeds the {upload_limit_bytes() // MIB} MB file limit."
        elif path == "/youtube-cookies":
            limit = MIB + 64 * 1024
            too_large_message = "The cookies.txt file must be 1 MB or smaller."
        elif path == "/upload-asset":
            query = scope.get("query_string", b"").decode("latin-1")
            kind = "image" if "kind=image" in query else "audio"
            limit = (25 if kind == "image" else 300) * MIB + 2 * MIB
            too_large_message = "Uploaded style asset exceeds its size limit."
        else:
            return await self.app(scope, receive, send)

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            announced = int(headers.get(b"content-length", b"0"))
        except ValueError:
            announced = 0
        if announced > limit:
            body = json.dumps({"detail": too_large_message}).encode()
            await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode()), (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY")]})
            await send({"type": "http.response.body", "body": body})
            return

        received = 0
        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestTooLarge()
            return message

        response_started = False
        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestTooLarge:
            if not response_started:
                body = json.dumps({"detail": too_large_message}).encode()
                await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode()), (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY")]})
                await send({"type": "http.response.body", "body": body})


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def budget_bytes() -> int:
    return _env_int("NEONCLIPPING_STORAGE_BUDGET_MB", 800, 128, 1024) * MIB


def reserve_bytes() -> int:
    return _env_int("NEONCLIPPING_STORAGE_RESERVE_MB", 96, 32, 512) * MIB


def upload_limit_bytes() -> int:
    # Leave room for source, generated clips, and working files on a 1 GB disk.
    return _env_int("NEONCLIPPING_MAX_UPLOAD_MB", 300, 10, 700) * MIB


def used_bytes(root: Path) -> int:
    total = 0
    for path in Path(root).rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def summary(root: Path) -> dict:
    root = Path(root)
    used = used_bytes(root)
    try:
        physical_free = shutil.disk_usage(root).free
    except OSError:
        physical_free = None
    budget = budget_bytes()
    return {
        "used_bytes": used,
        "budget_bytes": budget,
        "reserve_bytes": reserve_bytes(),
        "physical_free_bytes": physical_free,
        "upload_limit_bytes": upload_limit_bytes(),
        "percent": min(100, round(100 * used / budget)) if budget else 100,
        "user_media_cleanup": False,
    }


def ensure_capacity(root: Path, additional_bytes: int = 0) -> None:
    root = Path(root)
    additional_bytes = max(0, int(additional_bytes))
    stats = summary(root)
    reserve = reserve_bytes()
    if stats["used_bytes"] + additional_bytes + reserve > budget_bytes():
        raise StorageLimitError(
            "Storage is near its configured limit. Download your clips or remove files yourself, "
            "then try again. Existing media is never deleted automatically."
        )
    if stats["physical_free_bytes"] is not None and stats["physical_free_bytes"] < additional_bytes + reserve:
        raise StorageLimitError(
            "There is not enough free disk space for this operation. Free space and try again."
        )


def estimate_output_bytes(duration: float, resolution: int, width: int, height: int) -> int:
    """Conservative H.264+AAC output estimate used for preflight checks."""
    rates = {720: 7_000_000, 1080: 16_000_000, 2160: 48_000_000}
    base_rate = rates.get(int(resolution))
    if base_rate is None:
        raise ValueError("Unsupported output resolution.")
    pixels = max(1, int(width)) * max(1, int(height))
    rate = base_rate * pixels / (1080 * 1920) + 256_000
    return max(0, int(max(0.0, float(duration)) * rate / 8))


def cleanup_temporary(root: Path, older_than_hours: int = 24) -> dict:
    """Remove only stale transport/intermediate files and preview frame caches."""
    root = Path(root)
    cutoff = time.time() - max(1, older_than_hours) * 3600
    removed_files = 0
    removed_bytes = 0
    removed_dirs = 0
    temporary_suffixes = (".part", ".ytdl", ".tmp", ".upload")

    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_mtime >= cutoff:
                continue
            if path.name.endswith(temporary_suffixes) or path.name.startswith(("lr_", ".yt-cookie-")):
                removed_bytes += path.stat().st_size
                path.unlink()
                removed_files += 1
        except OSError:
            continue

    # Preview JPEGs can be regenerated. Do not prune sources, exports, captions,
    # assets, creator settings, or any other user-facing data.
    for path in sorted(root.glob("*-preview"), key=lambda p: len(p.parts), reverse=True):
        try:
            if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                shutil.rmtree(path)
                removed_bytes += size
                removed_dirs += 1
        except OSError:
            continue

    return {"removed_files": removed_files, "removed_preview_folders": removed_dirs, "freed_bytes": removed_bytes}
