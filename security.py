"""Single-user password gate for public deployments; local use remains unchanged."""
from __future__ import annotations

import hmac
import html
import os
import secrets
import time
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "nc_session"
SESSION_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 10 * 60
MAX_LOGIN_TRIES = 5

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
    if required and not password:
        raise RuntimeError("Set NEONCLIPPING_AUTH_PASSWORD in the hosting environment before starting this public service.")
    if required and len(password) < 16:
        raise RuntimeError("NEONCLIPPING_AUTH_PASSWORD must be at least 16 characters long.")

    sessions: dict[str, float] = {}
    attempts: dict[str, tuple[int, float]] = {}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse(LOGIN_HTML)

    @app.get("/auth/status")
    async def auth_status(request: Request):
        session = request.cookies.get(COOKIE, "")
        expiry = sessions.get(session, 0)
        return {"required": required, "authenticated": (not required) or expiry > time.time()}

    @app.post("/auth/login")
    async def login(request: Request):
        if not required:
            return {"ok": True}
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
        token = request.cookies.get(COOKIE, "")
        expiry = sessions.get(token, 0)
        authenticated = (not required) or expiry > time.time()
        if token and expiry and expiry <= time.time():
            sessions.pop(token, None)

        if required and not public and not authenticated:
            accepts_html = "text/html" in request.headers.get("accept", "")
            if request.method == "GET" and accepts_html:
                target = request.url.path
                if request.url.query:
                    target += "?" + request.url.query
                return RedirectResponse("/login?next=" + quote(target, safe="/?=&%"), status_code=303, headers={"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin"})
            return JSONResponse({"detail": "Sign in to use AshuViralCuts."}, status_code=401, headers={"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin"})

        # SameSite cookies plus origin validation protect state-changing requests.
        if required and authenticated and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                expected_host = request.headers.get("host", "")
                forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
                scheme = forwarded_proto or request.url.scheme
                if origin.rstrip("/") != f"{scheme}://{expected_host}".rstrip("/"):
                    return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)

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
