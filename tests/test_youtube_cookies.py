import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import youtube_cookies


SAMPLE = (
    b"# Netscape HTTP Cookie File\n"
    b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\ttest-login-cookie\n"
    b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\ttest-signature-cookie\n"
)


class YoutubeCookieTests(unittest.TestCase):
    def test_cookie_validation_requires_netscape_youtube_entry(self):
        parsed = youtube_cookies.validate_cookie_text(SAMPLE)
        self.assertIn("test-login-cookie", parsed)
        with self.assertRaisesRegex(ValueError, "Netscape"):
            youtube_cookies.validate_cookie_text(b"browser-export-json")
        twitch = b".twitch.tv\tTRUE\t/\tTRUE\t2147483647\tsid\tvalue\n"
        with self.assertRaisesRegex(ValueError, "YouTube"):
            youtube_cookies.validate_cookie_text(twitch)

    def test_expired_youtube_cookies_are_rejected_at_upload(self):
        expired = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\tprivate-value\n"
        with self.assertRaisesRegex(ValueError, "expired"):
            youtube_cookies.validate_cookie_text(expired)

    def test_incomplete_auth_cookies_report_names_without_values(self):
        cases = (
            (b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tdummy-secret\n", "LOGIN_INFO"),
            (b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\tdummy-secret\n", "one of SAPISID"),
        )
        for raw, missing in cases:
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError) as caught:
                    youtube_cookies.validate_cookie_text(raw)
                self.assertIn(missing, str(caught.exception))
                self.assertNotIn("dummy-secret", str(caught.exception))

    def test_incomplete_saved_cookies_produce_actionable_import_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            youtube_cookies.cookie_path(root).write_text(
                "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\tdummy-secret\n",
                encoding="utf-8",
            )
            self.assertEqual(youtube_cookies.cookie_state(root), "incomplete")
            error = youtube_cookies.user_error(
                RuntimeError("Sign in to confirm you're not a bot"),
                "https://www.youtube.com/watch?v=abc",
                root,
            )
            self.assertIn("LOGIN_INFO", error)
            self.assertNotIn("dummy-secret", error)

    def test_cookie_state_reports_only_presence_and_expiration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(youtube_cookies.cookie_state(root, now=100), "missing")
            path = youtube_cookies.cookie_path(root)
            path.write_bytes(SAMPLE)
            self.assertEqual(youtube_cookies.cookie_state(root, now=100), "available")
            path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\tprivate-value\n", encoding="utf-8")
            self.assertEqual(youtube_cookies.cookie_state(root, now=100), "expired")

    def test_auth_errors_distinguish_missing_expired_and_rejected_cookies(self):
        auth_error = RuntimeError("Sign in to confirm you're not a bot; do not echo this private-cookie-value")
        url = "https://www.youtube.com/watch?v=abc"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = youtube_cookies.user_error(auth_error, url, root)
            self.assertIn("no cookies are configured", missing)
            path = youtube_cookies.cookie_path(root)
            path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\tprivate-cookie-value\n", encoding="utf-8")
            expired = youtube_cookies.user_error(auth_error, url, root)
            self.assertIn("have expired", expired)
            self.assertNotIn("private-cookie-value", expired)
            path.write_bytes(SAMPLE)
            rejected = youtube_cookies.user_error(auth_error, url, root)
            self.assertIn("rejected", rejected)
            self.assertNotIn("private-cookie-value", rejected)

    def test_cookie_copy_is_private_and_removed_after_ytdlp_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            youtube_cookies.save_cookies(root, SAMPLE)
            self.assertTrue(youtube_cookies.has_cookies(root))
            observed = {}
            seen_options = []

            class FakeDownloader:
                def __init__(self, options):
                    seen_options.append(dict(options))
                    observed.update(options)
                    observed["temp_exists_during_job"] = Path(options["cookiefile"]).exists() if "cookiefile" in options else False

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {"quiet": True}):
                    temporary = Path(observed["cookiefile"])
                    self.assertTrue(temporary.exists())
                    self.assertFalse(observed["no_warnings"])
                    self.assertTrue(callable(observed["logger"].warning))
                    self.assertEqual(temporary.read_bytes(), SAMPLE)
                    self.assertTrue(observed["temp_exists_during_job"])
                self.assertFalse(temporary.exists())

                for url in ("https://twitch.tv/channel", "https://kick.com/channel"):
                    with youtube_cookies.youtube_dl(url, root, {"quiet": True}):
                        pass
                    self.assertNotIn("cookiefile", seen_options[-1])

    def test_actionable_youtube_error_does_not_echo_extractor_details(self):
        message = youtube_cookies.user_error(
            RuntimeError("Sign in to confirm you're not a bot; private cookie token"),
            "https://www.youtube.com/watch?v=abc",
        )
        self.assertIn("Netscape cookies.txt", message)
        self.assertNotIn("private cookie token", message)
        self.assertEqual(youtube_cookies.user_error(RuntimeError("twitch problem"), "https://twitch.tv/a"), "twitch problem")

    def test_youtube_failures_get_safe_specific_messages(self):
        cases = (
            ("No supported JavaScript runtime could be found", "JavaScript challenge"),
            ("PO Token required for GVS request", "Proof-of-Origin (PO) token"),
            ("HTTP Error 429: Too Many Requests", "rate-limited this request"),
            ("HTTP Error 403: Forbidden", "denied the media request"),
            ("Requested format is not available", "returned no format"),
        )
        url = "https://www.youtube.com/watch?v=abc"
        for detail, expected in cases:
            with self.subTest(category=expected):
                error = youtube_cookies.YoutubeDownloadFailure(
                    youtube_cookies._classify_youtube_failure(detail + " cookie=do-not-expose")
                )
                message = youtube_cookies.user_error(error, url)
                self.assertIn(expected, message)
                self.assertNotIn("do-not-expose", message)

    def test_safe_yt_dlp_logger_keeps_only_categories(self):
        logger = youtube_cookies._SafeYtDlpLogger()
        logger.warning("No supported JavaScript runtime could be found; private-token=do-not-expose")
        self.assertEqual(logger.categories, {"javascript_runtime"})
        self.assertNotIn("do-not-expose", repr(logger.categories))

if __name__ == "__main__":
    unittest.main()
