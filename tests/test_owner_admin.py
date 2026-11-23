import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

import owner_admin
import privacy
import security
import storage


class FakeQueue:
    def __init__(self):
        self.is_paused = False

    def rows(self):
        return []

    def paused(self):
        return self.is_paused

    def pause(self, value):
        self.is_paused = bool(value)

    def change(self, job, action):
        return {"id": job, "status": action, "payload": {"secret": "hidden"}}


class OwnerAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ashuviralcuts-admin-test-")
        self.password = "a-private-owner-password-2026"
        self.env = patch.dict(os.environ, {
            "NEONCLIPPING_REQUIRE_AUTH": "0",
            "NEONCLIPPING_PRIVATE_COOKIE_ADMIN": "1",
            "NEONCLIPPING_AUTH_PASSWORD": self.password,
        })
        self.env.start()
        self.app = FastAPI()
        self.app.processing_queue = FakeQueue()

        @self.app.post("/public-write")
        async def public_write():
            return {"ok": True}

        owner_admin.attach(self.app, Path(self.temp.name), {})
        privacy.attach(self.app, Path(self.temp.name))
        security.attach(self.app)

    def tearDown(self):
        storage.set_admin_limits(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def test_admin_panel_requires_owner_password_but_public_route_stays_open(self):
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://testserver") as client:
                response = await client.get("/admin/api/overview")
                self.assertEqual(response.status_code, 401)
                response = await client.get("/admin", headers={"accept": "text/html"}, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers["location"].startswith("/login?next=/admin"))
                response = await client.post("/public-write")
                self.assertEqual(response.status_code, 200)
                response = await client.post("/auth/login", json={"password": self.password})
                self.assertEqual(response.status_code, 200)
                response = await client.get("/admin/api/overview")
                self.assertEqual(response.status_code, 200)
                self.assertIn("jobs", response.json())
                self.assertEqual(response.json()["settings"]["upload_limit_mb"], 300)
                response = await client.get("/admin")
                self.assertEqual(response.status_code, 200)
                self.assertIn("OWNER ADMIN", response.text)
                self.assertIn("YouTube cookies", response.text)
                self.assertIn("youtube-cookie-choose", response.text)
                self.assertIn('/assets/youtube-cookies.js?v=6" defer', response.text)
                self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", response.text)
                self.assertIn("@media(max-width:700px)", response.text)

        asyncio.run(exercise())

    def test_admin_settings_and_maintenance_guard(self):
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://testserver") as client:
                await client.post("/auth/login", json={"password": self.password})
                response = await client.post("/admin/api/settings", json={
                    "maintenance_mode": True, "site_notice": "Back soon", "upload_limit_mb": 120,
                    "storage_budget_mb": 700, "storage_reserve_mb": 96,
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual((await client.get("/public-settings")).json(), {"maintenance_mode": True, "site_notice": "Back soon"})
                self.assertEqual((await client.post("/public-write")).status_code, 503)
                self.assertEqual((await client.post("/admin/api/queue/pause", json={"paused": True})).json(), {"paused": True})
                changed = await client.post("/admin/api/jobs/abc/retry", json={})
                self.assertEqual(changed.status_code, 200)
                self.assertNotIn("payload", changed.json())

        asyncio.run(exercise())

    def test_private_cookie_routes_are_not_public(self):
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://testserver") as client:
                response = await client.get("/youtube-cookies/status")
                self.assertEqual(response.status_code, 401)
                response = await client.post("/youtube-cookies", files={"file": ("cookies.txt", b"private", "text/plain")})
                self.assertEqual(response.status_code, 401)
                response = await client.delete("/youtube-cookies")
                self.assertEqual(response.status_code, 401)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
