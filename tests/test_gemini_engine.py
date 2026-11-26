import base64
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from PIL import Image

from faceless_shorts import integrations
from faceless_shorts.config import GEMINI_MODELS, IMAGE_ENGINES, SCRIPT_ENGINES, TTS_ENGINES, VIDEO_ENGINES
from faceless_shorts.integrations import GeminiClient, load_user_settings, save_user_settings
from faceless_shorts.script_generator import create_or_get_script
from faceless_shorts.tts_engine import GeminiTTS, align_words, resolve_gemini_voice, spread_words, voiced_span
from faceless_shorts.visuals import VisualManager

SCRIPT = {
    "title": "Why Foxes Hunt at Dawn",
    "hook": "Foxes know something about dawn that you do not.",
    "scenes": [
        {"index": i + 1, "text": f"Spoken line {i + 1}.", "visual_prompt": f"a fox, shot {i + 1}", "motion": "pan_left"}
        for i in range(4)
    ],
    "cta": "Follow for more.",
    "tags": ["#foxes"],
}


def png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (90, 160), (200, 120, 40)).save(out, format="PNG")
    return out.getvalue()


def wav_bytes(seconds: float = 2.0) -> bytes:
    """Half a second of silence, then a steady loud tone to the end, as Gemini sends speech: 24 kHz, mono, 16-bit."""
    quiet, total = 12000, int(seconds * 24000)
    samples = b"\x00\x00" * quiet + b"\x10\x27" * (total - quiet)
    out = io.BytesIO()
    with wave.open(out, "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(24000)
        sound.writeframes(samples)
    return out.getvalue()


class FakeGemini(BaseHTTPRequestHandler):
    """Answers like the Gemini API does (shapes copied from Google's REST documentation and real replies)."""

    requests = []  # every body sent to /interactions
    video = b"not a real clip"
    heard = []  # the word_info notes the transcription model answers with

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _key_problem(self):
        key = self.headers.get("x-goog-api-key")
        if key == "free-key":
            return 429, "Rate limit exceeded for model x (limit: 0 input tokens per minute on Free Tier)."
        if key != "good-key":
            return 400, "API key not valid. Please pass a valid API key."
        return None

    def do_GET(self):
        problem = None if self.headers.get("x-goog-api-key") == "free-key" else self._key_problem()
        self._send(*((problem[0], {"error": {"message": problem[1]}}) if problem else (200, {"models": []})))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeGemini.requests.append(body)
        kind = (body.get("response_format") or {}).get("type", "text")
        problem = self._key_problem()
        if problem and not (kind == "text" and problem[0] == 429):  # the free limits cover text
            return self._send(problem[0], {"error": {"message": problem[1]}})
        if body["model"] == "busy-model":
            return self._send(503, {"error": {"message": "busy-model is currently experiencing high demand", "code": "service_unavailable"}})
        if body["model"] == GEMINI_MODELS["transcribe"]["model"]:
            part = {"type": "text", "text": "heard", "annotations": FakeGemini.heard}
        elif kind == "audio":
            part = {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(wav_bytes()).decode()}
        elif kind == "image":
            part = {"type": "image", "mime_type": "image/png", "data": base64.b64encode(png_bytes()).decode()}
        elif kind == "video":
            part = {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(FakeGemini.video).decode()}
        else:
            part = {"type": "text", "text": "```json\n" + json.dumps(SCRIPT) + "\n```"}
        self._send(200, {"status": "completed", "steps": [{"type": "thought"}, {"type": "model_output", "content": [part]}]})


class TestGeminiEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeGemini)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeGemini.requests = []
        FakeGemini.heard = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {
            "FACELESS_SETTINGS_FILE": os.path.join(self.tmp.name, "cfg", "settings.json"),
            "GEMINI_API_URL": f"http://127.0.0.1:{self.server.server_port}",
            "GEMINI_API_KEY": "good-key",
            "GOOGLE_API_KEY": "",
            "GEMINI_TEXT_MODEL": "", "GEMINI_IMAGE_MODEL": "", "GEMINI_VIDEO_MODEL": "", "GEMINI_VIDEO_RESOLUTION": "",
            "GEMINI_TTS_MODEL": "", "GEMINI_TRANSCRIBE_MODEL": "",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.said = io.StringIO()
        redirect = contextlib.redirect_stdout(self.said)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_every_kind_of_engine_offers_gemini(self):
        for engines in (SCRIPT_ENGINES, TTS_ENGINES, IMAGE_ENGINES, VIDEO_ENGINES):
            self.assertIn("gemini", engines)

    def test_without_a_key_nothing_is_sent_and_the_fix_is_named(self):
        os.environ["GEMINI_API_KEY"] = ""
        self.assertFalse(GeminiClient.available())
        self.assertIn("--gemini-key", GeminiClient.problem()[1])
        self.assertIsNone(GeminiClient().generate_image("a fox", self.path("fox.jpg")))
        self.assertIsNone(GeminiClient().write_json("a script"))
        self.assertEqual(FakeGemini.requests, [])
        self.assertIn("aistudio.google.com/apikey", self.said.getvalue())

    def test_saved_key_wins_and_the_settings_file_is_private(self):
        self.assertEqual(GeminiClient.key_source(), ("good-key", "GEMINI_API_KEY"))
        save_user_settings({"gemini_api_key": "saved-key"})
        self.assertEqual(GeminiClient.key_source(), ("saved-key", "settings"))
        mode = stat.S_IMODE(os.stat(os.environ["FACELESS_SETTINGS_FILE"]).st_mode)
        self.assertEqual(mode, 0o600)

    def test_gemini_writes_the_script_for_a_topic(self):
        script = create_or_get_script("why foxes hunt at dawn", language="hi", script_engine="gemini")
        self.assertEqual(script.title, SCRIPT["title"])
        self.assertEqual([s.motion for s in script.scenes], ["pan_left"] * 4)
        sent = FakeGemini.requests[0]
        self.assertEqual(sent["model"], GEMINI_MODELS["text"]["model"])
        self.assertIn("Hindi", sent["input"])
        self.assertIn("visual_prompt", sent["system_instruction"])

    def test_a_busy_text_model_hands_over_to_the_fallback(self):
        os.environ["GEMINI_TEXT_MODEL"] = "busy-model"
        with mock.patch.object(integrations, "GEMINI_BUSY_WAIT", 0):
            script = create_or_get_script("why foxes hunt at dawn", script_engine="gemini")
        self.assertEqual(script.title, SCRIPT["title"])
        self.assertEqual([r["model"] for r in FakeGemini.requests], ["busy-model", "busy-model", GEMINI_MODELS["text"]["fallback"]])

    def test_a_refused_script_still_gives_a_script(self):
        os.environ["GEMINI_API_KEY"] = "wrong-key"
        script = create_or_get_script("why foxes hunt at dawn", script_engine="gemini")
        self.assertGreaterEqual(len(script.scenes), 3)
        self.assertEqual(len(FakeGemini.requests), 1)  # a key that is refused is not tried on the fallback model
        self.assertIn("API key not valid", self.said.getvalue())
        self.assertIn("built-in script writer", self.said.getvalue())

    def test_an_image_is_paid_for_once(self):
        visuals = VisualManager(cache_dir=self.path("cache"), engine="gemini")
        first = visuals.generate_ai_image("a fox at dawn", self.path("one.jpg"))
        second = visuals.generate_ai_image("a fox at dawn", self.path("two.jpg"))
        for made in (first, second):
            with Image.open(made) as image:
                self.assertEqual(image.format, "JPEG")
        self.assertEqual(len(FakeGemini.requests), 1)  # the second came from the cache
        sent = FakeGemini.requests[0]
        self.assertEqual(sent["model"], GEMINI_MODELS["image"]["model"])
        self.assertEqual(sent["response_format"], {"type": "image", "aspect_ratio": "9:16", "image_size": "1K"})

    def test_free_limits_refuse_images_and_say_what_is_needed(self):
        os.environ["GEMINI_API_KEY"] = "free-key"
        self.assertIsNone(GeminiClient().generate_image("a fox", self.path("fox.jpg")))
        self.assertIn("Free Tier", self.said.getvalue())
        self.assertIn("billing", self.said.getvalue())

    def test_estimated_word_times_stay_in_order_inside_the_speech(self):
        words = spread_words("Wait, an octopus can vanish. Really?", 1.0, 4.0)
        self.assertEqual([w.word for w in words], ["Wait", "an", "octopus", "can", "vanish", "Really"])
        self.assertEqual(words[0].start_sec, 1.0)
        for before, after in zip(words, words[1:]):
            self.assertLessEqual(before.end_sec, after.start_sec)
        self.assertLessEqual(words[-1].end_sec, 4.0)
        self.assertGreater(words[1].start_sec - words[0].end_sec, words[3].start_sec - words[2].end_sec)  # a comma is a pause
        self.assertEqual(spread_words("", 0, 1), [])
        start, end = voiced_span(wave.open(io.BytesIO(wav_bytes())).readframes(48000), 24000)
        self.assertEqual((start, end), (0.5, 2.0))
        self.assertEqual(resolve_gemini_voice("kore"), "Kore")
        self.assertEqual(resolve_gemini_voice("christopher"), "Charon")

    def test_script_words_take_the_times_the_transcription_heard(self):
        heard = [("It's", 0.2, 0.4), ("99", 0.5, 0.8), ("percent", 0.8, 1.2), ("skin.", 1.5, 1.9), ("Really", 2.4, 2.8)]
        words = align_words("It's 99% — colorblind skin. Really?", heard, 0.1, 3.0)
        self.assertEqual([w.word for w in words], ["It's", "99%", "—", "colorblind", "skin", "Really"])
        self.assertEqual((words[0].start_sec, words[0].end_sec), (0.2, 0.4))  # heard word for word
        self.assertEqual((words[4].start_sec, words[5].start_sec), (1.5, 2.4))
        # "99%" was heard as two words and "colorblind" not at all: they share the time between their neighbours.
        self.assertGreaterEqual(words[1].start_sec, 0.4)
        self.assertLessEqual(words[3].end_sec, 1.5)
        for before, after in zip(words, words[1:]):
            self.assertLessEqual(before.start_sec, after.start_sec)
        self.assertEqual(len(align_words("one two three", [], 0.0, 3.0)), 3)  # nothing heard: all estimated

    def test_voice_over_is_one_request_and_every_word_has_a_time(self):
        text = "An octopus can vanish. Its skin holds pigment."
        note = lambda word, start: {"type": "word_info", "text": word, "start_offset": f"{start}s", "end_offset": f"{start + 0.2:.3f}s"}
        FakeGemini.heard = [note("An", 0.6), note("octopus", 0.9), note("can", 1), note("vanish.", 1.2), note("Its", 1.5), note("pigment.", 1.8)]
        result = GeminiTTS("kore").synthesize(text, self.path("voice.wav"))
        with wave.open(result.audio_path) as sound:
            self.assertEqual(sound.getnframes() / sound.getframerate(), 2.0)
        self.assertEqual(result.duration_sec, 2.0)
        self.assertEqual([w.word for w in result.words], ["An", "octopus", "can", "vanish", "Its", "skin", "holds", "pigment"])
        self.assertEqual([w.start_sec for w in result.words[:5]], [0.6, 0.9, 1.0, 1.2, 1.5])
        self.assertEqual(result.words[-1].start_sec, 1.8)
        spoken = [r for r in FakeGemini.requests if r.get("response_format") == {"type": "audio"}]
        self.assertEqual([r["input"] for r in spoken], [text])
        self.assertEqual(spoken[0]["generation_config"], {"speech_config": [{"voice": "Kore"}]})
        self.assertEqual(spoken[0]["model"], GEMINI_MODELS["tts"]["model"])
        # Nothing heard back: the words are still all there, estimated inside the speech (which starts at 0.5 s).
        FakeGemini.heard = []
        estimated = GeminiTTS().synthesize(text, self.path("voice2.wav"))
        self.assertEqual(len(estimated.words), 8)
        self.assertEqual(estimated.words[0].start_sec, 0.5)
        self.assertIn("estimated", self.said.getvalue())

    def test_a_refused_voice_over_is_none_so_the_caller_can_use_another_voice(self):
        os.environ["GEMINI_API_KEY"] = "wrong-key"
        self.assertIsNone(GeminiTTS().synthesize("An octopus can vanish.", self.path("voice.wav")))
        self.assertFalse(os.path.exists(self.path("voice.wav")))

    def test_clip_length_stays_inside_what_the_model_makes(self):
        self.assertEqual([GeminiClient.seconds_for(d) for d in (0, 1.2, 3.0, 4.2, 15, 22)], [3, 3, 3, 5, 15, 15])
        os.environ["GEMINI_VIDEO_RESOLUTION"] = "8k"
        self.assertEqual(GeminiClient.video_resolution(), "720p")

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_a_scene_clip_is_generated_fitted_and_paid_for_once(self):
        source = self.path("source.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=24:duration=5",
             "-f", "lavfi", "-i", "sine=duration=5", "-shortest", "-pix_fmt", "yuv420p", source],
            check=True,
        )
        with open(source, "rb") as handle:
            FakeGemini.video = handle.read()
        visuals = VisualManager(cache_dir=self.path("cache"), width=270, height=480, fps=30)
        for name in ("a.mp4", "b.mp4"):
            clip = visuals.render_ai_video_clip("a fox at dawn", self.path(name), duration=4.2, engine="gemini")
            self.assertEqual(clip, self.path(name))
        self.assertEqual(len(FakeGemini.requests), 1)
        sent = FakeGemini.requests[0]
        self.assertEqual(sent["model"], GEMINI_MODELS["video"]["model"])
        self.assertEqual(sent["response_format"], {"type": "video", "aspect_ratio": "9:16", "duration": "5s", "resolution": "720p"})
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", clip],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(probe.stdout)
        self.assertEqual([s["codec_type"] for s in info["streams"]], ["video"])  # the model's own sound is dropped
        self.assertEqual((info["streams"][0]["width"], info["streams"][0]["height"]), (270, 480))
        self.assertAlmostEqual(float(info["format"]["duration"]), 4.2, delta=0.15)

    def test_config_command_checks_a_key_before_keeping_it(self):
        from faceless_shorts.cli import run_config

        os.environ["GEMINI_API_KEY"] = ""
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()) as err:
            run_config(mock.Mock(fastmetal_home=None, gemini_key="wrong-key"))
        self.assertIn("does not accept", err.getvalue())
        self.assertEqual(load_user_settings(), {})
        with mock.patch("sys.stdin", io.StringIO("good-key\n")):
            run_config(mock.Mock(fastmetal_home=None, gemini_key="-"))
        self.assertEqual(load_user_settings(), {"gemini_api_key": "good-key"})
        self.assertIn("ending in -key", self.said.getvalue())
        run_config(mock.Mock(fastmetal_home=None, gemini_key=""))
        self.assertEqual(load_user_settings(), {})

    def test_options_say_whether_gemini_can_be_used_without_giving_the_key_away(self):
        from faceless_shorts.cli import print_options

        def options():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                print_options()
            return out.getvalue()

        os.environ["GEMINI_API_KEY"] = "good-key-1234"
        text = options()
        self.assertNotIn("good-key-1234", text)
        ready = json.loads(text)
        self.assertEqual(
            {k: ready["gemini"][k] for k in ("configured", "source", "ending", "defaultVoice")},
            {"configured": True, "source": "GEMINI_API_KEY", "ending": "1234", "defaultVoice": "Charon"},
        )
        self.assertIn("Kore", [v["value"] for v in ready["gemini"]["voices"]])
        os.environ["GEMINI_API_KEY"] = ""
        missing = json.loads(options())
        for name in ("scriptEngines", "ttsEngines", "imageEngines", "videoEngines"):
            before = next(c for c in ready[name] if c["value"] == "gemini")
            after = next(c for c in missing[name] if c["value"] == "gemini")
            self.assertTrue(before["available"], name)
            self.assertFalse(after["available"], name)
            self.assertIn("--gemini-key", after["description"])


if __name__ == "__main__":
    unittest.main()
