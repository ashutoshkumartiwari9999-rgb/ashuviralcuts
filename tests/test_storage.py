import os
import tempfile
import time
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def test_budget_rejects_work_without_removing_existing_media(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            source.write_bytes(b"keep me")
            with patch.dict(os.environ, {"NEONCLIPPING_STORAGE_BUDGET_MB": "128", "NEONCLIPPING_STORAGE_RESERVE_MB": "32"}):
                with self.assertRaises(storage.StorageLimitError):
                    storage.ensure_capacity(root, 97 * storage.MIB)
            self.assertEqual(source.read_bytes(), b"keep me")

    def test_cleanup_only_removes_old_temporary_files_and_preview_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "finished.mp4"
            staging = root / "download.part"
            cookie_temp = root / ".yt-cookie-leftover.txt"
            saved_cookies = root / ".youtube-cookies.txt"
            preview = root / "job-preview"
            preview.mkdir()
            (preview / "0.jpg").write_bytes(b"preview")
            media.write_bytes(b"user media")
            staging.write_bytes(b"temporary")
            cookie_temp.write_bytes(b"temporary cookie copy")
            saved_cookies.write_bytes(b"saved private cookie configuration")
            old = time.time() - 48 * 3600
            for item in (media, staging, cookie_temp, saved_cookies, preview, preview / "0.jpg"):
                os.utime(item, (old, old))

            result = storage.cleanup_temporary(root, older_than_hours=24)

            self.assertEqual(result["removed_files"], 2)
            self.assertEqual(result["removed_preview_folders"], 1)
            self.assertGreater(result["freed_bytes"], 0)
            self.assertTrue(media.exists())
            self.assertFalse(staging.exists())
            self.assertFalse(cookie_temp.exists())
            self.assertTrue(saved_cookies.exists())
            self.assertFalse(preview.exists())

    def test_upload_limit_is_clamped(self):
        with patch.dict(os.environ, {"NEONCLIPPING_MAX_UPLOAD_MB": "9999"}):
            self.assertEqual(storage.upload_limit_bytes(), 700 * storage.MIB)

    def test_upload_body_limit_rejects_before_multipart_parser(self):
        called = False
        async def app(scope, receive, send):
            nonlocal called
            called = True
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        scope = {
            "type": "http", "method": "POST", "path": "/upload",
            "headers": [(b"content-length", str(13 * storage.MIB).encode())],
        }
        with patch.dict(os.environ, {"NEONCLIPPING_MAX_UPLOAD_MB": "10"}):
            asyncio.run(storage.UploadRequestLimitMiddleware(app)(scope, receive, send))
        self.assertFalse(called)
        self.assertEqual(messages[0]["status"], 413)

    def test_cookie_upload_body_has_its_own_small_limit(self):
        called = False
        async def app(scope, receive, send):
            nonlocal called
            called = True
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        scope = {
            "type": "http", "method": "POST", "path": "/youtube-cookies",
            "headers": [(b"content-length", str(storage.MIB + 64 * 1024 + 1).encode())],
        }
        asyncio.run(storage.UploadRequestLimitMiddleware(app)(scope, receive, send))
        self.assertFalse(called)
        self.assertEqual(messages[0]["status"], 413)
        self.assertIn(b"cookies.txt", messages[1]["body"])

    def test_render_estimate_scales_with_resolution_and_duration(self):
        short = storage.estimate_output_bytes(30, 1080, 1080, 1920)
        long_4k = storage.estimate_output_bytes(60, 2160, 2160, 3840)
        self.assertGreater(long_4k, short * 5)
        self.assertGreater(short, 0)


if __name__ == "__main__":
    unittest.main()
