"""Private, temporary yt-dlp cookie handling for YouTube imports."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import tempfile
import threading
from urllib.parse import urlparse


MAX_COOKIE_BYTES = 1024 * 1024
_LOCK = threading.RLock()


def is_youtube_url(url: str) -> bool:
    host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def validate_cookie_text(raw: bytes) -> str:
    if not raw:
        raise ValueError("Choose a non-empty Netscape cookies.txt file.")
    if len(raw) > MAX_COOKIE_BYTES:
        raise ValueError("The cookies.txt file must be 1 MB or smaller.")
    if b"\x00" in raw:
        raise ValueError("The cookies.txt file has an invalid format.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Save cookies.txt as UTF-8 Netscape format, then upload it again.") from error

    youtube_cookie = False
    has_cookie = False
    for line in text.splitlines():
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError("This is not a Netscape cookies.txt file. Export cookies in Netscape format.")
        domain = fields[0].removeprefix("#HttpOnly_").lower().lstrip(".")
        if fields[1] not in ("TRUE", "FALSE") or fields[3] not in ("TRUE", "FALSE"):
            raise ValueError("This cookies.txt file has invalid Netscape cookie fields.")
        if not fields[4].isdigit():
            raise ValueError("This cookies.txt file has an invalid expiration field.")
        has_cookie = True
        youtube_cookie |= domain == "youtube.com" or domain.endswith(".youtube.com") or domain == "youtu.be"

    if not has_cookie or not youtube_cookie:
        raise ValueError("The cookies file must contain at least one YouTube cookie.")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def cookie_path(data_dir: Path) -> Path:
    return Path(data_dir) / ".youtube-cookies.txt"


def has_cookies(data_dir: Path) -> bool:
    path = cookie_path(data_dir)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def save_cookies(data_dir: Path, raw: bytes) -> None:
    text = validate_cookie_text(raw)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    destination = cookie_path(data_dir)
    fd, temp_name = tempfile.mkstemp(prefix=".youtube-cookies-", suffix=".upload", dir=data_dir)
    temp = Path(temp_name)
    try:
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
        with _LOCK:
            temp.replace(destination)
            try:
                os.chmod(destination, 0o600)
            except OSError:
                pass
    finally:
        temp.unlink(missing_ok=True)


def remove_cookies(data_dir: Path) -> None:
    with _LOCK:
        cookie_path(data_dir).unlink(missing_ok=True)


@contextmanager
def youtube_dl(url: str, data_dir: Path, options: dict):
    """Create a private per-job cookie copy and always remove it after yt-dlp exits."""
    temp_cookie = None
    configured = dict(options)
    try:
        if is_youtube_url(url):
            source = cookie_path(data_dir)
            with _LOCK:
                if source.is_file():
                    fd, name = tempfile.mkstemp(prefix=".yt-cookie-", suffix=".txt", dir=data_dir)
                    temp_cookie = Path(name)
                    try:
                        os.chmod(temp_cookie, 0o600)
                    except OSError:
                        pass
                    with os.fdopen(fd, "wb") as output:
                        output.write(source.read_bytes())
            if temp_cookie:
                configured["cookiefile"] = str(temp_cookie)
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(configured) as downloader:
                yield downloader
        except Exception as error:
            raise RuntimeError(user_error(error, url)) from None
    finally:
        if temp_cookie:
            temp_cookie.unlink(missing_ok=True)


def user_error(error: BaseException, url: str) -> str:
    """Do not expose extractor internals for YouTube; show actionable cookie guidance."""
    if not is_youtube_url(url):
        return str(error)[-700:]
    message = str(error).lower()
    if any(term in message for term in ("sign in to confirm", "not a bot", "cookies", "login required", "authentication", "confirm your age")):
        return "YouTube needs a signed-in browser session. In Bring your video → YouTube cookies, upload a fresh Netscape-format cookies.txt exported while signed in to YouTube, then retry. Keep that file private."
    return "YouTube could not download this video. Check that the link is public and try again. If YouTube asks you to sign in, upload a fresh cookies.txt in Bring your video → YouTube cookies."
