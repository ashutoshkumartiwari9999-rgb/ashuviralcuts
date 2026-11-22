import os
import tempfile
import unittest
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx

COOKIE_SAMPLE = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tprivate-cookie-value\n"


_TEST_DATA = tempfile.TemporaryDirectory(prefix="ashuviralcuts-test-")
with patch.dict(os.environ, {"NEONCLIPPING_DATA_DIR": _TEST_DATA.name}, clear=False):
    os.environ.pop("NEONCLIPPING_REQUIRE_AUTH", None)
    import app as app_module


class AppSmokeTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        app_module.app.processing_queue.close()
        _TEST_DATA.cleanup()

    def test_dashboard_phone_assets_storage_and_health_routes(self):
        with patch.dict(os.environ, {"NEONCLIPPING_LAN": "1"}):
            with patch("phone.is_local", return_value=True):
                async def exercise():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),base_url="http://testserver") as client:
                        self.assertNotIn("yt_dlp",sys.modules,"yt-dlp should load only when a YouTube job starts")
                        dashboard = await client.get("/")
                        self.assertEqual(dashboard.status_code, 200)
                        self.assertIn("ASHUVIRALCUTS", dashboard.text)
                        self.assertIn("ashutoshkumartiwari9999-rgb", dashboard.text)
                        self.assertIn("ashutosh_official9", dashboard.text)
                        self.assertIn("YouTube cookies", dashboard.text)
                        self.assertIn('/assets/app.js?v=20261120-vision1" defer',dashboard.text)

                        phone = await client.get("/m")
                        self.assertEqual(phone.status_code, 200)
                        self.assertIn("Phone Remote", phone.text)
                        self.assertEqual((await client.get("/assets/mobile.js")).status_code, 200)
                        self.assertEqual((await client.get("/assets/security.js")).status_code, 200)
                        self.assertEqual((await client.get("/assets/admin.js")).status_code, 200)
                        self.assertEqual((await client.get("/assets/storage.js")).status_code, 200)
                        cached_asset=await client.get("/assets/youtube-cookies.js")
                        self.assertEqual(cached_asset.status_code, 200)
                        self.assertIn("max-age=300",cached_asset.headers.get("cache-control",""))
                        health=await client.get("/healthz")
                        self.assertEqual(health.status_code, 200)
                        self.assertTrue(health.json()["ok"])
                        auth_state=await client.get("/auth/status")
                        self.assertEqual(auth_state.status_code,200)
                        self.assertFalse(auth_state.json()["required"])
                        self.assertEqual((await client.get("/public-settings")).status_code,200)
                        cookies = await client.post("/youtube-cookies",files={"file":("cookies.txt",COOKIE_SAMPLE,"text/plain")})
                        self.assertEqual(cookies.status_code,200,cookies.text)
                        self.assertNotIn("private-cookie-value",cookies.text)
                        self.assertEqual((await client.get("/youtube-cookies/status")).json(),{"configured":True})
                        self.assertEqual((await client.get("/media/.youtube-cookies")).status_code,404)
                        self.assertEqual((await client.delete("/youtube-cookies")).status_code,200)
                        self.assertFalse(app_module.DATA.joinpath(".youtube-cookies.txt").exists())

                        storage_report = await client.get("/storage")
                        self.assertEqual(storage_report.status_code, 200)
                        self.assertFalse(storage_report.json()["user_media_cleanup"])

                        bad_upload = await client.post("/upload", files={"file": ("not-a-video.txt", b"no", "text/plain")})
                        self.assertEqual(bad_upload.status_code, 415)

                asyncio.run(exercise())

    def test_render_blueprint_uses_free_tier_without_disk(self):
        import yaml
        path = Path(__file__).resolve().parents[1] / "render.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        service = config["services"][0]
        self.assertEqual(service["plan"], "free")
        self.assertNotIn("disk", service)
        variables = {item.get("key"): item for item in service["envVars"] if item.get("key")}
        self.assertEqual(variables["NEONCLIPPING_REQUIRE_AUTH"]["value"], "0")
        self.assertEqual(variables["NEONCLIPPING_PRIVATE_COOKIE_ADMIN"]["value"], "1")
        self.assertEqual(variables["NEONCLIPPING_PUBLIC_MODE"]["value"], "1")
        self.assertTrue(variables["NEONCLIPPING_AUTH_PASSWORD"]["sync"] is False)

    def test_startup_lifespan_keeps_health_check_lightweight(self):
        async def exercise():
            async with app_module.app.router.lifespan_context(app_module.app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app),base_url="http://testserver") as client:
                    response=await client.get("/healthz")
                    self.assertEqual(response.status_code,200)
                    self.assertTrue(response.json()["ok"])
        started=time.monotonic()
        asyncio.run(exercise())
        self.assertLess(time.monotonic()-started,2.0)


if __name__ == "__main__":
    unittest.main()
