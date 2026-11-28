"""Unit tests for the director-control improvements:
- Text-aligned scene cut mapping (no drift on TTS number/hyphen expansion)
- Per-scene overrides (seed, video_engine, media_path, headline) & forward-compatible Scene.from_dict
- Subtitle position, words-per-screen, and toggle overrides
- Stickman non-Latin script detection & font fallback
- mflux local image caching & stock B-roll integration
- Decoupled transition whoosh SFX without background music
- CLI `script` drafting and `options` JSON payload
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from faceless_shorts import cli, stickman
from faceless_shorts.config import SUBTITLE_POSITIONS, VIDEO_ENGINES
from faceless_shorts.renderer import VideoRenderer
from faceless_shorts.script_generator import Scene, VideoScript, _direction_instructions, create_or_get_script
from faceless_shorts.subtitles import SubtitleGenerator
from faceless_shorts.tts_engine import WordTimestamp
from faceless_shorts.visuals import VisualManager


class TestDirectorControls(unittest.TestCase):
    def test_scene_cut_alignment_resists_number_expansion_drift(self):
        """TTS expanding '99%' into 'ninety nine percent' must not shift scene 2's cut."""
        scenes = [
            Scene(index=1, text="About 99% of people miss this.", visual_prompt="a"),
            Scene(index=2, text="Here is the real secret.", visual_prompt="b"),
        ]
        # Notice TTS produced 8 words for scene 1 (instead of 6 in scene.text.split())
        spoken = [
            WordTimestamp("About", 0.0, 0.3, 0.3),
            WordTimestamp("ninety", 0.3, 0.6, 0.3),
            WordTimestamp("nine", 0.6, 0.9, 0.3),
            WordTimestamp("percent", 0.9, 1.3, 0.4),
            WordTimestamp("of", 1.3, 1.5, 0.2),
            WordTimestamp("people", 1.5, 1.9, 0.4),
            WordTimestamp("miss", 1.9, 2.2, 0.3),
            WordTimestamp("this", 2.2, 2.5, 0.3),
            WordTimestamp("Here", 2.8, 3.1, 0.3),
            WordTimestamp("is", 3.1, 3.3, 0.2),
            WordTimestamp("the", 3.3, 3.5, 0.2),
            WordTimestamp("real", 3.5, 3.8, 0.3),
            WordTimestamp("secret", 3.8, 4.3, 0.5),
        ]
        renderer = VideoRenderer()
        bounds = renderer._map_words_to_scenes(scenes, spoken, total_duration=4.6)
        self.assertEqual(len(bounds), 2)
        # Scene 1 should end right where "Here" begins (2.8s), NOT at word index 6 ("miss" @ 1.9s)
        self.assertAlmostEqual(bounds[0][1], 2.8, places=2)
        self.assertAlmostEqual(bounds[1][0], 2.8, places=2)
        self.assertAlmostEqual(bounds[1][1], 4.6, places=2)

    def test_scene_from_dict_supports_new_fields_and_ignores_unknown(self):
        raw = {
            "title": "Test",
            "niche": "custom",
            "target_duration": 30,
            "hook": "Hi",
            "scenes": [
                {
                    "index": 1,
                    "text": "First line.",
                    "visual_prompt": "sunset",
                    "seed": 42,
                    "video_engine": "stickman",
                    "media_path": "/tmp/clip.mp4",
                    "headline": "BIG HOOK",
                    "future_unknown_field": True,
                }
            ],
            "cta": "Follow",
            "tags": ["#test"],
        }
        script = VideoScript.from_dict(raw)
        self.assertEqual(script.scenes[0].seed, 42)
        self.assertEqual(script.scenes[0].video_engine, "stickman")
        self.assertEqual(script.scenes[0].media_path, "/tmp/clip.mp4")
        self.assertEqual(script.scenes[0].headline, "BIG HOOK")

    def test_subtitle_position_and_words_per_screen_overrides(self):
        gen = SubtitleGenerator(style_name="hormozi", position="top", words_per_screen=2)
        self.assertEqual(gen.words_per_screen, 2)
        self.assertEqual(gen.style_config["margin_v"], SUBTITLE_POSITIONS["top"]["margin_v"])

    def test_stickman_detects_non_latin_scripts_and_loads_font(self):
        self.assertEqual(stickman._detect_script("नमस्ते दुनिया"), "hi")
        self.assertEqual(stickman._detect_script("こんにちは"), "ja")
        self.assertEqual(stickman._detect_script("مرحبا"), "ar")
        self.assertEqual(stickman._detect_script("Привет"), "ru")
        self.assertEqual(stickman._detect_script("Hello World"), "en")
        # Loading fonts should never raise even for non-Latin scripts
        for code in ("en", "hi", "ja", "ar", "ru"):
            self.assertIsNotNone(stickman._font(48, code))

    def test_direction_instructions_include_count_tone_and_stickman(self):
        text = _direction_instructions(scene_count=4, tone="witty and punchy", for_stickman=True)
        self.assertIn("4 scenes", text)
        self.assertIn("witty and punchy", text)
        self.assertIn("stick", text)
        self.assertIn("meditate", text)

    def test_create_or_get_script_respects_scene_count_for_builtin_builder(self):
        script = create_or_get_script("quantum tea brewing", language="en", use_llm=False, scene_count=3)
        self.assertEqual(len(script.scenes), 3)

    def test_mflux_images_are_cached_by_prompt_and_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            vm = VisualManager(cache_dir=os.path.join(tmp, "cache"), engine="flux2-klein")
            calls = []

            def fake_generate(self_client, prompt, output_path, seed=None, portrait=True, refs=()):
                calls.append((prompt, seed))
                with open(output_path, "wb") as f:
                    f.write(b"fake-jpg-bytes")
                return output_path

            with mock.patch("faceless_shorts.integrations.MfluxClient.generate", fake_generate):
                out1 = os.path.join(tmp, "s1.jpg")
                out2 = os.path.join(tmp, "s2.jpg")
                vm.generate_ai_image("a red fox at dawn", out1, seed=1234)
                vm.generate_ai_image("a red fox at dawn", out2, seed=1234)
            self.assertEqual(len(calls), 1)
            self.assertTrue(os.path.exists(out2))

    def test_stock_engine_registered_and_options_json_includes_new_fields(self):
        self.assertIn("stock", VIDEO_ENGINES)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_options()
        opts = json.loads(buf.getvalue())
        self.assertIn("subtitlePositions", opts)
        self.assertIn("vfx", opts)
        self.assertIn("motions", opts)
        self.assertIn("stickActions", opts)
        self.assertTrue(any(e["value"] == "stock" for e in opts["videoEngines"]))
        self.assertIn("preview", opts["subtitleStyles"][0])

    def test_cli_script_command_outputs_valid_json(self):
        buf = io.StringIO()
        args = mock.Mock(
            topic="coffee history",
            niche=None,
            language="en",
            script_engine="auto",
            scenes=4,
            tone="curious",
            stickman=False,
            json=True,
        )
        with redirect_stdout(buf):
            cli.run_script(args)
        data = json.loads(buf.getvalue())
        self.assertEqual(len(data["scenes"]), 4)
        self.assertIn("title", data)


if __name__ == "__main__":
    unittest.main()
