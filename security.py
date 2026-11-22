"""Single-user password gate for public deployments; local use remains unchanged."""
from __future__ import annotations

import hmac
import html
import os
import secrets
import time
from collections import deque
from math import ceil
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "nc_session"
SESSION_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 10 * 60
MAX_LOGIN_TRIES = 5
DEFAULT_WRITE_LIMIT = (180, 60)
RATE_LIMITS = {
    "/upload": (8, 10 * 60),
    "/link": (8, 10 * 60),
    "/analyze": (8, 10 * 60),
    "/make": (8, 10 * 60),
    "/export": (16, 10 * 60),
    "/transcribe": (6, 60 * 60),
    "/youtube-cookies": (5, 60 * 60),
    "/upload-asset": (20, 10 * 60),
}

LOGIN_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>Sign in · AshuViralCuts</title>
<style>
:root{color-scheme:dark;--bg:#0b0912;--panel:#171320;--line:#30263e;--pink:#ff4ba5;--cyan:#58e8f7;--muted:#a99db7;--white:#fbf6ff}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(ellipse at 50% 0,#30172f,var(--bg) 60%);color:var(--white);font:16px/1.5 'Segoe UI',sans-serif}.card{width:min(100%,420px);padding:30px;border:1px solid var(--line);border-radius:18px;background:var(--panel);box-shadow:0 24px 80px #0008}.mark{color:var(--pink);font-size:14px;font-weight:800;letter-spacing:.16em}h1{margin:14px 0 4px;font-size:28px}p{margin:0 0 22px;color:var(--muted)}label{display:block;margin:15px 0 7px;color:var(--muted);font-size:13px}input{width:100%;padding:13px;border:1px solid var(--line);border-radius:9px;background:#100c17;color:var(--white);font:inherit}input:focus{outline:2px solid var(--cyan)}button{width:100%;margin-top:18px;padding:13px;border:0;border-radius:9px;background:var(--pink);color:#1c0714;font:700 15px 'Segoe UI',sans-serif;cursor:pointer}#message{min-height:24px;margin:12px 0 0;color:#ff9bc9;font-size:13px}.hint{margin-top:18px;font-size:12px}
</style></head><body><main class="card"><div class="mark">A↗ ASHUVIRALCUTS</div><h1>Sign in to your studio</h1><p>Enter your private access password to continue.</p><form id="form"><label for="password">Access password</label><input id="password" type="password" autocomplete="current-password" required autofocus><button type="submit">Sign in</button><div id="message" role="status" aria-live="polite"></div></form><p class="hint">The password is configured by the studio owner in the hosting environment.</p></main>
<script>
const form=document.getElementById('form'),message=document.getElementById('message'),password=document.getElementById('password');
const params=new URLSearchParams(location.search),requested=params.get('next')||'/';
const next=requested.startsWith('/')&&!requested.startsWith('//')&&!requested.startsWith('/\\')?requested:'/';
form.addEventListener('submit',async event=>{event.preventDefault();message.textContent='Signing in…';try{const response=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:password.value})});const result=await response.json();if(!response.ok)throw new Error(result.detail||'Sign-in failed.');location.replace(next);}catch(error){message.textContent=error.message;password.focus();password.select();}});
</script></body></html>"""


def attach(app: FastAPI) -> None:
    password = os.environ.get("NEONCLIPPING_AUTH_PASSWORD", "")
    required = os.environ.get("NEONCLIPPING_REQUIRE_AUTH") == "1"
    cookie_admin_required = (
        bool(password) or os.environ.get("NEONCLIPPING_PRIVATE_COOKIE_ADMIN") == "1"
    )
    if required and not password:
        raise RuntimeError("Set NEONCLIPPING_AUTH_PASSWORD in the hosting environment before starting this public service.")
    if required and len(password) < 16:
        raise RuntimeError("NEONCLIPPING_AUTH_PASSWORD must be at least 16 characters long.")

    sessions: dict[str, float] = {}
    attempts: dict[str, tuple[int, float]] = {}
    writes: dict[tuple[str, str], deque[float]] = {}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse(LOGIN_HTML)

    @app.get("/auth/status")
    async def auth_status(request: Request):
        session = request.cookies.get(COOKIE, "")
        expiry = sessions.get(session, 0)
        signed_in = expiry > time.time()
        return {
            "required": required,
            "authenticated": (not required) or signed_in,
            "cookie_admin_required": cookie_admin_required,
            "cookie_admin_authenticated": signed_in and bool(password),
            "owner_admin_required": True,
            "owner_admin_authenticated": signed_in and bool(password),
        }

    @app.post("/auth/login")
    async def login(request: Request):
        if not required and not cookie_admin_required:
            return {"ok": True}
        if not password:
            return JSONResponse(
                {"detail": "Private YouTube cookie management is not configured."},
                status_code=503,
            )
        host = request.client.host if request.client else "unknown"
        now = time.time()
        count, started = attempts.get(host, (0, now))
        if now - started >= LOGIN_WINDOW_SECONDS:
            count, started = 0, now
        if count >= MAX_LOGIN_TRIES:
            return JSONResponse({"detail": "Too many attempts. Try again in ten minutes."}, status_code=429)
        try:
            body = await request.json()
            candidate = str(body.get("password", "")) if isinstance(body, dict) else ""
        except (ValueError, TypeError):
            candidate = ""
        if not hmac.compare_digest(candidate.encode("utf-8"), password.encode("utf-8")):
            attempts[host] = (count + 1, started)
            return JSONResponse({"detail": "Incorrect password."}, status_code=401)
        attempts.pop(host, None)
        token = secrets.token_urlsafe(32)
        sessions[token] = now + SESSION_SECONDS
        response = JSONResponse({"ok": True})
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict", path="/")
        return response

    @app.post("/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(COOKIE, "")
        sessions.pop(token, None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=request.url.scheme == "https", samesite="strict")
        return response

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        public = path in {"/login", "/auth/login", "/auth/status", "/healthz"}
        owner_admin_route = path == "/admin" or path.startswith("/admin/")
        cookie_admin_route = path in {"/youtube-cookies", "/youtube-cookies/status"}
        if owner_admin_route and not password:
            return JSONResponse(
                {"detail": "Owner Admin is unavailable until its password is configured."},
                status_code=503,
            )
        token = request.cookies.get(COOKIE, "")
        expiry = sessions.get(token, 0)
        signed_in = expiry > time.time()
        request.state.owner_admin_authenticated = signed_in and bool(password)
        needs_session = required or owner_admin_route or (cookie_admin_route and cookie_admin_required)
        authenticated = (not needs_session) or signed_in
        if token and expiry and expiry <= time.time():
            sessions.pop(token, None)

        if needs_session and not public and not authenticated:
            accepts_html = "text/html" in request.headers.get("accept", "")
            if request.method == "GET" and accepts_html:
                target = request.url.path
                if request.url.query:
                    target += "?" + request.url.query
                return RedirectResponse("/login?next=" + quote(target, safe="/?=&%"), status_code=303, headers={"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin"})
            return JSONResponse({"detail": "Sign in to use AshuViralCuts."}, status_code=401, headers={"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin"})

        # Reject cross-origin writes even in public mode so visitors cannot be
        # tricked into spending another user's processing or storage budget.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                expected_host = request.headers.get("host", "")
                forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
                scheme = forwarded_proto or request.url.scheme
                if origin.rstrip("/") != f"{scheme}://{expected_host}".rstrip("/"):
                    return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)

            host = request.client.host if request.client else "unknown"
            route_key = path
            if path.startswith("/live/"):
                route_key = "/live"
            elif path.startswith("/creators/"):
                route_key = "/creators/item"
            elif path.startswith("/transcripts/"):
                route_key = "/transcripts"

            now = time.monotonic()
            checks = [("*", DEFAULT_WRITE_LIMIT)]
            if route_key in RATE_LIMITS:
                checks.append((route_key, RATE_LIMITS[route_key]))
            for bucket_name, (maximum, window) in checks:
                bucket = writes.setdefault((host, bucket_name), deque())
                while bucket and now - bucket[0] >= window:
                    bucket.popleft()
                if len(bucket) >= maximum:
                    retry_after = max(1, ceil(window - (now - bucket[0])))
                    return JSONResponse(
                        {"detail": "Too many requests. Please wait and try again."},
                        status_code=429,
                        headers={"Retry-After": str(retry_after)},
                    )
                bucket.append(now)

            if len(writes) > 5000:
                for key, bucket in list(writes.items()):
                    if not bucket or now - bucket[-1] >= 60 * 60:
                        writes.pop(key, None)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self' data:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    app.state.auth_required = required
