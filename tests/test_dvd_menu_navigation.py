"""Exercise compiled DVD navigation with VideoLAN's independent DVD VM.

Set DISCAUTHOR_TEST_LIBDVDNAV to a libdvdnav shared library and, when tools
are not on PATH, DISCAUTHOR_TEST_FFMPEG / _DVDAUTHOR / _SPUMUX. Then run:
    python -m unittest discover -s tests -p test_dvd_menu_navigation.py -v

Only a 1.5-second synthetic video is encoded. The real author.dvd() creates
the menus, button commands and IFOs; making an ISO is deliberately skipped.
No optical drive, existing project, installed application or media is changed.
"""
import ctypes
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


class DVDNav:
    """Small binding to public libdvdnav APIs, not DiscAuthor's preview."""
    def __init__(self, library, path):
        self.lib = ctypes.CDLL(str(library))
        ptr = ctypes.c_void_p
        integer = ctypes.c_int32
        output = ctypes.POINTER(integer)
        declarations = {
            "dvdnav_open": ([ctypes.POINTER(ptr), ctypes.c_char_p], integer),
            "dvdnav_close": ([ptr], integer),
            "dvdnav_get_next_block": ([ptr, ptr, output, output], integer),
            "dvdnav_wait_skip": ([ptr], integer),
            "dvdnav_menu_call": ([ptr, integer], integer),
            "dvdnav_title_play": ([ptr, integer], integer),
            "dvdnav_current_title_program": ([ptr, output, output, output], integer),
            "dvdnav_get_current_highlight": ([ptr, output], integer),
            "dvdnav_get_current_nav_pci": ([ptr], ptr),
            "dvdnav_button_activate": ([ptr, ptr], integer),
            "dvdnav_err_to_string": ([ptr], ctypes.c_char_p),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.lib, name)
            function.argtypes = args
            function.restype = result
        self.handle = ptr()
        if self.lib.dvdnav_open(ctypes.byref(self.handle), os.fsencode(path)) != 1:
            raise RuntimeError("libdvdnav could not open generated DVD")

    def close(self):
        if self.handle:
            self.lib.dvdnav_close(self.handle)
            self.handle = ctypes.c_void_p()

    def call(self, name, *args):
        result = getattr(self.lib, name)(self.handle, *args)
        if result != 1:
            message = self.lib.dvdnav_err_to_string(self.handle)
            raise AssertionError(f"{name}: {message!r}")

    def next_event(self):
        block = ctypes.create_string_buffer(2048)
        event, length = ctypes.c_int32(), ctypes.c_int32()
        self.call("dvdnav_get_next_block", block, ctypes.byref(event), ctypes.byref(length))
        if event.value == 13:  # DVDNAV_WAIT: decoder has drained its buffers.
            self.call("dvdnav_wait_skip")
        return event.value

    def advance_to_nav(self):
        for _ in range(10000):
            event = self.next_event()
            if event == 7:  # DVDNAV_NAV_PACKET: reached actual video/menu VOBU.
                return
            if event in (2, 8):
                raise AssertionError(f"Unexpected still/stop before NAV packet: {event}")
        raise AssertionError("DVD navigation did not settle")

    def position(self):
        title, pgc, program = ctypes.c_int32(), ctypes.c_int32(), ctypes.c_int32()
        self.call("dvdnav_current_title_program", ctypes.byref(title),
                  ctypes.byref(pgc), ctypes.byref(program))
        return title.value, pgc.value, program.value

    def highlight(self):
        value = ctypes.c_int32()
        self.call("dvdnav_get_current_highlight", ctypes.byref(value))
        return value.value

    def activate(self):
        pci = self.lib.dvdnav_get_current_nav_pci(self.handle)
        self.call("dvdnav_button_activate", pci)


class DVDMenuNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = {name: os.environ.get(f"DISCAUTHOR_TEST_{name.upper()}") or shutil.which(name)
                     for name in ("ffmpeg", "dvdauthor", "spumux")}
        cls.library = os.environ.get("DISCAUTHOR_TEST_LIBDVDNAV")
        if not cls.library or not all(cls.tools.values()):
            raise unittest.SkipTest("Integration test requires libdvdnav, ffmpeg, dvdauthor and spumux")
        cls.temporary = tempfile.TemporaryDirectory(prefix="discauthor-dvd-navigation-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.folder = Path(cls.temporary.name)
        env = mock.patch.dict(os.environ, {"DISCAUTHOR_DATA_DIR": str(cls.folder / "app-data")})
        env.start()
        cls.addClassCleanup(env.stop)
        sys.path.insert(0, str(ROOT))
        cls.addClassCleanup(sys.path.remove, str(ROOT))
        cls.author = importlib.import_module("author")
        cls.clip = cls.folder / "synthetic.mpg"
        command = [cls.tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", "color=c=blue:s=720x480:r=30000/1001",
                   "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1.5",
                   "-target", "ntsc-dvd", "-aspect", "16:9", "-threads", "2", str(cls.clip)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)

    def make_dvd(self, name, menu=True, remember=True, missing_top_entry=False):
        folder = self.folder / name
        (folder / "work").mkdir(parents=True)
        config = json.loads((ROOT / "assets/default-project.json").read_text())
        config.update(menu_enabled=menu, per_page=2, remember_selection=remember,
                      videos=[{"title": f"Program {i + 1}", "path": str(self.clip),
                               "info": {"duration": 1.5}}
                              for i in range(4 if menu else 1)])
        pages = self.author.pages_for(config) if menu else []
        tools = self.tools

        class Job:
            def stage(self, *args):
                pass

            def run(self, args, stdin=None, stdout=None, **kwargs):
                name = Path(args[0]).name
                if name == "mkisofs":
                    return  # The compiled DVD directory is libdvdnav's input.
                if name == "dvdauthor" and missing_top_entry:
                    # Negative control: recreate the legacy disc's missing
                    # VMGM entry without changing any VTS menu or title.
                    project = ET.parse(args[-1])
                    menus = project.find("vmgm/menus")
                    for pgc in menus.findall("pgc"):
                        menus.remove(pgc)
                    project.write(args[-1], encoding="utf-8", xml_declaration=True)
                result = subprocess.run([tools[name], *map(str, args[1:])],
                                        stdin=stdin, stdout=stdout or subprocess.PIPE,
                                        stderr=subprocess.PIPE, timeout=30)
                if result.returncode:
                    raise AssertionError(result.stderr.decode(errors="replace"))

        job = Job()
        job.config, job.out = config, folder

        def encode(_job, _source, dest, *args, **kwargs):
            shutil.copyfile(self.clip, dest)

        def render(_config, _buttons, _page, dest):
            picture = Image.new("RGB", (1920, 1080), "blue")
            picture.save(dest)
            return picture

        with mock.patch.object(self.author, "encode", side_effect=encode), \
                mock.patch.object(self.author, "render", side_effect=render):
            self.author.dvd(job, pages, 6000)
        return folder

    def open_dvd(self, folder):
        nav = DVDNav(self.library, folder / "disc")
        self.addCleanup(nav.close)
        nav.advance_to_nav()
        return nav

    def test_root_top_and_escape_return_to_programme_page_and_selection(self):
        folder = self.make_dvd("menu")
        for title in (2, 4):
            for menu in (2, 3, 0):  # DVD Title/Top, Root/Menu, and libdvdnav Escape.
                with self.subTest(title=title, menu=menu):
                    nav = self.open_dvd(folder)
                    nav.call("dvdnav_title_play", title)
                    nav.advance_to_nav()
                    self.assertEqual(nav.position()[0], title)
                    # Consume real title data before the remote call, not its post command.
                    self.assertEqual(nav.next_event(), 0)
                    nav.call("dvdnav_menu_call", menu)
                    nav.advance_to_nav()
                    self.assertEqual(nav.position()[:2], (0, (title - 1) // 2 + 1))
                    self.assertEqual(nav.highlight(), 2)
                    nav.activate()
                    nav.advance_to_nav()
                    self.assertEqual(nav.position()[0], title)
                    nav.close()

    def test_top_menu_obeys_disabled_selection_memory(self):
        nav = self.open_dvd(self.make_dvd("default-focus", remember=False))
        nav.call("dvdnav_title_play", 4)
        nav.advance_to_nav()
        nav.call("dvdnav_menu_call", 2)
        nav.advance_to_nav()
        self.assertEqual(nav.position()[:2], (0, 2))
        self.assertEqual(nav.highlight(), 1)
        nav.activate()
        nav.advance_to_nav()
        self.assertEqual(nav.position()[0], 3)

    def test_missing_title_entry_reproduces_top_menu_failure(self):
        nav = self.open_dvd(self.make_dvd("legacy-missing-entry", missing_top_entry=True))
        nav.call("dvdnav_title_play", 2)
        nav.advance_to_nav()
        self.assertEqual(nav.lib.dvdnav_menu_call(nav.handle, 2), 0)
        self.assertEqual(nav.position()[0], 2)
        nav.call("dvdnav_menu_call", 3)
        nav.advance_to_nav()
        self.assertEqual(nav.position()[:2], (0, 1))
        self.assertEqual(nav.highlight(), 2)

    def test_single_video_without_menu_still_stops(self):
        folder = self.make_dvd("no-menu", menu=False)
        self.assertIsNone(ET.parse(folder / "dvd.xml").find("vmgm/menus/pgc"))
        nav = self.open_dvd(folder)
        self.assertEqual(nav.position()[0], 1)
        for _ in range(10000):
            event = nav.next_event()
            if event == 8:  # DVDNAV_STOP
                break
        else:
            self.fail("Single-video DVD did not stop")


if __name__ == "__main__":
    unittest.main()
