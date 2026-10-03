"""Public-edition boundaries and generic-menu regressions.

Run with the project's Python environment, for example:
    python -m unittest discover -s tests -p test_public_edition.py -v

Only generated fixtures and temporary user-data directories are written.
Video probing is mocked; no encoder, authoring tool or optical drive is used.
"""
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
MODULES = {}


def setUpModule():
    global _module_directory, _module_environment
    _module_directory = tempfile.TemporaryDirectory(prefix="discauthor-public-tests-")
    _module_environment = mock.patch.dict(
        os.environ,
        {"DISCAUTHOR_DATA_DIR": str(Path(_module_directory.name) / "app-data")},
    )
    _module_environment.start()
    sys.path.insert(0, str(ROOT))
    for name in ("paths", "menu_templates", "fonts", "author"):
        module = importlib.import_module(name)
        if Path(module.__file__).resolve().parent != ROOT:
            raise RuntimeError("Run these tests in the public edition's source directory")
        MODULES[name] = module


def tearDownModule():
    sys.path.remove(str(ROOT))
    _module_environment.stop()
    _module_directory.cleanup()


class PublicTemplateBoundaryTests(unittest.TestCase):
    def test_absent_or_null_template_keeps_general_menu(self):
        templates = MODULES["menu_templates"]
        for enabled in (True, False):
            for config in ({"menu_enabled": enabled},
                           {"menu_enabled": enabled, "menu_template": None}):
                with self.subTest(config=config):
                    self.assertIsNone(templates.settings(config, strict=True))
                    self.assertEqual(templates.asset_paths(config), [])

    def test_non_null_templates_are_explicitly_rejected(self):
        templates = MODULES["menu_templates"]
        for enabled in (True, False):
            for value in ({"id": "private-template"}, {}, "private-template", "", False, 0):
                with self.subTest(menu_enabled=enabled, template=value):
                    with self.assertRaises(ValueError) as error:
                        templates.settings({"menu_enabled": enabled, "menu_template": value})
                    self.assertEqual(str(error.exception), templates.UNAVAILABLE)

    def test_author_validation_rejects_private_template_even_without_menu(self):
        author = MODULES["author"]
        for enabled in (True, False):
            with self.subTest(menu_enabled=enabled):
                config = {"format": "dvd", "menu_enabled": enabled,
                          "no_menu_end_action": "stop",
                          "menu_template": {"id": "private-template"},
                          "videos": [{"path": "not-needed.mp4", "title": "Program"}]}
                with mock.patch.object(author, "probe", side_effect=AssertionError("Unexpected media probe")):
                    with self.assertRaises(ValueError) as error:
                        author.validate(config)
                self.assertEqual(str(error.exception), MODULES["menu_templates"].UNAVAILABLE)

    def test_public_font_registry_contains_only_the_redistributable_font(self):
        fonts = MODULES["fonts"]
        self.assertEqual(set(fonts.BUNDLED_FONTS), {fonts.BUILTIN_FONT})
        with self.assertRaises(ValueError):
            fonts.resolve_font("builtin:unavailable-private-font")


class GeneralMenuTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="discauthor-general-menu-")
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.author = MODULES["author"]
        self.source = self.folder / "source.mp4"
        self.source.write_bytes(b"Generated placeholder; probing is mocked in this test.\n")
        self.config = json.loads((ROOT / "assets/default-project.json").read_text())
        self.config.update(title="Sample Program", menu_language="en", per_page=2,
                           font={"path": str(self.author.FONT), "index": 0},
                           videos=[{"path": str(self.source), "title": "Program A"},
                                   {"path": str(self.source), "title": "Program B"}])
        self.info = {"duration": 2.0, "width": 1920, "height": 1080,
                     "fps": "24000/1001", "field_order": "progressive",
                     "audio": True, "codec": "h264", "pix_fmt": "yuv420p",
                     "color_space": "bt709", "color_transfer": "bt709",
                     "color_primaries": "bt709", "color_range": "tv"}

    def validated(self, config):
        with mock.patch.object(self.author, "probe", return_value=self.info), \
                mock.patch.object(self.author.subprocess, "run", side_effect=AssertionError("No external tools allowed")):
            return self.author.validate(copy.deepcopy(config))

    def require_render_font(self):
        if not self.author.FONT.is_file():
            self.skipTest("Menu rendering requires the supported macOS system font")

    def test_automatic_menu_renders_and_preserves_titles_across_pages(self):
        self.require_render_font()
        self.config["videos"].append({"path": str(self.source), "title": "Program C"})
        config = self.validated(self.config)
        pages = self.author.pages_for(config)
        self.assertEqual(len(pages), 2)
        self.assertEqual([[button["label"] for button in page if "title" in button]
                          for page in pages], [["Program A", "Program B"], ["Program C"]])
        self.assertEqual([button["page"] for button in pages[0] if "page" in button], [1])
        self.assertEqual([button["page"] for button in pages[1] if "page" in button], [0])
        output = self.folder / "automatic-menu.png"
        image = self.author.render(config, pages[0], 0, output)
        self.assertEqual(image.size, (1920, 1080))
        self.assertTrue(output.is_file())
        self.assertIsNotNone(ImageChops.difference(image, Image.new("RGB", image.size, image.getpixel((0, 0)))).getbbox())

    def test_custom_background_and_button_rectangles_are_preserved(self):
        self.require_render_font()
        background = self.folder / "own-background.png"
        colour = (40, 60, 80)
        Image.new("RGB", (1920, 1080), colour).save(background)
        rectangles = [[100, 200, 700, 150], [1000, 600, 700, 150]]
        self.config.update(layout="custom", background=str(background), button_rects=rectangles)
        config = self.validated(self.config)
        buttons = self.author.pages_for(config)[0]
        self.assertEqual([button["rect"] for button in buttons], rectangles)
        image = self.author.render(config, buttons, 0, self.folder / "custom-menu.png")
        self.assertEqual(image.getpixel((0, 0)), colour)
        self.assertEqual(image.getpixel((1919, 1079)), colour)
        for x, y, width, height in rectangles:
            crop = image.crop((x, y, x + width, y + height))
            self.assertIsNotNone(ImageChops.difference(crop, Image.new("RGB", crop.size, colour)).getbbox())

    def test_custom_layout_still_rejects_buttons_outside_canvas(self):
        self.config.update(layout="custom", button_rects=[[100, 200, 700, 150], [1800, 600, 700, 150]])
        with self.assertRaises(ValueError):
            self.validated(self.config)


class PublicPresetPrivacyTests(unittest.TestCase):
    def test_default_assets_have_no_user_material_paths(self):
        preset = json.loads((ROOT / "assets/default-project.json").read_text())
        self.assertEqual(preset["videos"], [])
        self.assertEqual(preset["background"], "")
        self.assertIsNone(preset.get("menu_template"))
        self.assertEqual(preset["layout"], "auto")
        self.assertFalse(preset.get("output_dir"))

        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)

        for path in (ROOT / "assets").rglob("*.json"):
            if path.name.startswith("._"):
                continue
            for value in strings(json.loads(path.read_text())):
                with self.subTest(asset=str(path.relative_to(ROOT))):
                    self.assertNotIn("/Volumes/", value)
                    self.assertNotIn("/Users/", value)
                    self.assertNotIn("file://", value)


class CommunityDataIsolationTests(unittest.TestCase):
    def read_data_path(self, home, frozen=False, override=None):
        environment = os.environ.copy()
        environment.pop("DISCAUTHOR_DATA_DIR", None)
        environment.pop("PYTHONPATH", None)
        environment.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
        if override is not None:
            environment["DISCAUTHOR_DATA_DIR"] = str(override)
        code = ("import json,sys; sys.path.insert(0,sys.argv[1]); "
                "sys.frozen = sys.argv[2] == '1'; import paths; "
                "print(json.dumps({'data':str(paths.DATA_ROOT)}))")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT), "1" if frozen else "0"],
                                cwd=str(home), env=environment, capture_output=True, text=True, timeout=20, check=True)
        return Path(json.loads(result.stdout)["data"])

    def test_source_and_packaged_defaults_never_use_company_data_directory(self):
        for frozen in (False, True):
            with self.subTest(packaged=frozen), tempfile.TemporaryDirectory(prefix="discauthor-fake-home-") as directory:
                home = Path(directory)
                expected = home / "Library/Application Support/DiscAuthor Community"
                self.assertEqual(self.read_data_path(home, frozen=frozen), expected)
                self.assertTrue(expected.is_dir())
                self.assertFalse((home / "Library/Application Support/DiscAuthor").exists())

    def test_explicit_data_directory_is_respected_without_creating_default(self):
        with tempfile.TemporaryDirectory(prefix="discauthor-fake-home-") as directory:
            home = Path(directory)
            override = home / "custom-public-data"
            self.assertEqual(self.read_data_path(home, override=override), override)
            self.assertTrue(override.is_dir())
            self.assertFalse((home / "Library/Application Support").exists())


if __name__ == "__main__":
    unittest.main()
