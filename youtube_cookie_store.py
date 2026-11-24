"""Optional encrypted persistence for the owner's YouTube cookie file.

Uses a private Supabase Storage bucket. The object is encrypted client-side with
AES-GCM before it leaves the process; credentials and key are server env vars.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_OBJECT = "ashuviralcuts/youtube-cookies.enc"
_AAD = b"ashuviralcuts/youtube-cookies/v1"
_TIMEOUT = 12
_MAX_ENCRYPTED_BYTES = 1024 * 1024 + 28


class CookieStoreError(RuntimeError):
    """Sanitized persistent-store failure. Never includes provider response text."""


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the server-side storage key to a redirect destination."""
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def configured() -> bool:
    return any(os.environ.get(name) for name in _REQUIRED)


_REQUIRED = (
    "NEONCLIPPING_COOKIE_STORE_URL",
    "NEONCLIPPING_COOKIE_STORE_BUCKET",
    "NEONCLIPPING_COOKIE_STORE_KEY",
    "NEONCLIPPING_COOKIE_ENCRYPTION_KEY",
)


def _settings():
    values = {name: os.environ.get(name, "").strip() for name in _REQUIRED}
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise CookieStoreError("Persistent cookie storage configuration is incomplete.")
    url = values["NEONCLIPPING_COOKIE_STORE_URL"].rstrip("/")
    parsed_url = urlsplit(url)
    if (parsed_url.scheme != "https" or not parsed_url.hostname
            or not parsed_url.hostname.endswith(".supabase.co")
            or parsed_url.username or parsed_url.password or parsed_url.path not in ("", "/")
            or parsed_url.query or parsed_url.fragment):
        raise CookieStoreError("Persistent cookie storage must use a valid Supabase HTTPS project URL.")
    bucket = values["NEONCLIPPING_COOKIE_STORE_BUCKET"]
    if len(bucket) > 128 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in bucket):
        raise CookieStoreError("Persistent cookie storage bucket name is invalid.")
    encoded_key = values["NEONCLIPPING_COOKIE_ENCRYPTION_KEY"]
    try:
        key = base64.b64decode(encoded_key + "=" * (-len(encoded_key) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as error:
        raise CookieStoreError("Persistent cookie encryption key is invalid.") from error
    if len(key) != 32:
        raise CookieStoreError("Persistent cookie encryption key must decode to 32 bytes.")
    return url, bucket, values["NEONCLIPPING_COOKIE_STORE_KEY"], key


def _aead(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:
        raise CookieStoreError("Encrypted cookie storage dependency is unavailable.") from error
    return AESGCM(key)


def _request(method: str, *, payload: bytes | None = None, bucket_info: bool = False) -> bytes | None:
    settings = _settings()
    if settings is None:
        return None
    url, bucket, api_key, _ = settings
    object_path = quote(_OBJECT, safe="/")
    endpoint = f"{url}/storage/v1/object/{quote(bucket, safe='')}/{object_path}"
    headers = {
        "apikey": api_key,
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/octet-stream",
        "Cache-Control": "no-store",
    }
    if method == "POST":
        headers["x-upsert"] = "true"
    if method == "DELETE":
        endpoint = f"{url}/storage/v1/object/{quote(bucket, safe='')}"
        headers["Content-Type"] = "application/json"
        payload = json.dumps({"prefixes": [_OBJECT]}, separators=(",", ":")).encode()
    if method == "GET":
        endpoint = f"{url}/storage/v1/object/authenticated/{quote(bucket, safe='')}/{object_path}"
    if bucket_info:
        endpoint = f"{url}/storage/v1/bucket/{quote(bucket, safe='')}"
        headers["Content-Type"] = "application/json"
    request = Request(endpoint, data=payload, headers=headers, method=method)
    try:
        opener = build_opener(_NoRedirect())
        with opener.open(request, timeout=_TIMEOUT) as response:
            return response.read(_MAX_ENCRYPTED_BYTES + 1)
    except HTTPError as error:
        if method == "GET" and error.code == 404:
            return b""
        raise CookieStoreError("Persistent cookie storage request failed.") from None
    except (URLError, TimeoutError, OSError):
        raise CookieStoreError("Persistent cookie storage is temporarily unavailable.") from None


def put(raw: bytes) -> bool:
    settings = _settings()
    if settings is None:
        return False
    _ensure_private_bucket()
    import secrets
    nonce = secrets.token_bytes(12)
    encrypted = nonce + _aead(settings[3]).encrypt(nonce, raw, _AAD)
    _request("POST", payload=encrypted)
    return True


def get() -> bytes | None:
    settings = _settings()
    if settings is None:
        return None
    _ensure_private_bucket()
    encrypted = _request("GET")
    if not encrypted:
        return b""
    if len(encrypted) < 29 or len(encrypted) > _MAX_ENCRYPTED_BYTES:
        raise CookieStoreError("Persistent cookie object is invalid.")
    try:
        return _aead(settings[3]).decrypt(encrypted[:12], encrypted[12:], _AAD)
    except Exception:
        raise CookieStoreError("Persistent cookie object could not be decrypted.") from None


def delete() -> bool:
    if _settings() is None:
        return False
    _ensure_private_bucket()
    _request("DELETE")
    return True


def _ensure_private_bucket() -> None:
    metadata = _request("GET", bucket_info=True)
    try:
        details = json.loads(metadata or b"")
    except (TypeError, ValueError):
        details = None
    if not isinstance(details, dict) or details.get("public") is not False:
        raise CookieStoreError("Persistent cookie storage bucket is missing or not private.")
