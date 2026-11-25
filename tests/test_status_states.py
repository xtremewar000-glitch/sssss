"""Unified status states (loading / empty / info / success / warning / error) on every page, and Linux maximised-window detection.
Pure rules of vpk_ui + the whole application built through the fake tkinter.  NOT a visual test."""
import os, re, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from test_ui_m6 import build_app, set_main_prefs, FW
import vpk_ui as UI
import wpk_view


class Rules(unittest.TestCase):
    def test_classify_samples(self):
        want = {"": "empty", "Open backfire.wpk (backfire.bfx beside it is opened too).": "empty", "Choose the wheels folder.": "empty",
                "Nothing open": "empty", "Open a car first.": "warning", "Select a lamp first.": "warning",
                "Reading 12 packs…": "loading", "Looking for the tiles' source pictures in C:/x ...": "loading",
                "Baking the ambient occlusion … (a few seconds)": "loading",
                "Saved a.wpk (the old file is kept as .bak).": "success", "Opened a.wpk.": "success", "Undone.": "success",
                "Cannot draw: boom": "error", "Could not open a.bin.": "error", "Not a tile number: x": "error",
                "Lamps copied to 2 pack(s); 1 failed": "warning", "UV layout not available: x": "error",
                "car.wpk  ·  5 stickers  ·  2 changed, not saved": "warning", "car.wpk  ·  5 stickers": "info",
                "Open a car .wpk first (Models > Open a car .wpk).": "warning"}
        for text, level in want.items():
            self.assertEqual(UI.classify(text), level, text)

    def test_statusline_set_get_level_and_views(self):
        s = UI.StatusLine("Open a file")
        self.assertEqual((s.get(), s.level), ("Open a file", "empty"))
        seen = []
        s._views.append(lambda t, l: seen.append((t, l)))
        s.set("Saved x.")                                    # classified
        s.set("whatever", "error")                           # explicit level wins
        s.set("Saved x.", "bogus")                           # unknown level -> classified
        s.loading("Reading…"); s.warning("w"); s.success("ok"); s.empty("")
        self.assertEqual([l for _, l in seen], ["success", "error", "success", "loading", "warning", "success", "empty"])
        self.assertTrue(UI.StatusLine().shown() == "" and UI.StatusLine(None).get() == "")
        s.set(None)
        self.assertEqual(s.get(), "")

    def test_every_state_has_colour_and_glyph_and_errors_are_not_colour_only(self):
        for st in UI.STATES:
            c, g = UI.state_style(st)
            self.assertTrue(c.startswith("#"))
        for st in ("warning", "error", "success", "loading"):
            self.assertTrue(UI.state_style(st)[1])           # a glyph too, so colour is never the only cue
        self.assertEqual(UI.state_style("nope"), UI.state_style("info"))
        self.assertEqual(len({UI.state_style(s)[0] for s in ("success", "warning", "error", "loading")}), 4)

    def test_broken_view_does_not_break_set(self):
        s = UI.StatusLine()
        s._views.append(lambda t, l: 1 / 0)
        s.set("x")
        self.assertEqual(s.get(), "x"); self.assertEqual(s._views, [])

    def test_every_literal_status_message_in_the_tabs_is_classified_sensibly(self):
        """no message the tabs can produce may fall outside STATES; the ones that mean 'something is wrong' must not read as success"""
        root = os.path.dirname(HERE)
        n = 0
        for f in ("backfire_edit.py", "wheels_tab.py", "lamp_edit.py", "meters_edit.py", "engine_tab.py", "wpk_edit.py", "vpk_tool.py"):
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r'status\.set\(\s*"((?:[^"\\]|\\.)*)"\s*(?:%|\)|,)', src):
                text = m.group(1)
                lv = UI.classify(text)
                self.assertIn(lv, UI.STATES)
                if re.match(r"(Cannot|Could not|Not a)", text):
                    self.assertEqual(lv, "error", (f, text))
                if text.rstrip(".").endswith("first"):
                    self.assertEqual(lv, "warning", (f, text))
                n += 1
        self.assertGreater(n, 50)

    def test_no_tab_still_uses_a_plain_stringvar_status_or_light_theme_grey(self):
        root = os.path.dirname(HERE)
        for f in ("backfire_edit.py", "wheels_tab.py", "lamp_edit.py", "meters_edit.py", "engine_tab.py", "wpk_edit.py", "vpk_tool.py"):
            with open(os.path.join(root, f), encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotRegex(src, r"self\.status\s*=\s*(tk\.StringVar|sv)\(", f)
            self.assertNotIn("textvariable=self.status", src, f)


class Maximised(unittest.TestCase):
    class R:
        def __init__(self, st="normal", zoomed=None, w=900, h=600, sw=1920, sh=1080):
            self.st, self.zoomed, self.w, self.h, self.sw, self.sh = st, zoomed, w, h, sw, sh
            self.calls = []
        def state(self, s=None):
            if s is not None:
                self.calls.append(("state", s))
                if self.no_state:
                    raise RuntimeError("bad argument zoomed")
                self.st = s
            return self.st
        no_state = False
        def attributes(self, name, value=None):
            if self.zoomed is None:
                raise RuntimeError("bad attribute")
            if value is not None:
                self.calls.append(("attr", value)); self.zoomed = int(value); return
            return self.zoomed
        def winfo_width(self): return self.w
        def winfo_height(self): return self.h
        def winfo_screenwidth(self): return self.sw
        def winfo_screenheight(self): return self.sh

    def test_windows_macos_report_zoomed_themselves(self):
        self.assertEqual(UI.window_state(self.R(st="zoomed")), "zoomed")
        self.assertEqual(UI.window_state(self.R(st="iconic")), "iconic")

    def test_linux_x11_zoomed_attribute(self):
        self.assertEqual(UI.window_state(self.R(zoomed=1)), "zoomed")
        self.assertEqual(UI.window_state(self.R(zoomed=0)), "normal")
        self.assertEqual(UI.window_state(self.R(zoomed="1")), "zoomed")

    def test_fallback_by_size_when_the_window_manager_does_not_say(self):
        self.assertEqual(UI.window_state(self.R(w=1920, h=1030)), "zoomed")           # fills the screen (minus a panel)
        self.assertEqual(UI.window_state(self.R(w=1360, h=860)), "normal")             # the default size is not maximised
        self.assertEqual(UI.window_state(self.R(w=1920, h=700)), "normal")             # full width but not full height
        self.assertTrue(UI.looks_maximised(1366, 700, 1366, 768) and not UI.looks_maximised(1000, 700, 1366, 768))

    def test_set_maximised_tries_both_ways(self):
        r = self.R(zoomed=0); r.no_state = True                                        # Linux: state('zoomed') is refused
        self.assertTrue(UI.set_maximised(r, True))
        self.assertEqual(r.calls, [("state", "zoomed"), ("attr", True)]); self.assertEqual(r.zoomed, 1)
        r2 = self.R()
        self.assertTrue(UI.set_maximised(r2, True)); self.assertEqual(r2.st, "zoomed")
        self.assertTrue(UI.set_maximised(r2, False)); self.assertEqual(r2.st, "normal")
        r3 = self.R(); r3.no_state = True                                              # neither works: False, no exception
        self.assertFalse(UI.set_maximised(r3, True))


class App(unittest.TestCase):
    def setUp(self):
        set_main_prefs(dict(width=1300, height=800, x=40, y=30, maximized=False))
        self.root, self.app = build_app()

    def test_every_page_has_a_status_line(self):
        a = self.app
        for name in ("models", "backfire", "wheels", "lamps", "meters", "engine"):
            tab = getattr(a, name, None)
            if tab is None:
                continue
            self.assertIsInstance(tab.status, UI.StatusLine, name)
            self.assertEqual(tab.status.level, UI.classify(tab.status.get()), name)
        self.assertIsInstance(a.status, UI.StatusLine)

    def test_error_and_warning_paths(self):
        a = self.app
        a.lamps.pack = None
        a.models.pack = None
        a.models_cmd("save")
        self.assertEqual(a.models.status.level, "warning")
        a.lamps.status.set("boom", "error"); self.assertEqual(a.lamps.status.level, "error")
        a.wheels.status.set("Reading 3 packs…"); self.assertEqual(a.wheels.status.level, "loading")
        a.refresh_all()
        self.assertEqual(a.status.level, "empty")                                        # "Nothing open"

    def test_linux_maximised_window_is_remembered(self):
        a = self.app
        a.root.st = "normal"
        a.root.attributes = lambda name, value=None: 1 if value is None else None       # X11: -zoomed = 1
        a.quit()
        main = wpk_view.load_prefs()["main"]
        self.assertTrue(main["maximized"])
        self.assertEqual((main["width"], main["height"]), (1300, 800))                    # the normal size is kept


if __name__ == "__main__":
    unittest.main(verbosity=2)
