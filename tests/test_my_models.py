import base64
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from faceless_shorts import my_models, stickman
from faceless_shorts.config import PHOTO_IMAGE_ENGINES, PHOTO_VIDEO_ENGINES
from faceless_shorts.integrations import GeminiClient, MfluxClient
from faceless_shorts.renderer import VideoRenderer
from faceless_shorts.script_generator import Scene
from faceless_shorts.visuals import VisualManager


class ModelsTestCase(unittest.TestCase):
    """Every test gets an empty model folder and no Gemini key, so nothing is sent anywhere."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        env = {"FACELESS_MY_MODELS_DIR": os.path.join(self.tmp, "models"),
               "FACELESS_SETTINGS_FILE": os.path.join(self.tmp, "settings.json"), "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.photo = os.path.join(self.tmp, "photo.png")
        Image.new("RGB", (600, 900), (200, 120, 40)).save(self.photo)


class TestLibrary(ModelsTestCase):
    def test_add_list_edit_remove(self):
        ravi = my_models.add("  Ravi  Kumar ", "man", self.photo, "curly hair, glasses")
        self.assertEqual((ravi.id, ravi.name), ("ravi-kumar", "Ravi Kumar"))
        with Image.open(ravi.photo) as kept:
            self.assertEqual(kept.mode, "RGB")
        bike = my_models.add("Red bike", "object", self.photo)
        self.assertEqual(bike.description, "")  # no key: nothing writes one
        self.assertEqual([m.id for m in my_models.all_models()], ["ravi-kumar", "red-bike"])
        self.assertEqual(my_models.get("RAVI KUMAR").id, "ravi-kumar")
        # Renaming keeps the id, so scripts that name the model by id still find it.
        my_models.edit("ravi-kumar", name="Ravi", description="beard")
        self.assertEqual((my_models.get("ravi-kumar").name, my_models.get("Ravi").description), ("Ravi", "beard"))
        my_models.remove("Red bike")
        self.assertEqual([m.id for m in my_models.all_models()], ["ravi-kumar"])
        self.assertFalse(os.path.exists(bike.folder))

    def test_refusals_say_why_and_leave_nothing_behind(self):
        my_models.add("Ravi", "man", self.photo)
        for args, why in (
            (("ravi", "man", self.photo), "already"),
            (("", "man", self.photo), "name"),
            (("x" * 41, "man", self.photo), "40"),
            (("Cat", "cat", self.photo), "kind"),
            (("Note", "object", __file__), "not a picture"),
        ):
            with self.assertRaises(ValueError) as caught:
                my_models.add(*args)
            self.assertIn(why, str(caught.exception))
        self.assertEqual(len(os.listdir(my_models.library_dir())), 1)
        # Same name as another model: the next free id is used.
        self.assertEqual(my_models.add("Ravi!", "man", self.photo).id, "ravi-2")

    def test_resolve(self):
        my_models.add("Ravi", "man", self.photo)
        my_models.add("Asha", "woman", self.photo)
        self.assertEqual([m.name for m in my_models.resolve(["asha", " ", "ravi", "Asha"])], ["Asha", "Ravi"])
        with self.assertRaises(ValueError) as caught:
            my_models.resolve(["nobody"])
        self.assertIn("Ravi", str(caught.exception))
        for i in range(2):
            my_models.add(f"Kid {i}", "boy", self.photo)
        with self.assertRaises(ValueError):
            my_models.resolve(["ravi", "asha", "kid-0", "kid-1"])

    def test_prompts_point_at_photos_or_describe_in_words(self):
        ravi = my_models.add("Ravi", "man", self.photo, "curly hair")
        bike = my_models.add("Bike", "object", self.photo)
        self.assertEqual(my_models.with_models("a market", [], photos=False), "a market")
        words = my_models.with_models("a market", [ravi, bike], photos=False)
        self.assertEqual(words, "a market. Featuring a man (curly hair) and an object")
        photos = my_models.with_models("a market", [ravi, bike], photos=True)
        self.assertIn("reference image 1 (curly hair)", photos)
        self.assertIn("reference image 2", photos)
        self.assertIn("same face", photos)

    def test_head_and_card(self):
        ravi = my_models.add("Ravi", "man", self.photo)
        face = my_models.head(ravi, 120)
        self.assertEqual((face.size, face.mode), ((120, 120), "RGBA"))
        self.assertEqual(face.getpixel((0, 0))[3], 0)       # outside the circle
        self.assertEqual(face.getpixel((60, 60))[3], 255)   # inside
        self.assertEqual(my_models.card(ravi, 90).size, (90, 90))


class TestEngines(ModelsTestCase):
    def test_which_engines_use_the_photo(self):
        self.assertEqual(PHOTO_IMAGE_ENGINES, {"gemini", "flux2-klein"})
        self.assertEqual(PHOTO_VIDEO_ENGINES, {"gemini", "stickman"})

    def test_mflux_switches_to_the_edit_command_for_references(self):
        client = MfluxClient("flux2-klein")
        runs = []

        def run(cmd, **kwargs):
            runs.append(cmd)
            Image.new("RGB", (8, 8)).save(cmd[cmd.index("--output") + 1])
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(MfluxClient, "command", lambda self, edit=False: "/bin/" + ("edit" if edit else "plain")), \
                mock.patch("subprocess.run", run):
            self.assertTrue(client.generate("a", os.path.join(self.tmp, "a.jpg"), refs=[self.photo]))
            self.assertTrue(client.generate("b", os.path.join(self.tmp, "b.jpg")))
            # A model with no edit command ignores the photos rather than failing.
            self.assertTrue(MfluxClient("z-image-turbo").generate("c", os.path.join(self.tmp, "c.jpg"), refs=[self.photo]))
        self.assertEqual(runs[0][0], "/bin/edit")
        self.assertEqual(runs[0][runs[0].index("--image-paths") + 1], self.photo)
        self.assertEqual(runs[1][0], "/bin/plain")
        self.assertNotIn("--image-paths", runs[1] + runs[2])

    def test_gemini_sends_the_photos_after_the_prompt(self):
        sent = []
        with open(self.photo, "rb") as handle:
            image = base64.b64encode(handle.read()).decode()

        def http(url, data=None, headers=None, timeout=None):
            sent.append(data)
            return 200, json.dumps({"steps": [{"type": "model_output", "content": [{"type": "image", "data": image}]}]}).encode()

        with mock.patch.object(GeminiClient, "key_source", staticmethod(lambda: ("k", "settings"))), \
                mock.patch("faceless_shorts.integrations._http_request", http):
            self.assertTrue(GeminiClient().generate_image("a cafe", os.path.join(self.tmp, "g.jpg"), refs=[self.photo]))
            self.assertTrue(GeminiClient().generate_image("a cafe", os.path.join(self.tmp, "h.jpg")))
        self.assertEqual([p["type"] for p in sent[0]["input"]], ["text", "image"])
        self.assertEqual(sent[0]["input"][1]["mime_type"], "image/png")
        self.assertEqual(sent[1]["input"], "a cafe")

    def test_visuals_give_photos_to_photo_engines_and_words_to_the_rest(self):
        ravi = my_models.add("Ravi", "man", self.photo, "curly hair")
        calls = []

        def fake(self_, prompt, output_path, portrait=True, refs=()):
            calls.append((prompt, list(refs)))
            Image.new("RGB", (8, 8)).save(output_path)
            return output_path

        visuals = VisualManager(cache_dir=os.path.join(self.tmp, "cache"), engine="gemini")
        with mock.patch.object(GeminiClient, "generate_image", fake):
            visuals.generate_ai_image("a cafe", os.path.join(self.tmp, "v.jpg"), models=[ravi])
        self.assertEqual(calls[0][1], [ravi.photo])
        self.assertIn("reference image 1", calls[0][0])
        # No key: Gemini fails; without fallback nothing is drawn, so a video never opens on a placeholder.
        with mock.patch.object(GeminiClient, "generate_image", lambda *a, **k: None):
            self.assertIsNone(visuals.generate_ai_image("x", os.path.join(self.tmp, "w.jpg"), models=[ravi], fallback=False))

    def test_scene_can_name_its_own_models(self):
        ravi = my_models.add("Ravi", "man", self.photo)
        asha = my_models.add("Asha", "woman", self.photo)
        renderer = VideoRenderer(featuring=[ravi])
        scene = lambda featuring: Scene(index=1, text="t", visual_prompt="p", featuring=featuring)
        self.assertEqual(renderer._scene_models(scene(None)), [ravi])
        self.assertEqual(renderer._scene_models(scene(["asha"])), [asha])
        self.assertEqual(renderer._scene_models(scene([])), [])
        self.assertEqual(renderer._scene_models(scene(["nobody"])), [])

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_stickman_wears_the_faces(self):
        ravi = my_models.add("Ravi", "man", self.photo)
        asha = my_models.add("Asha", "woman", self.photo)
        bike = my_models.add("Bike", "object", self.photo)
        clip = stickman.render_clip("Hello.", os.path.join(self.tmp, "s.mp4"), 0.3, 10, 270, 480, models=[ravi, asha, bike])
        self.assertTrue(clip and os.path.exists(clip))
        size = int(2 * stickman.HEAD * stickman.PHOTO_HEAD * stickman.S)
        cast = {"main": my_models.head(ravi, size), "other": my_models.head(asha, size), "prop": my_models.card(bike, 100)}
        plan = stickman.storyboard_for("Hello.", 0, {"action": "talk", "other": "wave"})
        frame = stickman.draw_frame(3.0, plan, 4.0, cast=cast)
        # The photo's orange (kept as JPEG, so within a step or two) shows where the plain heads would be paper-coloured.
        orange = lambda px: all(abs(a - b) <= 3 for a, b in zip(px, (200, 120, 40)))
        self.assertTrue(any(orange(frame.getpixel((x, y))) for x in range(0, frame.width, 9) for y in range(1250, 1450, 9)))


if __name__ == "__main__":
    unittest.main()
