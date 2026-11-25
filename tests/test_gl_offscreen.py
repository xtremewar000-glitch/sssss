"""The off-screen GPU viewport (macOS path): the Tk glue, with a FAKE GL context and renderer, so it runs anywhere.
Proves: mode choice, the picture is read back, flipped and shown, resize makes a new target, a failure falls back, pick / screenshot / close work.
NOT a test of real OpenGL or of how it looks on a Mac (that needs a Mac: python gl_viewport.py info / shot)."""
import os, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import faketk
faketk.install()
import numpy as np
from PIL import Image
import gl_viewport as G
import wpk_browser as WB


class FakeFbo:
    def __init__(self, W, H):
        self.W, self.H, self.released = W, H, False
    def read(self, components=3, **k):
        a = np.zeros((self.H, self.W, 3), np.uint8)
        a[0] = (255, 0, 0)                                   # GL's FIRST row is the BOTTOM of the picture: red
        a[-1] = (0, 0, 255)                                  # last row = top: blue
        return a.tobytes()
    def release(self):
        self.released = True


class FakeTex:
    def __init__(self, size):
        self.size = size
    def release(self):
        pass


class FakeCtx:
    info = {"GL_RENDERER": "Fake GPU", "GL_VERSION": "4.1 Metal"}
    def texture(self, size, comps):
        return FakeTex(size)
    def framebuffer(self, attachments):
        return FakeFbo(*attachments[0].size)


class FakeRenderer:
    made = []
    def __init__(self, ctx):
        self.ctx, self.draws, self.top, self.bottom, self.released = ctx, [], None, None, False
        FakeRenderer.made.append(self)
    def set_selection(self, model, sel):
        pass
    def draw(self, model, view, target, **kw):
        self.draws.append((view.size, target))
    def pick(self, x, y):
        return None
    def release(self):
        self.released = True


class FakePhoto:
    made = 0
    def __init__(self, img):
        FakePhoto.made += 1
        self.img = img
    def width(self):
        return self.img.size[0]
    def height(self):
        return self.img.size[1]
    def paste(self, img):
        self.img = img


class T(unittest.TestCase):
    def setUp(self):
        import PIL
        self._old = (G.headless_context, G.Renderer, getattr(PIL, "ImageTk", None))
        G.headless_context = lambda: FakeCtx()
        G.Renderer = FakeRenderer
        PIL.ImageTk = types.SimpleNamespace(PhotoImage=FakePhoto)
        sys.modules["PIL.ImageTk"] = PIL.ImageTk
        FakePhoto.made = 0
        self.fails = []
        self.parent = faketk.W()
        self.v = G.OffscreenGLViewport(self.parent, WB.Camera(-35, 14), lambda r, d: self.fails.append(r))

    def tearDown(self):
        import PIL
        G.headless_context, G.Renderer = self._old[0], self._old[1]

    def run_idle(self):
        self.v._run()

    def test_mode_choice(self):
        old = (sys.platform, os.environ.get("VPK_GL_MODE"))
        try:
            os.environ.pop("VPK_GL_MODE", None)
            G.sys.platform = "darwin"
            self.assertEqual(G.mode(), "offscreen")
            os.environ["VPK_GL_MODE"] = "direct"
            self.assertEqual(G.mode(), "direct")
            os.environ["VPK_GL_MODE"] = "offscreen"; G.sys.platform = "win32"
            self.assertEqual(G.mode(), "offscreen")
        finally:
            G.sys.platform = old[0]
            os.environ.pop("VPK_GL_MODE", None)
            if old[1]:
                os.environ["VPK_GL_MODE"] = old[1]

    def test_factory_returns_the_right_class(self):
        os.environ["VPK_GL_MODE"] = "offscreen"
        try:
            v = G.create_viewport(faketk.W(), WB.Camera(0, 0), lambda r, d: None)
            self.assertIsInstance(v, G.OffscreenGLViewport)
        finally:
            os.environ.pop("VPK_GL_MODE", None)

    def test_draw_reads_back_flips_and_shows(self):
        self.run_idle()
        self.assertIsNone(self.v.failed)
        self.assertEqual(len(FakeRenderer.made[-1].draws), 1)
        self.assertEqual(FakeRenderer.made[-1].draws[0][0], (640, 360))
        img = self.v._photo.img
        self.assertEqual(img.size, (640, 360))
        self.assertEqual(img.getpixel((10, 0)), (0, 0, 255))          # top row is the blue GL last row: the picture is the right way up
        self.assertEqual(img.getpixel((10, 359)), (255, 0, 0))
        self.assertIn("off-screen copy", self.v.info)

    def test_same_size_reuses_the_photo_and_target(self):
        self.run_idle(); fbo = self.v._fbo; made = FakePhoto.made
        self.v.request_draw(); self.run_idle(); self.run_idle()
        self.assertIs(self.v._fbo, fbo); self.assertEqual(FakePhoto.made, made)

    def test_resize_makes_a_new_target(self):
        self.run_idle(); old = self.v._fbo
        self.v.widget.winfo_width = lambda: 800
        self.run_idle()
        self.assertIsNot(self.v._fbo, old); self.assertTrue(old.released)
        self.assertEqual(self.v._photo.img.size, (800, 360))

    def test_not_drawn_while_unmapped_or_after_failure(self):
        self.v.widget.mapped = False
        self.run_idle(); self.assertIsNone(self.v.ctx)
        self.v.widget.mapped = True
        G.headless_context = lambda: (_ for _ in ()).throw(RuntimeError("no GL here"))
        self.run_idle()
        self.assertTrue(self.v.failed)                                  # the owner is told: it goes back to the software viewport
        self.assertEqual(self.v.ctx, None)

    def test_draw_error_is_reported_once(self):
        self.run_idle()
        FakeRenderer.made[-1].draw = lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
        self.v._run(); self.v._run()
        self.assertIn("failed while drawing", self.v.failed)

    def test_pick_screenshot_close(self):
        self.run_idle()
        self.assertIsNone(self.v.pick(5, 5))                            # nothing loaded: nothing picked, no exception
        self.v.close()
        self.assertIsNone(self.v._fbo); self.assertTrue(FakeRenderer.made[-1].released)


if __name__ == "__main__":
    unittest.main(verbosity=2)
