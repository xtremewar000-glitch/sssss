"""UI size: fit-to-window rules (pure) and the app wiring through the fake tkinter.  NOT a visual test: whether the real pages then fit
a given window needs a real Tk."""
import os, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from test_ui_m6 import build_app, set_main_prefs
import vpk_ui as UI


class FakeFont:
    def __init__(self, size): self.size = size
    def cget(self, k): return self.size
    def configure(self, **k): self.size = k.get("size", self.size)


class Pure(unittest.TestCase):
    def test_worst_overflow(self):
        self.assertIsNone(UI.worst_overflow([]))
        self.assertEqual(UI.worst_overflow([lambda: (1500, 1000, True), lambda: (900, 1000, True)]), 500)
        self.assertEqual(UI.worst_overflow([lambda: (800, 1000, True)]), -200)
        self.assertIsNone(UI.worst_overflow([lambda: (1500, 1000, False)]))           # a page that is not shown does not count
        self.assertIsNone(UI.worst_overflow([lambda: (None, 1000, True), lambda: 1 / 0]))   # junk / vanished widgets are skipped

    def test_pick_scale_takes_the_biggest_that_fits(self):
        need = lambda s: int(1500 * s) - 1100                      # the page is 1500 px wide at 100 %, the window gives 1100
        self.assertEqual(UI.pick_scale(UI.SCALE_STEPS, need), 0.70)
        self.assertEqual(UI.pick_scale(UI.SCALE_STEPS, lambda s: -10), 1.0)               # plenty of room: no shrinking
        self.assertEqual(UI.pick_scale(UI.SCALE_STEPS, lambda s: 999), UI.SCALE_STEPS[-1])   # never fits: the smallest, then scrollbars
        self.assertIsNone(UI.pick_scale(UI.SCALE_STEPS, lambda s: None))                  # nothing shown: leave it

    def test_uiscale_scales_from_the_start_sizes(self):
        fonts = {"TkDefaultFont": FakeFont(13), "TkFixedFont": FakeFont(-12)}
        tkfont = types.SimpleNamespace(nametofont=lambda n, root=None: fonts[n] if n in fonts else (_ for _ in ()).throw(KeyError(n)))
        u = UI.UIScale(tkfont, None)
        u.apply(0.7)
        self.assertEqual(fonts["TkDefaultFont"].size, 9)
        self.assertEqual(fonts["TkFixedFont"].size, -8)                                   # pixel sizes stay pixel sizes
        u.apply(1.0)
        self.assertEqual((fonts["TkDefaultFont"].size, fonts["TkFixedFont"].size), (13, -12))   # always from the start size: no drift
        u.apply(0.1)
        self.assertGreaterEqual(fonts["TkDefaultFont"].size, 6)


class App(unittest.TestCase):
    def test_app_builds_with_the_size_menu_and_fits(self):
        set_main_prefs({})
        root, app = build_app()
        self.assertEqual(app.ui_mode, "auto")
        app.fit_now()                                                                      # no real pages to measure: must not raise
        app.ui_var.set("0.8"); app.ui_scale_chosen()
        self.assertEqual(app.ui_mode, 0.8)
        app.ui_var.set("auto"); app.ui_scale_chosen()
        self.assertEqual(app.ui_mode, "auto")


class Loop(unittest.TestCase):
    """the UI must not rescale for ever: a <Configure> that is no resize, or fonts re-applied at the same size, must not start another fit"""

    def test_same_scale_does_not_touch_the_fonts(self):
        calls = []

        class F(FakeFont):
            def configure(self, **k):
                calls.append(k)
                FakeFont.configure(self, **k)
        fonts = {"TkDefaultFont": F(13), "TkFixedFont": F(-12)}
        tkfont = types.SimpleNamespace(nametofont=lambda n, root=None: fonts[n])
        u = UI.UIScale(tkfont, None)
        u.apply(0.8)
        n = len(calls)
        u.apply(0.8)
        self.assertEqual(len(calls), n)                                   # already 0.8: nothing touched

    def test_configure_without_a_size_change_is_ignored(self):
        set_main_prefs({})
        root, app = build_app()
        app.fit_now()
        app._fit_size = (1200, 800)
        app._fit_job = None
        same = types.SimpleNamespace(widget=app.root, width=1200, height=800)
        app.fit_soon(same)
        self.assertIsNone(app._fit_job)                                   # same size: no new fit
        other = types.SimpleNamespace(widget=app.root, width=1300, height=800)
        app.fit_soon(other)
        self.assertIsNotNone(app._fit_job)                                # a real resize still re-fits

    def test_circuit_breaker_stops_a_runaway(self):
        import time
        set_main_prefs({})
        root, app = build_app()
        app.fit_now()
        ran = []
        app.uiscale.apply = lambda s: ran.append(s) or s
        app._fit_times = [time.time()] * 4                                # four fits just now
        app.fit_now()
        self.assertEqual(ran, [])                                         # refused: no measuring, no font changes
        self.assertGreater(app._fit_hold, time.time())
        app.fit_soon()                                                    # and nothing is scheduled while it holds
        self.assertIsNone(app._fit_job)


if __name__ == "__main__":
    unittest.main()
