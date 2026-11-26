import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image

from faceless_shorts.config import IMAGE_ENGINES, MFLUX_MODELS
from faceless_shorts.integrations import MfluxClient
from faceless_shorts.visuals import VisualManager

# Stands in for an mflux command: records its arguments and writes a PNG where --output says.
FAKE_MFLUX = """#!{python}
import json, sys
from PIL import Image
args = sys.argv[1:]
value = lambda flag: args[args.index(flag) + 1]
json.dump(args, open(value("--output") + ".args.json", "w"))
Image.new("RGBA", (int(value("--width")), int(value("--height"))), "red").save(value("--output"))
"""


class TestMfluxEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for spec in MFLUX_MODELS.values():
            path = os.path.join(self.tmp.name, spec["command"])
            with open(path, "w") as handle:
                handle.write(FAKE_MFLUX.format(python=sys.executable))
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        env = {"PATH": self.tmp.name + os.pathsep + os.environ.get("PATH", "")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_models_are_usable_sizes(self):
        for key, spec in MFLUX_MODELS.items():
            self.assertIn(key, IMAGE_ENGINES)  # every local model is an image engine of its own
            self.assertEqual(spec["width"] % 16, 0, key)
            self.assertEqual(spec["height"] % 16, 0, key)
            self.assertGreater(spec["height"], spec["width"], key)

    def test_generates_into_the_requested_file_and_format(self):
        out = os.path.join(self.tmp.name, "scenes", "image_scene_01.jpg")
        client = MfluxClient()
        self.assertIsNone(client.problem())
        self.assertEqual(client.generate("a fox", out, seed=7), out)
        with Image.open(out) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (client.spec["width"], client.spec["height"]))
        self.assertFalse(os.path.exists(out + ".mflux.png"))  # the intermediate PNG is removed

    def test_each_engine_runs_its_own_model_and_unknown_names_fall_back(self):
        client = MfluxClient("z-image-turbo")
        self.assertTrue(client.command().endswith("mflux-generate-z-image-turbo"))
        out = os.path.join(self.tmp.name, "z.jpg")
        client.generate("a fox", out)
        with open(out + ".mflux.png.args.json") as handle:
            args = __import__("json").load(handle)
        self.assertIn(MFLUX_MODELS["z-image-turbo"]["model"], args)
        self.assertNotIn("--base-model", args)
        self.assertEqual(MfluxClient("flux2-klein").spec["base_model"], "flux2-klein-4b")
        self.assertEqual(MfluxClient("no-such-model").key, next(iter(MFLUX_MODELS)))

    def test_landscape_canvas_swaps_the_size(self):
        out = os.path.join(self.tmp.name, "wide.jpg")
        MfluxClient().generate("a fox", out, portrait=False)
        with Image.open(out) as image:
            self.assertGreater(image.width, image.height)

    def test_engine_generates_one_image_at_a_time_through_the_visual_manager(self):
        visuals = VisualManager(cache_dir=os.path.join(self.tmp.name, "cache"), engine="flux2-klein")
        jobs = [{"prompt": f"scene {i}", "output_path": os.path.join(self.tmp.name, f"s{i}.jpg")} for i in range(3)]
        with mock.patch("faceless_shorts.visuals.ThreadPoolExecutor", wraps=__import__("concurrent.futures").futures.ThreadPoolExecutor) as pool:
            results = visuals.generate_ai_images_parallel(jobs, max_workers=4)
        self.assertEqual(pool.call_args.kwargs["max_workers"], 1)
        self.assertEqual(len(results), 3)
        for job in jobs:
            with Image.open(job["output_path"]) as image:
                self.assertEqual(image.format, "JPEG")

    def test_missing_mflux_names_the_install_command(self):
        with mock.patch("faceless_shorts.integrations.shutil.which", return_value=None):
            what, fix = MfluxClient().problem()
            self.assertIn("uv tool install", fix)
            self.assertIsNone(MfluxClient().generate("a fox", os.path.join(self.tmp.name, "none.jpg")))


if __name__ == "__main__":
    unittest.main()
