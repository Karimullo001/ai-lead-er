from __future__ import annotations
import unittest
from core.plugins import get_plugin_manager
from core.media_generator import _enhance_image_prompt, _is_valid_image
from core.video_pipeline import is_valid_mp4
from core.website_engine import research_trends_for_project


class TestAgentOSFeatures(unittest.TestCase):
    def test_plugin_manager_registration(self):
        pm = get_plugin_manager()
        plugins = pm.list_plugins()
        plugin_names = [p["name"] for p in plugins]
        self.assertIn("github", plugin_names)
        self.assertIn("google_workspace", plugin_names)
        self.assertIn("figma", plugin_names)
        self.assertIn("notion", plugin_names)

    def test_image_prompt_enhancer(self):
        ui_prompt = _enhance_image_prompt("Modern AI analytics dashboard")
        self.assertIn("UI UX", ui_prompt)

        logo_prompt = _enhance_image_prompt("Futuristic cyber company logo")
        self.assertIn("logo", logo_prompt)

        photo_prompt = _enhance_image_prompt("Realistic portrait of astronaut on Mars")
        self.assertIn("photorealistic", photo_prompt)

    def test_image_and_video_validators(self):
        # PNG header
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 3000
        self.assertTrue(_is_valid_image(png_data))

        # Too small or invalid
        self.assertFalse(_is_valid_image(b"invalid data"))

        # MP4 header
        mp4_data = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 3000
        self.assertTrue(is_valid_mp4(mp4_data))
        self.assertFalse(is_valid_mp4(b"random bytes"))


if __name__ == "__main__":
    unittest.main()
