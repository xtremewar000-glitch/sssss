"""Lamps module inside the Models page, sharing its ONE GPU viewport (a fake gl_viewport module): the module attaches its overlay on enter and
removes it on leave, orbit / pan / wheel / pick, one car + one Undo with Parts, dark scene.  NOT a visual test."""
import os, sys, types, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp()
os.environ["HOME"] = TMP
os.environ["VPK_VIEWPORT"] = "gl"
import faketk
MB = faketk.install()
import numpy as np


class FakeGL:
    def __init__(self, parent, cam, on_fail, width=0, height=0, on_draw=None):
        self.widget = faketk.W()
        self.cam, self.on_fail, self.on_draw = cam, on_fail, on_draw
        self.extras, self.models, self.visible, self.focus = [], [], None, "unset"
        self.draws, self.upp, self.info, self.look, self.bg, self.picked = 0, 0.01, "Fake GPU", None, None, None
        self.r = None

    def set_model(self, m, ao=None): self.models.append((m, ao))
    def set_visible(self, tags): self.visible = set(tags)
    def set_focus(self, c=None, r=None): self.focus = None if c is None else (np.asarray(c), r)
    def set_look(self, shading=None, *a): self.look = shading
    def set_background(self, t=None, b=None): self.bg = (t, b)
    def request_draw(self): self.draws += 1
    def set_selection(self, tags): self.selection = list(tags)
    def clear_model(self): pass
    def reset_background(self): self.bg = None
    def release(self): pass
    def pick(self, x, y): return self.picked


class FakeTri:
    def __init__(self): self.data = np.zeros((0, 7), np.float32)
    def set_triangles(self, d): self.data = np.asarray(d, np.float32).reshape(-1, 7)
    def clear(self): self.data = np.zeros((0, 7), np.float32)


fake = types.ModuleType("gl_viewport")
fake.import_error = lambda: None
fake.DEFAULT_SHADING = "Game: car select"
fake.TriLayer = FakeTri
fake.SHADINGS = ["Game: car select", "Game: Tokyo, night"]
fake.create_viewport = lambda parent, cam, on_fail, **kw: FakeGL(parent, cam, on_fail, **kw)
sys.modules["gl_viewport"] = fake
import lamp_edit as L
import wpk_edit as E
from synth import make_pack


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = os.path.join(TMP, "maz_TEST.wpk")
        make_pack(cls.f)

    def lamps_of_host(self):
        return self._t

    def tab(self):
        page = faketk.W()
        host = E.build_tab(page, faketk.W())
        host.open_path(self.f)
        self.n_open = len(host.gl.models)
        t = L.build_tab(page, faketk.W(), host=host)
        host.activate("lamps")
        self.host = host
        self._t = t
        return t

    def test_viewport_is_the_pages_and_the_overlay_attaches_and_detaches(self):
        t = self.tab()
        self.assertIs(t.gl, self.host.gl); self.assertIs(t.cam, self.host.cam)
        self.assertIn(t.layer, t.gl.extras)
        self.assertEqual(len(t.gl.models), self.n_open)             # entering the module does not resend the car
        self.assertEqual(t.gl.models[0][1], os.path.splitext(self.f)[0] + ".ao")
        self.host.activate("parts")
        self.assertNotIn(t.layer, t.gl.extras)                     # Parts shows no lamp overlay
        self.host.activate("lamps")
        self.assertIn(t.layer, t.gl.extras)
        self.assertEqual(len(t.gl.models), self.n_open)

    def test_selection_does_not_resend_the_model(self):
        t = self.tab()
        if not t.lamps:
            self.skipTest("synthetic pack has no lamps")
        n = len(t.gl.models)
        t.tree.selection_set(["0"]); t.on_select()
        t.tree.selection_set(["0"]); t.on_select()
        self.assertEqual(len(t.gl.models), n)
        self.assertGreater(len(t.layer.data), 0)                   # blue triangles for the selection
        self.assertEqual(len(t.layer.data) % 3, 0)
        self.assertIsNone(t.gl.focus)                              # the page's camera: no close-up of its own

    def test_edit_uses_the_pages_undo_and_resends_the_model(self):
        t = self.tab()
        if not t.lamps:
            self.skipTest("synthetic pack has no lamps")
        n, u = len(t.gl.models), len(self.host.undo)
        t.tree.selection_set(["0"]); t.on_select()
        t.nudge((0.5, 0, 0))
        self.assertEqual(len(self.host.undo), u + 1)               # one Undo for the whole page
        self.assertTrue(self.host.dirty)
        self.assertEqual(len(t.gl.models), n + 1)
        t.do_undo()
        self.assertEqual(len(self.host.undo), u)

    def test_orbit_is_free_and_does_not_snap_back(self):
        t = self.tab()
        t.vp_press(types.SimpleNamespace(x=100, y=100))
        for i in range(1, 40):
            t.vp_motion(types.SimpleNamespace(x=100 + i * 20, y=100 + i * 12))   # a long drag: past the top, round the back
        t.vp_release(types.SimpleNamespace(x=900, y=600))
        self.assertTrue(-180 <= t.cam.yaw <= 180 and -180 <= t.cam.pitch <= 180)
        yaw, pit = t.cam.yaw, t.cam.pitch
        t.render(); t.render()                                      # a redraw (a selection, a light) leaves the camera alone
        self.assertEqual((t.cam.yaw, t.cam.pitch), (yaw, pit))

    def test_wheel_zooms_and_stops_the_scroll_bar(self):
        t = self.tab()
        z = t.cam.zoom
        self.assertEqual(t.vp_wheel(types.SimpleNamespace(delta=120, num=0)), "break")
        self.assertGreater(t.cam.zoom, z)
        self.assertEqual(t.vp_wheel(types.SimpleNamespace(delta=0, num=5)), "break")

    def test_click_selects_the_lamp_piece(self):
        t = self.tab()
        if not t.lamps:
            self.skipTest("synthetic pack has no lamps")
        l = t.lamps[0]
        t.gl.picked = l.tag
        t.vp_press(types.SimpleNamespace(x=5, y=5)); t.vp_release(types.SimpleNamespace(x=5, y=5))
        self.assertIn(l.key, [x.key for x in t.selected()])
        t.gl.picked = None
        t.tree.selection_set(()); t.vp_press(types.SimpleNamespace(x=5, y=5)); t.vp_release(types.SimpleNamespace(x=5, y=5))
        self.assertEqual(t.selected(), [])

    def test_dark_scene_and_light(self):
        t = self.tab()
        t.v_night.set(True); t.render(); dark = t.gl.bg
        t.v_night.set(False); t.render()
        self.assertNotEqual(dark, t.gl.bg)
        t.v_light.set("Race, lamps on"); t.render()
        self.assertTrue(t.gl.visible)

    def test_no_viewport_says_so(self):
        self.tab()
        self.host.gl = None
        t = self.lamps_of_host()
        t.on_leave(); t.on_enter()                                  # must not raise without a GPU viewport
        t.render()


if __name__ == "__main__":
    unittest.main()
