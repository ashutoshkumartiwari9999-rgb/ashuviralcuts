import tempfile
import unittest
import base64
import io
import os
from pathlib import Path
from unittest.mock import patch

import youtube_cookies
import youtube_cookie_store


SAMPLE = (
    b"# Netscape HTTP Cookie File\n"
    b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tLOGIN_INFO\ttest-login-cookie\n"
    b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSAPISID\ttest-signature-cookie\n"
)


class YoutubeCookieTests(unittest.TestCase):
    def setUp(self):
        youtube_cookies._clear_bot_challenge_cooldown()

    def tearDown(self):
        youtube_cookies._clear_bot_challenge_cooldown()

    def test_supabase_server_secret_is_apikey_only_and_legacy_jwt_remains_supported(self):
        key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        for api_key, expected_authorization in (
            ("sb_secret_test-only", None),
            ("eyJ.legacy-test-only", "Bearer eyJ.legacy-test-only"),
        ):
            with self.subTest(api_key_type=api_key.split("_")[0]):
                env = {
                    "NEONCLIPPING_COOKIE_STORE_URL": "https://project.supabase.co",
                    "NEONCLIPPING_COOKIE_STORE_BUCKET": "private-auth",
                    "NEONCLIPPING_COOKIE_STORE_KEY": api_key,
                    "NEONCLIPPING_COOKIE_ENCRYPTION_KEY": key,
                }
                observed = {}

                class FakeOpener:
                    def open(self, request, timeout):
                        observed.update({name.lower(): value for name, value in request.header_items()})
                        return io.BytesIO(b'{"public":false}')

                with patch.dict(os.environ, env, clear=True), patch(
                    "youtube_cookie_store.build_opener", return_value=FakeOpener()
                ):
                    youtube_cookie_store._request("GET", bucket_info=True)

                self.assertEqual(observed["apikey"], api_key)
                self.assertEqual(observed.get("authorization"), expected_authorization)

    def test_encrypted_remote_cookie_store_survives_local_restart_and_fallback(self):
        key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        env = {
            "NEONCLIPPING_COOKIE_STORE_URL": "https://project.supabase.co",
            "NEONCLIPPING_COOKIE_STORE_BUCKET": "private-auth",
            "NEONCLIPPING_COOKIE_STORE_KEY": "server-only-test-key",
            "NEONCLIPPING_COOKIE_ENCRYPTION_KEY": key,
        }
        remote = {"body": b""}
        calls = []

        class FakeOpener:
            def open(self, request, timeout):
                target = "bucket" if "/storage/v1/bucket/" in request.full_url else "object"
                calls.append((request.get_method(), target))
                if target == "bucket":
                    return io.BytesIO(b'{"public":false}')
                if request.get_method() == "POST":
                    remote["body"] = request.data
                    if SAMPLE in request.data:
                        raise AssertionError("cookie fixture was sent without encryption")
                    return io.BytesIO(b'{"Key":"private"}')
                if request.get_method() == "GET":
                    return io.BytesIO(remote["body"])
                if request.get_method() == "DELETE":
                    remote["body"] = b""
                    return io.BytesIO(b'{"message":"deleted"}')
                self.fail("unexpected remote storage operation")

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, env, clear=False), patch(
            "youtube_cookie_store.build_opener", return_value=FakeOpener()
        ):
            root = Path(temp)
            youtube_cookies.save_cookies(root, SAMPLE)
            self.assertFalse(youtube_cookies.cookie_path(root).exists())
            self.assertEqual(youtube_cookies.cookie_state(root), "available")
            self.assertEqual(youtube_cookies._stored_cookie_bytes(root), SAMPLE)

            # A simulated Render restart has a new, empty local data folder.
            restarted = root / "after-restart"
            restarted.mkdir()
            self.assertEqual(youtube_cookies.cookie_state(restarted), "available")
            temp_paths = []

            class FakeDownloader:
                def __init__(self, options):
                    cookie_file = options.get("cookiefile")
                    if cookie_file:
                        temp_paths.append(Path(cookie_file))
                        self.assertCookiePrivate = cookie_file.endswith(".txt") and Path(cookie_file).is_file()

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def extract_info(self, *_args, **_kwargs):
                    if not temp_paths:
                        raise RuntimeError("Sign in to confirm you're not a bot")
                    return {"id": "remote-cookie-fallback"}

            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", restarted, {}) as request:
                    result = request.extract_info("https://youtube.com/watch?v=abc", download=False)
            self.assertEqual(result["id"], "remote-cookie-fallback")
            self.assertEqual(len(temp_paths), 1)
            self.assertFalse(temp_paths[0].exists())
            self.assertIn(("POST", "object"), calls)
            youtube_cookies.remove_cookies(restarted)
            self.assertEqual(calls[-1], ("DELETE", "object"))
            self.assertEqual(youtube_cookies.cookie_state(restarted), "missing")

    def test_partial_or_invalid_persistent_store_configuration_fails_closed(self):
        partial = {"NEONCLIPPING_COOKIE_STORE_URL": "https://project.supabase.co"}
        with patch.dict(os.environ, partial, clear=True):
            self.assertEqual(youtube_cookies.cookie_state(Path("unused")), "storage_unavailable")
            with self.assertRaisesRegex(ValueError, "configuration is incomplete"):
                youtube_cookies.save_cookies(Path("unused"), SAMPLE)

    def test_storage_credentials_are_never_sent_to_non_supabase_host(self):
        env = {
            "NEONCLIPPING_COOKIE_STORE_URL": "https://attacker.example",
            "NEONCLIPPING_COOKIE_STORE_BUCKET": "private-auth",
            "NEONCLIPPING_COOKIE_STORE_KEY": "server-only-test-key",
            "NEONCLIPPING_COOKIE_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "valid Supabase HTTPS project URL"):
                youtube_cookies.save_cookies(Path("unused"), SAMPLE)

    def test_remote_storage_outage_is_not_reported_as_missing_or_logged(self):
        key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        env = {
            "NEONCLIPPING_COOKIE_STORE_URL": "https://project.supabase.co",
            "NEONCLIPPING_COOKIE_STORE_BUCKET": "private-auth",
            "NEONCLIPPING_COOKIE_STORE_KEY": "server-only-test-key",
            "NEONCLIPPING_COOKIE_ENCRYPTION_KEY": key,
        }
        class OfflineStore:
            def open(self, *_args, **_kwargs):
                raise OSError("secret-bearing provider response")

        with patch.dict(os.environ, env, clear=True), patch(
            "youtube_cookie_store.build_opener", return_value=OfflineStore()
        ):
            self.assertEqual(youtube_cookies.cookie_state(Path("unused")), "storage_unavailable")
            message = youtube_cookies.user_error(
                youtube_cookies.YoutubeDownloadFailure("authentication"),
                "https://youtube.com/watch?v=abc",
                Path("unused"),
            )
        self.assertIn("private cookie store is unavailable", message)
        self.assertNotIn("secret-bearing", message)

    def test_public_bucket_is_rejected_before_cookie_upload(self):
        key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
        env = {
            "NEONCLIPPING_COOKIE_STORE_URL": "https://project.supabase.co",
            "NEONCLIPPING_COOKIE_STORE_BUCKET": "private-auth",
            "NEONCLIPPING_COOKIE_STORE_KEY": "server-only-test-key",
            "NEONCLIPPING_COOKIE_ENCRYPTION_KEY": key,
        }

        class PublicBucketOpener:
            def open(self, request, timeout):
                if request.get_method() == "POST":
                    raise AssertionError("must not upload into a public bucket")
                return io.BytesIO(b'{"public":true}')

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, env, clear=True), patch(
            "youtube_cookie_store.build_opener", return_value=PublicBucketOpener()
        ):
            with self.assertRaisesRegex(ValueError, "not private"):
                youtube_cookies.save_cookies(Path(temp), SAMPLE)

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
                RuntimeError("Login required"),
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
        auth_error = RuntimeError("Login required; do not echo this private-cookie-value")
        url = "https://www.youtube.com/watch?v=abc"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = youtube_cookies.user_error(auth_error, url, root)
            self.assertIn("does not prove that the video itself requires sign-in", missing)
            self.assertIn("optional authentication fallback", missing)
            path = youtube_cookies.cookie_path(root)
            path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\tprivate-cookie-value\n", encoding="utf-8")
            expired = youtube_cookies.user_error(auth_error, url, root)
            self.assertIn("have expired", expired)
            self.assertNotIn("private-cookie-value", expired)
            path.write_bytes(SAMPLE)
            rejected = youtube_cookies.user_error(auth_error, url, root)
            self.assertIn("rejected", rejected)
            self.assertNotIn("private-cookie-value", rejected)

    def test_public_youtube_request_does_not_send_configured_cookies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            youtube_cookies.save_cookies(root, SAMPLE)
            seen_options = []

            class FakeDownloader:
                def __init__(self, options):
                    seen_options.append(dict(options))

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def extract_info(self, *_args, **_kwargs):
                    return {"id": "public-video"}

            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {"quiet": True}) as request:
                    self.assertEqual(request.extract_info("https://youtube.com/watch?v=abc", download=False)["id"], "public-video")
                self.assertEqual(len(seen_options), 1)
                self.assertNotIn("cookiefile", seen_options[0])
                self.assertFalse(list(root.glob(".yt-cookie-*.txt")))

    def test_bot_challenge_retries_once_with_private_cookie_copy_and_cleans_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            youtube_cookies.save_cookies(root, SAMPLE)
            seen = []
            temporary_files = []

            class FakeDownloader:
                def __init__(self, options):
                    cookie_file = options.get("cookiefile")
                    seen.append(bool(cookie_file))
                    if cookie_file:
                        cookie_path = Path(cookie_file)
                        temporary_files.append(cookie_path)
                        self.private_copy_exists = cookie_path.is_file()

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def extract_info(self, *_args, **_kwargs):
                    if not seen[-1]:
                        raise RuntimeError("Sign in to confirm you're not a bot")
                    return {"id": "authenticated-fallback"}

            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {"quiet": True}) as request:
                    result = request.extract_info("https://youtube.com/watch?v=abc", download=False)
                self.assertEqual(result["id"], "authenticated-fallback")
            self.assertEqual(seen, [False, True])
            self.assertEqual(len(temporary_files), 1)
            self.assertFalse(temporary_files[0].exists())
            self.assertFalse(list(root.glob(".yt-cookie-*.txt")))

    def test_bot_challenge_without_saved_session_is_not_mislabeled_as_video_auth(self):
        attempts = []

        class FakeDownloader:
            def __init__(self, options):
                attempts.append("cookiefile" in options)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, *_args, **_kwargs):
                raise RuntimeError("Sign in to confirm you're not a bot")

        with tempfile.TemporaryDirectory() as temp, patch("yt_dlp.YoutubeDL", FakeDownloader):
            root = Path(temp)
            with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {}) as request:
                with self.assertRaises(youtube_cookies.YoutubeDownloadFailure) as caught:
                    request.extract_info("https://youtube.com/watch?v=abc", download=False)
            message = youtube_cookies.user_error(caught.exception, "https://youtube.com/watch?v=abc", root)

        self.assertEqual(attempts, [False])
        self.assertEqual(caught.exception.youtube_category, "bot_challenge")
        self.assertIn("explicitly challenged", message)
        self.assertIn("distinct from a video being private", message)
        self.assertIn("upload the video file here", message)
        self.assertNotIn("saved owner session", message)

    def test_bot_challenge_cooldown_prevents_repeated_youtube_requests(self):
        attempts = []

        class FakeDownloader:
            def __init__(self, options):
                attempts.append("cookiefile" in options)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, *_args, **_kwargs):
                raise RuntimeError("Sign in to confirm you're not a bot")

        with tempfile.TemporaryDirectory() as temp, patch("yt_dlp.YoutubeDL", FakeDownloader):
            root = Path(temp)
            with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {}) as request:
                with self.assertRaises(youtube_cookies.YoutubeDownloadFailure) as first:
                    request.extract_info("https://youtube.com/watch?v=abc", download=False)
            with youtube_cookies.youtube_dl("https://youtube.com/watch?v=def", root, {}) as request:
                with self.assertRaises(youtube_cookies.YoutubeDownloadFailure) as second:
                    request.extract_info("https://youtube.com/watch?v=def", download=False)

        self.assertEqual(first.exception.youtube_category, "bot_challenge")
        self.assertEqual(second.exception.youtube_category, "bot_cooldown")
        self.assertEqual(attempts, [False])
        message = youtube_cookies.user_error(second.exception, "https://youtube.com/watch?v=def", root)
        self.assertIn("pausing new YouTube requests", message)
        self.assertIn("uploading the video file here", message)

    def test_new_owner_session_clears_bot_challenge_cooldown(self):
        youtube_cookies._mark_bot_challenge()
        self.assertGreater(youtube_cookies._cooldown_remaining(), 0)
        with tempfile.TemporaryDirectory() as temp:
            youtube_cookies.save_cookies(Path(temp), SAMPLE)
        self.assertEqual(youtube_cookies._cooldown_remaining(), 0)

    def test_cookie_fallback_is_not_used_for_po_token_or_network_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            youtube_cookies.save_cookies(root, SAMPLE)
            seen = []

            class FakeDownloader:
                def __init__(self, options):
                    seen.append("cookiefile" in options)

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def extract_info(self, *_args, **_kwargs):
                    raise RuntimeError("PO Token required; private-token=do-not-show")

            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {"quiet": True}) as request:
                    with self.assertRaises(youtube_cookies.YoutubeDownloadFailure) as caught:
                        request.extract_info("https://youtube.com/watch?v=abc", download=False)
            self.assertEqual(seen, [False])
            self.assertEqual(caught.exception.youtube_category, "po_token")
            self.assertNotIn("do-not-show", str(caught.exception))

    def test_unusual_traffic_rate_limit_does_not_trigger_cookie_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            youtube_cookies.save_cookies(root, SAMPLE)
            attempts = []

            class FakeDownloader:
                def __init__(self, options):
                    attempts.append("cookiefile" in options)

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def extract_info(self, *_args, **_kwargs):
                    raise RuntimeError("YouTube detected unusual traffic")

            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {}) as request:
                    with self.assertRaises(youtube_cookies.YoutubeDownloadFailure) as caught:
                        request.extract_info("https://youtube.com/watch?v=abc", download=False)

            self.assertEqual(attempts, [False])
            self.assertEqual(caught.exception.youtube_category, "rate_limited")

    def test_terminal_error_category_overrides_incidental_logger_warning(self):
        cases = (
            ("HTTP Error 403: Forbidden", "forbidden"),
            ("HTTP Error 429: Too Many Requests", "rate_limited"),
            ("YouTube detected unusual traffic", "rate_limited"),
            ("Sign in to confirm you're not a bot", "bot_challenge"),
            ("Connection timed out", "network"),
            ("PO Token required", "po_token"),
            ("No supported JavaScript runtime could be found", "javascript_runtime"),
        )
        for terminal_error, expected in cases:
            with self.subTest(category=expected):
                logger = youtube_cookies._SafeYtDlpLogger()
                logger.warning("Sign in to confirm you're not a bot; secret=do-not-log")
                self.assertEqual(
                    youtube_cookies._classify_youtube_failure(terminal_error, logger.categories),
                    expected,
                )
        self.assertEqual(
            youtube_cookies._classify_youtube_failure("yt-dlp stopped", {"authentication"}),
            "authentication",
        )
        self.assertEqual(
            youtube_cookies._classify_youtube_failure("yt-dlp stopped", {"bot_challenge"}),
            "bot_challenge",
        )

    def test_incidental_auth_warning_does_not_trigger_cookie_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            youtube_cookies.save_cookies(root, SAMPLE)
            attempts = []

            class FakeDownloader:
                def __init__(self, options):
                    self.logger = options["logger"]
                    attempts.append("cookiefile" in options)

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def extract_info(self, *_args, **_kwargs):
                    self.logger.warning("Sign in to confirm you're not a bot; secret=do-not-log")
                    raise RuntimeError("HTTP Error 403: Forbidden; signed-token=private")

            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", root, {}) as request:
                    with self.assertRaises(youtube_cookies.YoutubeDownloadFailure) as caught:
                        request.extract_info("https://youtube.com/watch?v=abc", download=False)

            self.assertEqual(attempts, [False])
            self.assertEqual(caught.exception.youtube_category, "forbidden")
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIn("do-not-log", str(caught.exception))

    def test_safe_failure_log_records_only_operation_category_and_fallback_flag(self):
        class FakeDownloader:
            def __init__(self, options):
                self.logger = options["logger"]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, *_args, **_kwargs):
                raise RuntimeError("HTTP Error 403: Forbidden signed-url=do-not-log")

        with tempfile.TemporaryDirectory() as temp, patch("yt_dlp.YoutubeDL", FakeDownloader):
            with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", Path(temp), {}) as request:
                with self.assertLogs("youtube_cookies", level="WARNING") as captured:
                    with self.assertRaises(youtube_cookies.YoutubeDownloadFailure):
                        request.extract_info("https://youtube.com/watch?v=abc", download=False)
        logged = "\n".join(captured.output)
        self.assertIn("operation=extract_info category=forbidden cookie_fallback=False", logged)
        self.assertNotIn("do-not-log", logged)
        self.assertNotIn("youtube.com", logged)

    def test_actionable_youtube_error_does_not_echo_extractor_details(self):
        message = youtube_cookies.user_error(
            RuntimeError("Login required; private cookie token"),
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
            ("Private video. Sign in if you've been granted access.", "identifies this video as private"),
            ("Sign in to confirm you're not a bot", "explicitly challenged"),
            ("Sign in to confirm you're not a bot", "upload the video file here"),
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

    def test_configured_deno_path_is_passed_to_yt_dlp_for_youtube_only(self):
        observed = []
        class FakeDownloader:
            def __init__(self, options): observed.append(options)
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def extract_info(self, *_args, **_kwargs): return {"id": "ok"}
        with patch.dict("os.environ", {"NEONCLIPPING_DENO_PATH": "/render/.deno/bin/deno"}):
            with patch("yt_dlp.YoutubeDL", FakeDownloader):
                with youtube_cookies.youtube_dl("https://youtube.com/watch?v=abc", Path("."), {}) as request:
                    request.extract_info("https://youtube.com/watch?v=abc", download=False)
                with youtube_cookies.youtube_dl("https://twitch.tv/channel", Path("."), {}):
                    pass
        self.assertEqual(observed[0]["js_runtimes"], {"deno": {"path": "/render/.deno/bin/deno"}})
        self.assertNotIn("js_runtimes", observed[1])

if __name__ == "__main__":
    unittest.main()
