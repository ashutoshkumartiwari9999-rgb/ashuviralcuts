import tempfile
import unittest
from pathlib import Path

import layouts
import styling


class ShortsSettingsTests(unittest.TestCase):
    def test_vertical_default_and_supported_aspects(self):
        self.assertEqual(layouts.output_settings({}), (1080, 1920, 0))
        self.assertEqual(layouts.output_settings({"aspect": "square"}), (1080, 1080, 0))
        self.assertEqual(layouts.output_settings({"aspect": "landscape"}), (1920, 1080, 0))

    def test_text_watermark_is_off_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = styling.plan({}, Path(folder))
            self.assertIsNone(plan)

    def test_channel_watermark_supports_each_corner_and_size(self):
        positions = {
            "tl": "\\an7\\pos(59,144)",
            "tr": "\\an9\\pos(1020,144)",
            "bl": "\\an1\\pos(59,1766)",
            "br": "\\an3\\pos(1020,1766)",
        }
        with tempfile.TemporaryDirectory() as folder:
            for corner, marker in positions.items():
                with self.subTest(corner=corner):
                    plan = styling.plan({
                        "channel_name": "Ashu Official",
                        "channel_watermark": True,
                        "channel_watermark_pos": corner,
                        "channel_watermark_size": 5,
                    }, Path(folder))
                    self.assertTrue(plan.active)
                    plan.width, plan.height, plan.s = 1080, 1920, 1
                    _, events = plan.ass(30)
                    event = next(line for line in events if "Ashu Official" in line)
                    self.assertIn(marker, event)
                    self.assertIn("\\fs54", event)

    def test_watermark_requires_name_and_valid_position(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "Enter a channel name"):
                styling.plan({"channel_watermark": True}, Path(folder))
            with self.assertRaisesRegex(ValueError, "position must be tl"):
                styling.plan({"channel_watermark_pos": "center"}, Path(folder))


if __name__ == "__main__":
    unittest.main()
