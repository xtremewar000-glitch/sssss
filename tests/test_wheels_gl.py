"""Wheels tab with the GPU viewport (a fake gl_viewport module): the viewport replaces the software picture, View / Wheel boxes,
free orbit, wheel zoom, the model sent only when the pack changed, fall-back.  NOT a visual test."""
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
        self.cam, self.on_fail = cam, on_fail
        self.models, self.visible, self.focus, self.draws, self.upp, self.info = [], None, "unset", 0, 0.01, "Fake GPU"
    def set_model(self, m, ao=None): self.models.append(m)
    def set_visible(self, t): self.visible = set(t)
    def set_focus(self, c=None, r=None): self.focus = None if c is None else (np.asarray(c), r)
    def request_draw(self): self.draws += 1


fake = types.ModuleType("gl_viewport")
fake.import_error = lambda: None
fake.create_viewport = lambda parent, cam, on_fail, **kw: FakeGL(parent, cam, on_fail, **kw)
sys.modules["gl_viewport"] = fake
import wheels_tab as W
import wpk_view
from synth import make_pack


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = os.path.join(TMP, "maz_TEST.wpk")
        make_pack(cls.f)

    def tab(self):
        t = W.build_tab(faketk.W(), faketk.W())
        t.idx = [1]
        t.view_cb = types.SimpleNamespace(current=lambda *a: t.idx[0] if not a else t.idx.__setitem__(0, a[0]))   # the fake combobox has no index
        t.load(self.f)
        return t

    def test_viewport_replaces_the_picture(self):
        t = self.tab()
        self.assertIsNotNone(t.gl)
        self.assertEqual(len(t.gl.models), 1)
        self.assertTrue(t.q.empty())
        self.assertIsNotNone(t.gl.focus)

    def test_boxes_do_not_resend_the_model(self):
        t = self.tab()
        t.which.set("Both"); t.draw(); t.on_view(); t.draw()
        self.assertEqual(len(t.gl.models), 1)
        t.refresh()                                            # a texture edit ends here
        self.assertEqual(len(t.gl.models), 2)

    def test_view_box_turns_the_camera(self):
        t = self.tab()
        t.view_cb.current(0); t.on_view()
        v = wpk_view.VIEWS[0]
        self.assertEqual((t.cam.yaw, t.cam.pitch), (float(v[1]), float(v[2])))
        t.cam.zoom_by(3); t.fit_view()
        self.assertEqual(t.cam.zoom, 1.0)

    def test_orbit_wheel_and_scroll_bar(self):
        t = self.tab()
        t.vp_press(types.SimpleNamespace(x=0, y=0))
        for i in range(1, 50):
            t.vp_motion(types.SimpleNamespace(x=i * 20, y=i * 15))
        t.vp_release(None)
        self.assertTrue(-180 <= t.cam.yaw <= 180 and -180 <= t.cam.pitch <= 180)
        z = t.cam.zoom
        self.assertEqual(t.vp_wheel(types.SimpleNamespace(delta=120, num=0)), "break")
        self.assertGreater(t.cam.zoom, z)

    def test_failure_falls_back(self):
        t = self.tab()
        t.gl_failed("could not start", "boom")
        self.assertIsNone(t.gl)
        self.assertIn("Software picture", t.gl_info.cget("text"))
        t.draw()


if __name__ == "__main__":
    unittest.main()
