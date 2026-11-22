import asyncio
import json
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI

import security


async def asgi_request(app, path, method="GET", body=b"", headers=None):
    values = {key.lower(): value for key, value in (headers or {}).items()}
    values.setdefault("host", "testserver")
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(key.encode(), value.encode()) for key, value in values.items()],
        "client": ("127.0.0.1", 4567), "server": ("testserver", 80),
    }
    sent = []
    yielded = False
    async def receive():
        nonlocal yielded
        if yielded:
            return {"type": "http.disconnect"}
        yielded = True
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(message):
        sent.append(message)
    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    payload = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start["status"], dict(start.get("headers", [])), payload


class SecurityTests(unittest.TestCase):
    def make_app(self):
        app = FastAPI()
        @app.get("/private")
        async def private():
            return {"ok": True}
        @app.get("/youtube-cookies/status")
        async def cookies():
            return {"configured": False}
        @app.post("/private")
        async def change():
            return {"changed": True}
        security.attach(app)
        return app

    def test_required_auth_fails_closed_without_password(self):
        with patch.dict(os.environ, {"NEONCLIPPING_REQUIRE_AUTH": "1"}, clear=False):
            os.environ.pop("NEONCLIPPING_AUTH_PASSWORD", None)
            with self.assertRaises(RuntimeError):
                security.attach(FastAPI())
        with patch.dict(os.environ, {"NEONCLIPPING_REQUIRE_AUTH": "1", "NEONCLIPPING_AUTH_PASSWORD": "short"}):
            with self.assertRaises(RuntimeError):
                security.attach(FastAPI())

    def test_login_protects_routes_and_issues_http_only_session(self):
        password = "a-strong-test-password-2026"
        with patch.dict(os.environ, {"NEONCLIPPING_REQUIRE_AUTH": "1", "NEONCLIPPING_AUTH_PASSWORD": password}):
            app = self.make_app()
        async def exercise():
            status, _, _ = await asgi_request(app, "/private", headers={"accept": "application/json"})
            self.assertEqual(status, 401)
            status, _, _ = await asgi_request(app, "/youtube-cookies/status", headers={"accept": "application/json"})
            self.assertEqual(status, 401)
            body = json.dumps({"password": password}).encode()
            status, headers, response = await asgi_request(app, "/auth/login", "POST", body, {"content-type": "application/json"})
            self.assertEqual(status, 200)
            self.assertIn(b'"ok":true', response)
            cookie = headers[b"set-cookie"].decode().split(";", 1)[0]
            self.assertIn("HttpOnly", headers[b"set-cookie"].decode())
            status, _, response = await asgi_request(app, "/private", headers={"cookie": cookie, "accept": "application/json"})
            self.assertEqual(status, 200)
            self.assertIn(b'"ok":true', response)
            status, _, response = await asgi_request(app, "/youtube-cookies/status", headers={"cookie": cookie, "accept": "application/json"})
            self.assertEqual(status, 200)
            self.assertIn(b'"configured":false', response)
            status, _, _ = await asgi_request(app, "/private", "POST", b"{}", {"cookie": cookie, "origin": "https://attacker.invalid", "content-type": "application/json"})
            self.assertEqual(status, 403)
        asyncio.run(exercise())

    def test_wrong_password_is_rejected(self):
        with patch.dict(os.environ, {"NEONCLIPPING_REQUIRE_AUTH": "1", "NEONCLIPPING_AUTH_PASSWORD": "a-strong-test-password-2026"}):
            app = self.make_app()
        async def exercise():
            for _ in range(security.MAX_LOGIN_TRIES):
                status, _, _ = await asgi_request(app, "/auth/login", "POST", b'{"password":"wrong"}', {"content-type": "application/json"})
                self.assertEqual(status, 401)
            status, _, _ = await asgi_request(app, "/auth/login", "POST", b'{"password":"wrong"}', {"content-type": "application/json"})
            self.assertEqual(status, 429)
        asyncio.run(exercise())

    def test_public_mode_keeps_clipping_open_and_cookie_settings_private(self):
        password = "a-strong-test-password-2026"
        with patch.dict(os.environ, {
            "NEONCLIPPING_REQUIRE_AUTH": "0",
            "NEONCLIPPING_PRIVATE_COOKIE_ADMIN": "1",
            "NEONCLIPPING_AUTH_PASSWORD": password,
        }):
            app = self.make_app()

        async def exercise():
            status, _, body = await asgi_request(app, "/auth/status")
            self.assertEqual(status, 200)
            state = json.loads(body)
            self.assertFalse(state["required"])
            self.assertTrue(state["cookie_admin_required"])
            status, _, _ = await asgi_request(app, "/private", headers={"accept": "application/json"})
            self.assertEqual(status, 200)
            status, _, _ = await asgi_request(app, "/youtube-cookies/status", headers={"accept": "application/json"})
            self.assertEqual(status, 401)
            status, _, _ = await asgi_request(
                app, "/private", "POST", b"{}",
                {"origin": "https://attacker.invalid", "content-type": "application/json"},
            )
            self.assertEqual(status, 403)
            body = json.dumps({"password": password}).encode()
            status, headers, _ = await asgi_request(app, "/auth/login", "POST", body, {"content-type": "application/json"})
            self.assertEqual(status, 200)
            cookie = headers[b"set-cookie"].decode().split(";", 1)[0]
            status, _, _ = await asgi_request(app, "/youtube-cookies/status", headers={"cookie": cookie})
            self.assertEqual(status, 200)

        asyncio.run(exercise())

    def test_public_write_rate_limits_expensive_uploads(self):
        with patch.dict(os.environ, {"NEONCLIPPING_REQUIRE_AUTH": "0"}, clear=False):
            app = FastAPI()

            @app.post("/upload")
            async def upload():
                return {"ok": True}

            security.attach(app)

        async def exercise():
            limit = security.RATE_LIMITS["/upload"][0]
            for _ in range(limit):
                status, _, _ = await asgi_request(app, "/upload", "POST", b"{}")
                self.assertEqual(status, 200)
            status, headers, body = await asgi_request(app, "/upload", "POST", b"{}")
            self.assertEqual(status, 429)
            self.assertIn(b"Too many requests", body)
            self.assertIn(b"retry-after", headers)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
