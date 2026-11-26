import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from faceless_shorts.config import VIDEO_ENGINES, DEFAULT_VIDEO_ENGINE
from faceless_shorts.integrations import FastMetalClient, load_user_settings, save_user_settings
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

    def test_install_folder_is_remembered_and_problems_name_their_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"FACELESS_SETTINGS_FILE": os.path.join(tmp, "cfg", "settings.json")}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("FACELESS_FASTMETAL_HOME", None)
                install = os.path.join(tmp, "install")
                gone = os.path.join(tmp, "nowhere")
                save_user_settings({"fastmetal_home": gone})
                self.assertEqual(FastMetalClient().home, gone)
                # No folder at all: the folder is named, not a file deep inside it.
                path, fix = FastMetalClient().problem()
                self.assertEqual(path, gone)
                self.assertIn("--fastmetal-home", fix)
                # The folder is there but empty: the first step still to do is the clone.
                os.makedirs(install)
                save_user_settings({"fastmetal_home": install})
                path, fix = FastMetalClient().problem()
                self.assertTrue(path.endswith("mlx_wan_prompt_to_video.py"))
                self.assertIn("git clone", fix)
                # The environment variable wins over the saved folder; clearing the setting forgets it.
                with mock.patch.dict(os.environ, {"FACELESS_FASTMETAL_HOME": tmp}):
                    self.assertEqual(FastMetalClient().home, tmp)
                save_user_settings({"fastmetal_home": ""})
                self.assertEqual(load_user_settings(), {})

    def test_config_command_refuses_a_folder_that_is_not_there(self):
        from faceless_shorts.cli import run_config

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"FACELESS_SETTINGS_FILE": os.path.join(tmp, "settings.json")}):
                args = mock.Mock(fastmetal_home=os.path.join(tmp, "nowhere"))
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                    run_config(args)
                self.assertEqual(load_user_settings(), {})
                args = mock.Mock(fastmetal_home=tmp)
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    run_config(args)
                self.assertEqual(load_user_settings(), {"fastmetal_home": tmp})
                self.assertIn("not ready", out.getvalue())

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
