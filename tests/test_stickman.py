import os
import shutil
import subprocess
import tempfile
import unittest

from faceless_shorts import stickman
from faceless_shorts.config import VIDEO_ENGINES
from faceless_shorts.renderer import VideoRenderer
from faceless_shorts.script_generator import VIRAL_TEMPLATES, VideoScript


class TestStickman(unittest.TestCase):
    def test_engine_is_offered_and_moves_captions_under_the_ground(self):
        self.assertIn("stickman", VIDEO_ENGINES)
        renderer = VideoRenderer(video_engine="stickman")
        self.assertEqual(renderer.video_engine, "stickman")
        self.assertEqual(renderer.subtitles.style_config["margin_v"], stickman.CAPTION_MARGIN_V)
        self.assertEqual(VideoRenderer().subtitles.style_config["margin_v"], 700)

    def test_storyboard_comes_from_the_script_or_the_text(self):
        plan = stickman.storyboard_for("Stop lifting weights like an amateur.", 0)
        self.assertEqual(plan["action"], "lift")
        self.assertEqual(stickman.storyboard_for("Is it even possible?", 1)["bubble"], "?")
        # No keyword: the actions rotate with the scene number.
        self.assertEqual(stickman.storyboard_for("Plain words here.", 1)["action"], "point")
        # The script's own storyboard wins over the keywords, bubble included.
        plan = stickman.storyboard_for("Stop lifting weights.", 0, {"action": "wave", "headline": "Hello there"})
        self.assertEqual((plan["action"], plan["bubble"], plan["headline"]), ("wave", None, "Hello there"))
        # What a script may get wrong never raises: unknown actions and wrong types are replaced.
        plan = stickman.storyboard_for("Hello.", 0, {"action": "fly", "other": 7, "bubble": 42})
        self.assertEqual((plan["action"], plan["other"], plan["bubble"]), ("talk", None, "42"))
        self.assertEqual(stickman.storyboard_for("Hello.", 0, "not a dict")["action"], "talk")

    def test_expressive_actions_and_keyword_matching(self):
        self.assertGreaterEqual(len(stickman.ACTIONS), 30)
        self.assertEqual(stickman.storyboard_for("I just had a genius breakthrough!", 0)["action"], "idea")
        self.assertEqual(stickman.storyboard_for("Stop scrolling your phone at midnight.", 0)["action"], "sleep")
        self.assertEqual(stickman.storyboard_for("Stop scrolling your phone all day.", 0)["action"], "scroll")
        self.assertEqual(stickman.storyboard_for("That hilarious joke made me laugh.", 0)["action"], "laugh")
        self.assertEqual(stickman.storyboard_for("That was a huge mistake.", 0)["action"], "facepalm")
        self.assertEqual(stickman.storyboard_for("Feeling totally overwhelmed with burnout.", 0)["action"], "stress")

    def test_character_variants_draw_cleanly_solo_and_paired(self):
        for char in stickman.CHARACTERS:
            plan = stickman.storyboard_for(
                "", 0,
                {"action": "wave", "other": "idea", "character": char, "other_character": "kid", "bubble": "HI"},
            )
            self.assertEqual(plan["character"], char)
            self.assertEqual(plan["other_character"], "kid")
            frame = stickman.draw_frame(2.0, plan, 5.0)
            self.assertEqual(frame.size, (stickman.W * stickman.S, stickman.H * stickman.S), char)
        # Unknown character safely degrades to None
        self.assertIsNone(stickman.storyboard_for("", 0, {"character": "alien"})["character"])

    def test_every_action_draws_alone_and_as_the_second_figure(self):
        for name in stickman.ACTIONS:
            self.assertEqual(set(stickman.ACTIONS[name](1.3)), set(stickman.STAND), name)
            plan = stickman.storyboard_for("", 0, {"action": name, "other": name, "bubble": "?", "other_bubble": "!!", "headline": "one two three"})
            for t in (0.0, 0.4, 2.0, 4.9):
                frame = stickman.draw_frame(t, plan, 5.0, walk_in=True)
                self.assertEqual(frame.size, (stickman.W * stickman.S, stickman.H * stickman.S), name)

    def test_storyboard_survives_the_script_json_round_trip(self):
        script = VideoScript.from_dict(VIRAL_TEMPLATES["stoicism"].to_dict())
        self.assertEqual(script.scenes[0].stick["other"], "angry")
        for scene in script.scenes:
            self.assertIn(scene.stick["action"], stickman.ACTIONS)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_clip_has_the_length_and_size_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = stickman.render_clip("Why do foxes hunt at dawn?", os.path.join(tmp, "clips", "a.mp4"), 1.0, 12, 540, 960)
            self.assertTrue(clip and os.path.exists(clip))
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                 "-show_entries", "stream=width,height,nb_read_frames", "-of", "csv=p=0", clip],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(probe.stdout.strip(), "540,960,12")

    def test_missing_ffmpeg_degrades_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ["PATH"]
            os.environ["PATH"] = tmp
            try:
                self.assertIsNone(stickman.render_clip("Hello.", os.path.join(tmp, "a.mp4"), 0.2, 10, 540, 960))
            finally:
                os.environ["PATH"] = old


if __name__ == "__main__":
    unittest.main()
