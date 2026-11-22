import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import youtube_cookies


SAMPLE = (
    b"# Netscape HTTP Cookie File\n"
    b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret-cookie-value\n"
)


class YoutubeCookieTests(unittest.TestCase):
    def test_cookie_validation_requires_netscape_youtube_entry(self):
        parsed = youtube_cookies.validate_cookie_text(SAMPLE)
        self.assertIn("secret-cookie-value", parsed)
        with self.assertRaisesRegex(ValueError, "Netscape"):
            youtube_cookies.validate_cookie_text(b"browser-export-json")
        twitch = b".twitch.tv\tTRUE\t/\tTRUE\t2147483647\tsid\tvalue\n"
        with self.assertRaisesRegex(ValueError, "YouTube"):
            youtube_cookies.validate_cookie_text(twitch)

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
        self.assertIn("Netscape-format cookies.txt", message)
        self.assertNotIn("private cookie token", message)
        self.assertEqual(youtube_cookies.user_error(RuntimeError("twitch problem"), "https://twitch.tv/a"), "twitch problem")

if __name__ == "__main__":
    unittest.main()
