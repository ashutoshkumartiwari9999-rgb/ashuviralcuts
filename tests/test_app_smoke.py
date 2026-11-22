import os
import tempfile
import unittest
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx

COOKIE_SAMPLE = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\ttest-login-cookie\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\ttest-signature-cookie\n"


_TEST_DATA = tempfile.TemporaryDirectory(prefix="ashuviralcuts-test-")
with patch.dict(os.environ, {"NEONCLIPPING_DATA_DIR": _TEST_DATA.name, "NEONCLIPPING_REQUIRE_AUTH": "0", "NEONCLIPPING_PRIVATE_COOKIE_ADMIN": "1", "NEONCLIPPING_AUTH_PASSWORD": "test-owner-password-for-cookie-tests"}, clear=False):
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
                        self.assertIn("Upload YouTube cookies.txt", dashboard.text)
                        self.assertIn("Owner only · private", dashboard.text)
                        self.assertIn('href="/login?next=/"', dashboard.text)
                        self.assertIn('id="youtube-cookie-choose"', dashboard.text)
                        self.assertIn('>Choose cookies.txt</button>', dashboard.text)
                        self.assertIn('id="youtube-cookie-upload"', dashboard.text)
                        self.assertIn('id="youtube-cookie-filename"', dashboard.text)
                        self.assertIn('/assets/youtube-cookies.js?v=5" defer', dashboard.text)
                        self.assertIn('/assets/style.css?v=20261122-cookies2"', dashboard.text)
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
                        self.assertIn("file.click()", cached_asset.text)
                        self.assertIn("youtube-cookie-upload", cached_asset.text)
                        self.assertIn("filename.textContent = chosen.name", cached_asset.text)
                        health=await client.get("/healthz")
                        self.assertEqual(health.status_code, 200)
                        self.assertTrue(health.json()["ok"])
                        auth_state=await client.get("/auth/status")
                        self.assertEqual(auth_state.status_code,200)
                        self.assertFalse(auth_state.json()["required"])
                        self.assertEqual((await client.get("/public-settings")).status_code,200)
                        unauthenticated_cookie_upload = await client.post("/youtube-cookies",files={"file":("cookies.txt",COOKIE_SAMPLE,"text/plain")})
                        self.assertEqual(unauthenticated_cookie_upload.status_code,401)
                        self.assertEqual((await client.post("/upload", files={"file":("not-a-video.txt",b"no","text/plain")})).status_code,415)
                        login = await client.post("/auth/login", json={"password":"test-owner-password-for-cookie-tests"})
                        self.assertEqual(login.status_code,200,login.text)
                        invalid_cookies = await client.post("/youtube-cookies",files={"file":("cookies.txt",b"browser-export-json","text/plain")})
                        self.assertEqual(invalid_cookies.status_code,400)
                        self.assertIn("Netscape",invalid_cookies.text)
                        cookies = await client.post("/youtube-cookies",files={"file":("cookies.txt",COOKIE_SAMPLE,"text/plain")})
                        self.assertEqual(cookies.status_code,200,cookies.text)
                        self.assertNotIn("private-cookie-value",cookies.text)
                        self.assertFalse(list(app_module.DATA.glob(".youtube-cookies-*.upload")))
                        self.assertEqual((await client.get("/youtube-cookies/status")).json(),{"configured":True})
                        self.assertEqual((await client.get("/media/.youtube-cookies")).status_code,404)
                        self.assertEqual((await client.delete("/youtube-cookies")).status_code,200)
                        self.assertFalse(app_module.DATA.joinpath(".youtube-cookies.txt").exists())
                        oversized_cookies = await client.post("/youtube-cookies",files={"file":("cookies.txt",b"x"*(1024*1024+1),"text/plain")})
                        self.assertIn(oversized_cookies.status_code,(400,413))
                        self.assertIn("1 MB",oversized_cookies.text)

                        storage_report = await client.get("/storage")
                        self.assertEqual(storage_report.status_code, 200)
                        self.assertFalse(storage_report.json()["user_media_cleanup"])

                        bad_upload = await client.post("/upload", files={"file": ("not-a-video.txt", b"no", "text/plain")})
                        self.assertEqual(bad_upload.status_code, 415)

                asyncio.run(exercise())

    def test_cookie_upload_accepts_windows_filename_variants_but_validates_contents(self):
        async def exercise():
            cases = (
                ("cookies (1).txt", COOKIE_SAMPLE),
                ("cookies.txt.txt", COOKIE_SAMPLE),
                ("YouTube cookies.txt", COOKIE_SAMPLE),
                ("cookies.txt.txt", b"browser-export-json"),
            )
            for index, (name, content) in enumerate(cases, start=20):
                with self.subTest(filename=name, valid=content == COOKIE_SAMPLE):
                    transport = httpx.ASGITransport(app=app_module.app, client=(f"192.0.2.{index}", 12345))
                    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                        login = await client.post("/auth/login", json={"password": "test-owner-password-for-cookie-tests"})
                        self.assertEqual(login.status_code, 200, login.text)
                        response = await client.post("/youtube-cookies", files={"file": (name, content, "text/plain")})
                        if content == COOKIE_SAMPLE:
                            self.assertEqual(response.status_code, 200, response.text)
                            self.assertNotIn("private-cookie-value", response.text)
                            self.assertEqual((await client.delete("/youtube-cookies")).status_code, 200)
                        else:
                            self.assertEqual(response.status_code, 400)
                            self.assertIn("Netscape", response.text)

        asyncio.run(exercise())

    def test_render_blueprint_uses_free_tier_without_disk(self):
        import yaml
        path = Path(__file__).resolve().parents[1] / "render.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        service = config["services"][0]
        self.assertEqual(service["plan"], "free")
        self.assertNotIn("disk", service)
        self.assertIn("DENO_INSTALL=/opt/render/project/src/.deno", service["buildCommand"])
        self.assertIn("https://deno.land/install.sh", service["buildCommand"])
        self.assertIn('NEONCLIPPING_DENO_PATH="$DENO_INSTALL/bin/deno"', service["startCommand"])
        self.assertIn('test -x "$NEONCLIPPING_DENO_PATH"', service["startCommand"])
        self.assertIn('"$DENO_INSTALL/bin/deno" --version', service["buildCommand"])
        requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("yt-dlp[default]==2026.10.26", requirements)
        variables = {item.get("key"): item for item in service["envVars"] if item.get("key")}
        self.assertEqual(variables["NEONCLIPPING_REQUIRE_AUTH"]["value"], "0")
        self.assertEqual(variables["NEONCLIPPING_PRIVATE_COOKIE_ADMIN"]["value"], "1")
        self.assertEqual(variables["NEONCLIPPING_PUBLIC_MODE"]["value"], "1")
        self.assertTrue(variables["NEONCLIPPING_AUTH_PASSWORD"]["sync"] is False)
        self.assertEqual(variables["NEONCLIPPING_MAKE_WORKERS"]["value"], "1")

    def test_make_worker_limit_is_bounded_and_safe_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app_module.make_candidate_workers(), 1)
        with patch.dict(os.environ, {"NEONCLIPPING_MAKE_WORKERS": "4"}, clear=False):
            self.assertEqual(app_module.make_candidate_workers(), 4)
        with patch.dict(os.environ, {"NEONCLIPPING_MAKE_WORKERS": "99"}, clear=False):
            self.assertEqual(app_module.make_candidate_workers(), 4)
        with patch.dict(os.environ, {"NEONCLIPPING_MAKE_WORKERS": "not-a-number"}, clear=False):
            self.assertEqual(app_module.make_candidate_workers(), 1)

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

    def test_make_local_source_does_not_shadow_curation(self):
        """Regression: /make must use module-level curation before lazy imports."""
        source_id = "makeregressionsource"
        source = app_module.DATA / f"{source_id}.mp4"
        source.write_bytes(b"test source placeholder")
        clip_id = "makeregressionexport"
        candidate = {
            "start": 1.0, "end": 8.0, "score": 70, "rank": 0.7,
            "title": "Regression clip", "hook": "", "reason": "test",
            "words": [{"start": 1.1, "end": 1.4, "text": "hello"}],
            "features": {},
        }

        def completed_export(_body):
            app_module.state[clip_id] = {"status": "done", "meta": {"duration": 7.0}}
            return {"job": clip_id}

        with patch.object(app_module, "probe", return_value={"w": 320, "h": 240, "duration": 12.0}), \
             patch("transcripts.cached_source", return_value=None), \
             patch("learn.load_weights", return_value={}), \
             patch("action.analyze", return_value={"motion": 0.5, "flash": 0, "strobe": 0, "cuts": 0}), \
             patch.object(app_module, "large_v3", return_value=None), \
             patch.object(app_module, "transcribe", return_value={"words": [{"start": 1.1, "end": 1.4, "text": "hello"}]}), \
             patch.object(app_module, "export", side_effect=completed_export), \
             patch.dict(app_module.state, {clip_id: {"status": "running"}}):
            async def exercise():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver") as client:
                    response = await client.post("/make", json={
                        "id": source_id, "count": 1, "min_len": 5, "max_len": 10,
                        "moments": [{"start": 1, "end": 8, "title": "Regression clip", "hook": "Test hook"}],
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    job = response.json()["job"]
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        record = app_module.state.get(job, {})
                        if record.get("status") != "running":
                            break
                        await asyncio.sleep(0.02)
                    self.assertEqual(record.get("status"), "done", record)
                    self.assertEqual(len(record.get("clips", [])), 1)
                    self.assertNotIn("curation", record.get("error", ""))

            try:
                asyncio.run(exercise())
            finally:
                source.unlink(missing_ok=True)
                app_module.state.pop(clip_id, None)


if __name__ == "__main__":
    unittest.main()
