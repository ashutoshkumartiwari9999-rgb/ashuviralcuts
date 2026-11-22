"""Private, temporary yt-dlp cookie handling for YouTube imports."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import tempfile
import threading
import time
from urllib.parse import urlparse


MAX_COOKIE_BYTES = 1024 * 1024
_LOCK = threading.RLock()
LOGIN_COOKIE = "LOGIN_INFO"
AUTH_SIGNATURE_COOKIES = ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID")


def is_youtube_url(url: str) -> bool:
    host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def _active_youtube_cookie_names(text: str, now: float | None = None) -> set[str]:
    """Read cookie names only; never return or log cookie values."""
    timestamp = time.time() if now is None else now
    names = set()
    for line in text.splitlines():
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain = fields[0].removeprefix("#HttpOnly_").lower().lstrip(".")
        if not (domain == "youtube.com" or domain.endswith(".youtube.com") or domain == "youtu.be"):
            continue
        try:
            expiry = int(fields[4])
        except ValueError:
            continue
        if expiry == 0 or expiry > timestamp:
            names.add(fields[5])
    return names


def _missing_auth_cookie_groups(names: set[str]) -> list[str]:
    missing = []
    if LOGIN_COOKIE not in names:
        missing.append(LOGIN_COOKIE)
    if not names.intersection(AUTH_SIGNATURE_COOKIES):
        missing.append("one of " + ", ".join(AUTH_SIGNATURE_COOKIES))
    return missing


def _missing_auth_message(missing: list[str]) -> str:
    return (
        "The cookies file is missing active YouTube sign-in cookie(s): "
        + " and ".join(missing)
        + ". These cookie names are required for yt-dlp to recognize a signed-in session; "
        "cookie values are never shown. Export a complete fresh Netscape cookies.txt while signed in to YouTube."
    )


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
    active_youtube_cookie_names = set()
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
        is_youtube_cookie = domain == "youtube.com" or domain.endswith(".youtube.com") or domain == "youtu.be"
        youtube_cookie |= is_youtube_cookie
        if is_youtube_cookie and (fields[4] == "0" or int(fields[4]) > time.time()):
            active_youtube_cookie_names.add(fields[5])

    if not has_cookie or not youtube_cookie:
        raise ValueError("The cookies file must contain at least one YouTube cookie.")
    if not active_youtube_cookie_names:
        raise ValueError("All YouTube cookies in this file have expired. Export a fresh cookies.txt while signed in to YouTube.")
    missing = _missing_auth_cookie_groups(active_youtube_cookie_names)
    if missing:
        raise ValueError(_missing_auth_message(missing))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def cookie_path(data_dir: Path) -> Path:
    return Path(data_dir) / ".youtube-cookies.txt"


def has_cookies(data_dir: Path) -> bool:
    path = cookie_path(data_dir)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def cookie_state(data_dir: Path | None, now: float | None = None) -> str:
    """Return only non-sensitive cookie availability/expiry metadata."""
    if data_dir is None:
        return "unknown"
    path = cookie_path(data_dir)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return "missing"
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "unavailable"
    timestamp = time.time() if now is None else now
    youtube_expiries = []
    active_youtube_cookie_names = set()
    for line in text.splitlines():
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        domain = fields[0].removeprefix("#HttpOnly_").lower().lstrip(".")
        if domain == "youtube.com" or domain.endswith(".youtube.com") or domain == "youtu.be":
            try:
                expiry = int(fields[4])
            except ValueError:
                continue
            # Expiry 0 is a browser-session cookie; it has no fixed timestamp.
            if expiry == 0 or expiry > timestamp:
                active_youtube_cookie_names.add(fields[5])
            else:
                youtube_expiries.append(expiry)
    if active_youtube_cookie_names:
        return "incomplete" if _missing_auth_cookie_groups(active_youtube_cookie_names) else "available"
    return "expired" if youtube_expiries else "unavailable"


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
            raise RuntimeError(user_error(error, url, data_dir)) from None
    finally:
        if temp_cookie:
            temp_cookie.unlink(missing_ok=True)


def user_error(error: BaseException, url: str, data_dir: Path | None = None) -> str:
    """Do not expose extractor internals for YouTube; show actionable cookie guidance."""
    if not is_youtube_url(url):
        return str(error)[-700:]
    message = str(error).lower()
    if any(term in message for term in ("sign in to confirm", "not a bot", "cookies", "login required", "authentication", "confirm your age")):
        status = cookie_state(data_dir)
        if status == "missing":
            return "YouTube needs a signed-in browser session, but no cookies are configured on this server. Sign in to YouTube, export a fresh Netscape-format cookies.txt, and upload it under Bring your video → YouTube cookies."
        if status == "expired":
            return "The saved YouTube cookies have expired. Sign in to YouTube again, export a fresh Netscape-format cookies.txt, and replace them under Bring your video → YouTube cookies."
        if status == "incomplete":
            try:
                text = cookie_path(data_dir).read_text(encoding="utf-8") if data_dir is not None else ""
                missing = _missing_auth_cookie_groups(_active_youtube_cookie_names(text))
            except (OSError, UnicodeError):
                missing = []
            if missing:
                return _missing_auth_message(missing)
            return "The saved YouTube cookies are missing active sign-in cookies. Export a complete fresh Netscape cookies.txt while signed in to YouTube."
        if status == "unavailable":
            return "The saved YouTube cookies are unreadable or invalid. Export a fresh Netscape-format cookies.txt while signed in to YouTube and replace them under Bring your video → YouTube cookies."
        return (
            "The saved YouTube cookie file contains the expected sign-in cookie names, but YouTube rejected the session. "
            "The cookies may have been rotated while the browser tab stayed open, revoked, or otherwise invalid. "
            "Export a fresh Netscape cookies.txt from a newly signed-in YouTube session and replace it under "
            "Bring your video → YouTube cookies. Cookie values are never shown."
        )
    return "YouTube could not download this video. Check that the link is public and try again. If it requires sign-in, configure a valid signed-in YouTube cookies.txt in Bring your video → YouTube cookies."
