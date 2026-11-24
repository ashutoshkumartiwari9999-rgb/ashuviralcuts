"""Private, temporary yt-dlp cookie handling for YouTube imports."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import logging
import os
import tempfile
import threading
import time
from urllib.parse import urlparse


MAX_COOKIE_BYTES = 1024 * 1024
_LOCK = threading.RLock()
LOGIN_COOKIE = "LOGIN_INFO"
AUTH_SIGNATURE_COOKIES = ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID")
_LOG = logging.getLogger(__name__)


def _classify_youtube_failure(message: str, logged_categories=()) -> str:
    """Prefer the terminal exception; use captured logger categories only as fallback."""
    lower = str(message).lower()
    if "po token" in lower or "po_token" in lower or "proof of origin" in lower:
        return "po_token"
    if any(term in lower for term in (
        "no supported javascript runtime", "javascript runtime could not be found",
        "javascript challenge", "signature/nsig extraction failed", "nsig extraction failed",
        "ejs challenge solver", "challenge solver script",
    )):
        return "javascript_runtime"
    if "429" in lower or "too many requests" in lower or "rate limit" in lower or "unusual traffic" in lower:
        return "rate_limited"
    if any(term in lower for term in (
        "not a bot", "automated requests", "automated traffic", "bot challenge",
    )):
        return "bot_challenge"
    if any(term in lower for term in ("sign in to confirm", "login required", "authentication", "confirm your age", "cookies are no longer valid")):
        return "authentication"
    if "403" in lower or "forbidden" in lower or "blocked" in lower:
        return "forbidden"
    if any(term in lower for term in ("video unavailable", "private video", "video has been removed", "404 not found")):
        return "unavailable"
    if any(term in lower for term in ("requested format is not available", "no video formats found", "no formats found")):
        return "no_formats"
    if any(term in lower for term in ("timed out", "timeout", "name or service not known", "temporary failure in name resolution", "connection reset", "connection refused", "ssl: certificate")):
        return "network"
    if "no module named 'yt_dlp'" in lower or "no module named \"yt_dlp\"" in lower:
        return "dependency"
    categories = set(logged_categories or ())
    for category in ("po_token", "javascript_runtime", "rate_limited", "bot_challenge", "authentication", "forbidden", "unavailable", "no_formats", "network", "dependency"):
        if category in categories:
            return category
    return "unknown"


class _SafeYtDlpLogger:
    """Capture only diagnostic categories; never store or emit yt-dlp messages."""
    def __init__(self):
        self.categories = set()

    def _inspect(self, message):
        category = _classify_youtube_failure(str(message))
        if category != "unknown":
            self.categories.add(category)

    def debug(self, message):
        self._inspect(message)

    def warning(self, message):
        self._inspect(message)

    def error(self, message):
        self._inspect(message)


class YoutubeDownloadFailure(RuntimeError):
    """Sanitized yt-dlp failure category for display in the job UI."""
    def __init__(self, category):
        self.youtube_category = category
        super().__init__("YouTube download failed")


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
    return cookie_state(data_dir) == "available"


def _stored_cookie_bytes(data_dir: Path) -> bytes | None:
    """Read local dev storage or decrypt the configured private remote object."""
    import youtube_cookie_store
    if youtube_cookie_store.configured():
        return youtube_cookie_store.get()
    path = cookie_path(data_dir)
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def cookie_state(data_dir: Path | None, now: float | None = None) -> str:
    """Return only non-sensitive cookie availability/expiry metadata."""
    if data_dir is None:
        return "unknown"
    try:
        raw = _stored_cookie_bytes(Path(data_dir))
        if raw is None or not raw:
            return "missing"
        text = raw.decode("utf-8")
    except (OSError, UnicodeError, ValueError, RuntimeError):
        import youtube_cookie_store
        return "storage_unavailable" if youtube_cookie_store.configured() else "unavailable"
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
    import youtube_cookie_store
    try:
        if youtube_cookie_store.configured():
            youtube_cookie_store.put(text.encode("utf-8"))
            # Do not leave a plaintext persistent copy on Render's ephemeral disk.
            destination.unlink(missing_ok=True)
            return
    except youtube_cookie_store.CookieStoreError as error:
        raise ValueError(str(error)) from None
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
    import youtube_cookie_store
    with _LOCK:
        if youtube_cookie_store.configured():
            try:
                youtube_cookie_store.delete()
            except youtube_cookie_store.CookieStoreError as error:
                raise RuntimeError(str(error)) from None
        cookie_path(data_dir).unlink(missing_ok=True)


@contextmanager
def youtube_dl(url: str, data_dir: Path, options: dict):
    """Try public YouTube access first; use a private cookie copy only on auth failure."""
    if is_youtube_url(url):
        yield _YoutubeRequest(url, Path(data_dir), dict(options))
        return
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(dict(options)) as downloader:
            yield downloader
    except Exception as error:
        raise RuntimeError(user_error(error, url, data_dir)) from None


class _YoutubeRequest:
    """Retry one yt-dlp operation with saved cookies only after sign-in is required."""
    def __init__(self, url: str, data_dir: Path, options: dict):
        self.url = url
        self.data_dir = data_dir
        self.options = options

    def _configured(self, diagnostics: _SafeYtDlpLogger, cookie_file: Path | None = None) -> dict:
        configured = dict(self.options)
        configured["logger"] = diagnostics
        configured["no_warnings"] = False
        deno_path = os.environ.get("NEONCLIPPING_DENO_PATH")
        if deno_path:
            configured.setdefault("js_runtimes", {"deno": {"path": deno_path}})
        if cookie_file is not None:
            configured["cookiefile"] = str(cookie_file)
        else:
            configured.pop("cookiefile", None)
        return configured

    def _invoke(self, method: str, args: tuple, kwargs: dict, cookie_file: Path | None = None):
        import yt_dlp
        diagnostics = _SafeYtDlpLogger()
        try:
            with yt_dlp.YoutubeDL(self._configured(diagnostics, cookie_file)) as downloader:
                return getattr(downloader, method)(*args, **kwargs)
        except Exception as error:
            category = _classify_youtube_failure(str(error), diagnostics.categories)
            # Deliberately log only safe metadata. Raw yt-dlp messages can contain
            # signed URLs, cookie material, or request tokens.
            _LOG.warning(
                "yt-dlp operation failed operation=%s category=%s cookie_fallback=%s",
                method,
                category,
                cookie_file is not None,
            )
            raise YoutubeDownloadFailure(category) from None

    @contextmanager
    def _private_cookie_copy(self):
        temp_cookie = None
        try:
            if cookie_state(self.data_dir) == "available":
                with _LOCK:
                    raw = _stored_cookie_bytes(self.data_dir)
                    if raw:
                        fd, name = tempfile.mkstemp(prefix=".yt-cookie-", suffix=".txt", dir=self.data_dir)
                        temp_cookie = Path(name)
                        try:
                            os.chmod(temp_cookie, 0o600)
                        except OSError:
                            pass
                        with os.fdopen(fd, "wb") as output:
                            output.write(raw)
            yield temp_cookie
        finally:
            if temp_cookie:
                temp_cookie.unlink(missing_ok=True)

    def _run(self, method: str, *args, **kwargs):
        try:
            return self._invoke(method, args, kwargs)
        except YoutubeDownloadFailure as first_error:
            # A saved owner session may clear either a genuine sign-in gate or
            # an explicit YouTube bot challenge. Keep the first attempt public
            # and unauthenticated; never repeatedly retry a rejected session.
            if first_error.youtube_category not in ("authentication", "bot_challenge"):
                raise
            with self._private_cookie_copy() as cookie_file:
                if cookie_file is None:
                    raise
                return self._invoke(method, args, kwargs, cookie_file)

    def extract_info(self, *args, **kwargs):
        return self._run("extract_info", *args, **kwargs)

    def download(self, *args, **kwargs):
        return self._run("download", *args, **kwargs)


def user_error(error: BaseException, url: str, data_dir: Path | None = None) -> str:
    """Do not expose extractor internals for YouTube; show actionable cookie guidance."""
    if not is_youtube_url(url):
        return str(error)[-700:]
    message = str(error).lower()
    category = getattr(error, "youtube_category", None) or _classify_youtube_failure(message)
    if category == "javascript_runtime":
        return "YouTube extraction could not solve its JavaScript challenge. This server needs the supported Deno runtime and yt-dlp-ejs component; check the deployment build and retry. No cookie values or yt-dlp log text were exposed."
    if category == "po_token":
        return "YouTube requires a Proof-of-Origin (PO) token for this video or stream. Cookies alone cannot provide it; this request was not bypassed. Try another publicly available video/format or wait for supported token-provider configuration."
    if category == "rate_limited":
        return "YouTube rate-limited this request (HTTP 429 or equivalent). This can be an account or shared server-IP limit. Wait before retrying; repeated attempts may extend the block."
    if category == "bot_challenge":
        status = cookie_state(data_dir)
        if status == "storage_unavailable":
            return "YouTube challenged this server request as automated traffic, and the configured private cookie store is unavailable. No session data was sent; retry when that store is reachable."
        if status == "available":
            return (
                "YouTube explicitly challenged this request as automated traffic. The unauthenticated attempt and the one optional saved-session fallback were rejected. "
                "This does not prove the video is private; YouTube may be evaluating the request or its network origin, and yt-dlp cannot safely override that challenge. Wait before retrying."
            )
        return (
            "YouTube explicitly challenged this server request as automated traffic (the not-a-bot check). "
            "That is distinct from a video being private or age-restricted. No saved owner session is available for the single optional fallback, "
            "and the challenge cannot be safely bypassed. Wait before retrying; repeated attempts may extend the block."
        )
    if category == "forbidden":
        return "YouTube denied the media request (HTTP 403). The cause can be a PO-token requirement, an expired stream URL, or a network/IP restriction; the safe diagnostic did not identify which one."
    if category == "unavailable":
        return "YouTube reports that this video is unavailable, private, removed, or restricted for this request. Check the public link and its viewing restrictions."
    if category == "no_formats":
        return "YouTube returned no format this service can download for this video. The video may require a PO token or have account, region, or playback restrictions."
    if category == "network":
        return "The server could not complete its connection to YouTube (timeout, DNS, TLS, or connection error). Retry later; this is not a cookie-content error."
    if category == "dependency":
        return "The YouTube downloader dependency is missing in this deployment. Rebuild the service from its pinned requirements."
    if category == "authentication" or any(term in message for term in ("sign in to confirm", "not a bot", "cookies", "login required", "authentication", "confirm your age")):
        status = cookie_state(data_dir)
        if status == "storage_unavailable":
            return "YouTube requested sign-in, but the configured private cookie store is unavailable. No session data was sent; retry when that store is reachable."
        if status == "missing":
            return (
                "YouTube challenged the unauthenticated request with a sign-in/bot check before media download. "
                "This can happen even for a public video and does not prove that the video itself requires sign-in. "
                "No saved cookie session is available for the optional authentication fallback."
            )
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
