"""Visitor-scoped access to videos, clips, jobs, queues, and downloadable bundles."""
from __future__ import annotations

import json
import re
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

VISITOR_COOKIE = "nc_visitor"
HEX_ID = re.compile(r"^[a-fA-F0-9]{12,64}$")
ID_KEYS = {"id", "job", "live", "bundle", "token", "file", "files", "source_id", "child", "creator_id", "cid", "anchor"}
PRIVATE_LISTS = {"/library", "/highlights", "/queue", "/creators", "/runtime"}
PRIVATE_PREFIXES = ("/media/", "/text/", "/preview/", "/make/", "/status/", "/analysis/", "/bundles/", "/clips/", "/vision/frame/", "/live/", "/creators/", "/queue/")


def _ids(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in ID_KEYS:
                if isinstance(item, str) and HEX_ID.fullmatch(item):
                    found.add(item)
                elif isinstance(item, list):
                    found.update(x for x in item if isinstance(x, str) and HEX_ID.fullmatch(x))
            found.update(_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_ids(item))
    return found


def _path_id(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    if parts[0] in {"media", "make", "status", "analysis", "bundles"} and len(parts) >= 2:
        return parts[1]
    if parts[0] in {"text", "preview"} and len(parts) >= 2:
        return parts[1]
    if parts[0] == "clips" and len(parts) >= 3 and parts[2] == "edit":
        return parts[1]
    if parts[0] == "vision" and len(parts) >= 3 and parts[1] == "frame":
        return parts[2]
    if parts[0] in {"live", "creators"} and len(parts) >= 2 and parts[1] not in {"start", "active"}:
        return parts[1]
    if parts[0] == "queue" and len(parts) >= 3:
        return parts[1]
    return None


class OwnerPrivacyMiddleware:
    def __init__(self, app, data: str):
        self.app = app
        self.registry_path = Path(data) / "visitor-owners.json"
        self.lock = threading.RLock()
        try:
            saved = json.loads(self.registry_path.read_text(encoding="utf-8"))
            self.owners = saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            self.owners = {}

    def owner_of(self, item: str) -> str | None:
        return self.owners.get(item)

    def remember(self, items: set[str], owner: str) -> None:
        changed = False
        with self.lock:
            for item in items:
                if HEX_ID.fullmatch(item) and item not in self.owners:
                    self.owners[item] = owner
                    changed = True
            if changed:
                temp = self.registry_path.with_suffix(".tmp")
                temp.write_text(json.dumps(self.owners, separators=(",", ":")), encoding="utf-8")
                temp.replace(self.registry_path)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        cookies = headers.get(b"cookie", b"").decode("latin-1")
        visitor = None
        for cookie in cookies.split(";"):
            name, sep, value = cookie.strip().partition("=")
            if sep and name == VISITOR_COOKIE:
                visitor = value
                break
        if not visitor or not re.fullmatch(r"[A-Za-z0-9_-]{40,60}", visitor):
            visitor = secrets.token_urlsafe(32)
            new_visitor = True
        else:
            new_visitor = False

        state = scope.get("state") or {}
        is_admin = bool(state.get("owner_admin_authenticated"))
        path = scope.get("path", "/")
        method = scope.get("method", "GET").upper()
        if not is_admin:
            object_id = _path_id(path)
            if object_id and self.owner_of(object_id) != visitor:
                response = JSONResponse({"detail": "This private video or job is not available in this browser."}, status_code=404, headers={"Cache-Control": "private, no-store", "Vary": "Cookie"})
                await response(scope, receive, send)
                return

        request_data = None
        receive_for_app = receive
        ctype = headers.get(b"content-type", b"").decode("latin-1").lower()
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if method in {"POST", "PUT", "PATCH", "DELETE"} and ctype.startswith("application/json") and 0 < length <= 1024 * 1024:
            messages = []
            body = bytearray()
            while True:
                message = await receive()
                messages.append(message)
                if message.get("type") == "http.request":
                    body.extend(message.get("body", b""))
                if message.get("type") != "http.request" or not message.get("more_body", False):
                    break
            try:
                request_data = json.loads(body)
            except (ValueError, TypeError):
                request_data = None
            replay = list(messages)

            async def receive_for_app():
                return replay.pop(0) if replay else {"type": "http.disconnect"}

            if not is_admin and isinstance(request_data, (dict, list)):
                for item in _ids(request_data):
                    existing_owner = self.owner_of(item)
                    if existing_owner is not None and existing_owner != visitor:
                        response = JSONResponse({"detail": "This private video or job is not available in this browser."}, status_code=404, headers={"Cache-Control": "private, no-store", "Vary": "Cookie"})
                        await response(scope, receive_for_app, send)
                        return

        new_scope = dict(scope)
        state = dict(state)
        state["visitor_owner"] = visitor
        new_scope["state"] = state

        start_message = None
        response_chunks: list[bytes] = []
        json_response = False

        async def guarded_send(message):
            nonlocal start_message, json_response
            if message.get("type") == "http.response.start":
                start_message = dict(message)
                response_headers = list(start_message.get("headers", []))
                content_type = next((v.decode("latin-1") for k, v in response_headers if k.lower() == b"content-type"), "")
                json_response = "application/json" in content_type
                if new_visitor:
                    proto = headers.get(b"x-forwarded-proto", b"").decode("latin-1").split(",")[0].strip()
                    secure = scope.get("scheme") == "https" or proto == "https"
                    cookie_value = f"{VISITOR_COOKIE}={visitor}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")
                    response_headers.append((b"set-cookie", cookie_value.encode("latin-1")))
                if path.startswith(PRIVATE_PREFIXES) or path in PRIVATE_LISTS:
                    response_headers.extend([(b"cache-control", b"private, no-store"), (b"vary", b"Cookie")])
                start_message["headers"] = response_headers
                if not json_response:
                    await send(start_message)
                return

            if json_response and message.get("type") == "http.response.body":
                response_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = b"".join(response_chunks)
                status_code = start_message.get("status", 200)
                if status_code < 400:
                    try:
                        payload = json.loads(body)
                    except (ValueError, TypeError):
                        payload = None
                    if payload is not None:
                        if not is_admin and method == "GET" and path in PRIVATE_LISTS:
                            payload = self._filter_list(path, payload, visitor)
                        if not is_admin and (method in {"POST", "PUT", "PATCH"} or (method == "GET" and _path_id(path))):
                            self.remember(_ids(payload), visitor)
                        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                response_headers = [(key, value) for key, value in start_message.get("headers", []) if key.lower() != b"content-length"]
                response_headers.append((b"content-length", str(len(body)).encode("ascii")))
                start_message["headers"] = response_headers
                await send(start_message)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(message)

        await self.app(new_scope, receive_for_app, guarded_send)

    def _filter_list(self, path: str, payload, visitor: str):
        def is_mine(row) -> bool:
            if not isinstance(row, dict):
                return False
            return any(isinstance(row.get(key), str) and self.owner_of(row[key]) == visitor for key in ("id", "job", "file", "child"))

        if path == "/queue" and isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
            payload["jobs"] = [row for row in payload["jobs"] if is_mine(row)]
        elif path == "/runtime" and isinstance(payload, dict) and isinstance(payload.get("active"), list):
            payload["active"] = [row for row in payload["active"] if is_mine(row)]
        elif isinstance(payload, list):
            return [row for row in payload if is_mine(row)]
        return payload


def attach(app: FastAPI, data: Path) -> None:
    app.add_middleware(OwnerPrivacyMiddleware, data=str(Path(data).resolve()))
