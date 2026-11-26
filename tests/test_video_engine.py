import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from faceless_shorts.config import VIDEO_ENGINES, DEFAULT_VIDEO_ENGINE
from faceless_shorts.integrations import FastMetalClient
from faceless_shorts.renderer import VideoRenderer
from faceless_shorts.visuals import VisualManager


class TestVideoEngine(unittest.TestCase):
    def test_default_engine_keeps_ken_burns(self):
        self.assertEqual(DEFAULT_VIDEO_ENGINE, "none")
        self.assertIn("fastmetal", VIDEO_ENGINES)
        self.assertEqual(VideoRenderer().video_engine, "none")
        self.assertEqual(VideoRenderer(video_engine="bogus").video_engine, "none")
        self.assertEqual(VideoRenderer(video_engine="fastmetal").video_engine, "fastmetal")

    def test_frames_cover_duration_on_model_grid(self):
        for duration in (0.0, 0.5, 1.0, 2.3, 3.0, 4.2, 5.0, 5.06, 9.0):
            frames = FastMetalClient.frames_for(duration)
            self.assertEqual((frames - 1) % 4, 0, duration)
            self.assertTrue(17 <= frames <= 81, duration)
            if duration <= 5.0:
                self.assertGreaterEqual(frames / 16, duration)
        self.assertEqual(FastMetalClient.frames_for(5.0), 81)
        self.assertEqual(FastMetalClient.frames_for(1.0), 17)

    def test_missing_install_degrades_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FastMetalClient(home=tmp)
            self.assertEqual(len(client.missing()), 3)
            self.assertIsNone(client.generate("a fox", os.path.join(tmp, "out.mp4"), 3.0))
            with mock.patch.dict(os.environ, {"FACELESS_FASTMETAL_HOME": tmp}):
                self.assertFalse(FastMetalClient.available())
                visuals = VisualManager(cache_dir=os.path.join(tmp, "cache"))
                self.assertIsNone(visuals.render_ai_video_clip("a fox", os.path.join(tmp, "clip.mp4"), 3.0))

    def test_extra_args_env_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACELESS_FASTMETAL_ARGS", None)
            self.assertEqual(FastMetalClient.extra_args(), ["--fast"])
        with mock.patch.dict(os.environ, {"FACELESS_FASTMETAL_ARGS": ""}):
            self.assertEqual(FastMetalClient.extra_args(), [])

    def test_options_command_offers_its_own_defaults(self):
        from faceless_shorts.cli import print_options

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_options()
        options = json.loads(out.getvalue())
        lists = {
            "language": "languages", "voice": "voices", "subtitleStyle": "subtitleStyles", "artStyle": "artStyles",
            "transition": "transitions", "effects": "effects", "quality": "qualities",
            "imageEngine": "imageEngines", "videoEngine": "videoEngines",
        }
        for field, name in lists.items():
            self.assertIn(options["defaults"][field], [c["value"] for c in options[name]], field)
        self.assertIn(options["defaults"]["music"], options["music"])
        self.assertIn("available", options["fastmetal"])


if __name__ == "__main__":
    unittest.main()
