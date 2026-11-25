"""GPU viewport (gl_viewport.py) against the software rasteriser, headless.  Skipped without OpenGL 3.3 (or moderngl)."""
import os, sys, struct, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import numpy as np
import gl_viewport as G

CTX = None
try:
    CTX = G.headless_context()
except Exception as ex:
    SKIP = str(ex)


@unittest.skipIf(CTX is None, "no OpenGL 3.3 context here")
class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import wpk_edit as E, wpk_view as V, wpk_browser as WB
        from synth import make_pack
        cls.E, cls.V, cls.WB = E, V, WB
        cls.dir = tempfile.mkdtemp()
        cls.path = os.path.join(cls.dir, "car.wpk")
        make_pack(cls.path)
        cls.pack = E.Pack.load(cls.path)
        cls.model = E.view_model(cls.pack, None)
        cls.r = G.Renderer(CTX)
        cls.size = (640, 360)
        cls.tex = CTX.texture(cls.size, 4)
        cls.fbo = CTX.framebuffer([cls.tex])

    def gm(self, ao=None):
        g = G.GPUModel(self.r, self.model, ao)
        g.vis = [t != 0xFFFF for t in g.tags]
        return g

    def draw(self, gm, cam=None, **kw):
        cam = cam or self.WB.Camera(-35, 14)
        view = G.View(cam, gm.centre, gm.radius, self.size)
        self.r.draw(gm, view, self.fbo, light=G.SHADINGS[G.DEFAULT_SHADING], **kw)
        img = np.frombuffer(self.fbo.read(components=3), np.uint8).reshape(self.size[1], self.size[0], 3)[::-1].astype(int)
        return view, img

    def test_silhouette_matches_software(self):
        V, WB = self.V, self.WB
        gm = self.gm()
        vis = [c for c in self.model[1] if c[8] != 0xFFFF]
        W, H = self.size
        for yaw, pitch in ((-35, 14), (90, 0), (0, 0), (0, 89)):
            cx, cy, hw = WB.view_extent(self.model[1], yaw, pitch, V.view_point, W / H)
            g = V.rasterize((self.model[0], vis, self.model[2]), yaw, pitch, W, H, window=(cx, cy, hw))
            s = min((W - 24) / (2 * hw), (H - 24) / (2 * hw * H / W))
            view = G.View(WB.Camera(yaw, pitch), gm.centre, gm.radius, self.size, perspective_on=False)
            hh = (H / 2) / s
            view.P = G.orthographic(hh * W / H, hh, 0.01, view.dist + gm.radius * 4)
            view.MVP = view.P @ view.V
            self.r.draw(gm, view, self.fbo, want_ids=True)
            a = np.frombuffer(self.r.id_fbo.read(components=4), np.uint8).reshape(H, W, 4)[::-1]
            gl = (a[..., 0].astype(int) + 256 * a[..., 1].astype(int)) > 0
            sw = g.M >= 0
            self.assertGreater((gl & sw).sum() / (gl | sw).sum(), 0.995, (yaw, pitch))

    def test_pick_hide_and_outline(self):
        gm = self.gm()
        view, base = self.draw(gm)
        body = next(c for c in gm.chunks if c[8] == 0)
        px, py = view.project([(body[0] + body[3]) / 2, (body[1] + body[4]) / 2, (body[2] + body[5]) / 2])
        ci = self.r.pick(px, py)
        self.assertIsNotNone(ci)
        self.assertEqual(gm.tags[ci], 0)
        self.assertIsNone(self.r.pick(2, 2))
        self.r.set_selection(gm, {0})
        _, sel = self.draw(gm)
        self.assertGreater((np.abs(sel - base).sum(2) > 0).sum(), 100)
        self.assertGreater((np.abs(sel - np.array([255, 200, 0])).sum(2) < 12).sum(), 20)       # the outline
        self.r.set_selection(gm, set())
        _, again = self.draw(gm)
        self.assertTrue((again == base).all())
        gm.vis = [t not in (0, 0xFFFF) for t in gm.tags]
        _, nobody = self.draw(gm, want_ids=True)
        self.assertNotEqual(self.r.pick(px, py), ci)
        self.assertGreater((np.abs(nobody - base).sum(2) > 30).sum(), 100)

    def test_ao_file(self):
        name = self.model[0][0][0]
        raw = b"WAO1" + struct.pack("<I", 1) + name.encode().ljust(48, b"\0") + struct.pack("<HH", 16, 16) + bytes([60]) * 256
        p = os.path.join(self.dir, "car.ao")
        open(p, "wb").write(raw)
        self.assertEqual(list(G.read_ao(p)), [name])
        self.assertEqual(G.read_ao(os.path.join(self.dir, "none.ao")), {})
        _, plain = self.draw(self.gm())
        g = self.gm(p)
        self.assertTrue(g.has_ao)
        _, dark = self.draw(g, use_ao=True)
        _, off = self.draw(g, use_ao=False)
        self.assertLess(dark.mean(), plain.mean())
        self.assertTrue((off == plain).all())

    def test_camera_orientation_agrees_with_view_point(self):
        gm = self.gm()
        v = G.View(self.WB.Camera(-35, 14), np.zeros(3), 1.0, (100, 100), perspective_on=False)
        q = (v.V @ np.array([0.3, 0.2, -0.4, 1.0]))[:3]
        sx, sy = self.V.view_point([0.3, 0.2, -0.4], -35, 14)
        self.assertAlmostEqual(q[0], sx, 6)
        self.assertAlmostEqual(q[1], sy, 6)

    def test_texture_override_and_lamp_switches(self):
        gm = self.gm()
        _, base = self.draw(gm, refl=0.0)
        ti = next(d.tex for d in gm.opaque if gm.vis[d.ci] and d.tex != 0xFFFF)   # a drawn part's texture
        im = np.zeros((8, 8, 4), np.uint8)
        im[..., 2], im[..., 3] = 255, 255                                # solid blue
        gm.set_overrides({ti: im})
        _, red = self.draw(gm, refl=0.0)
        self.assertGreater(np.abs(red - base).sum(), 5000)                    # (the pack paints it red, the override is blue)
        gm.set_overrides({})                                             # back to the pack's own picture
        _, back = self.draw(gm, refl=0.0)
        self.assertTrue((back == base).all())
        kinds = {d.kind for d in gm.blend}
        self.assertTrue(kinds <= {None, "brake", "lamp"})
        gm.show_brake = gm.show_lamps = False                           # must not fail, whatever the car has
        self.draw(gm, refl=0.0)
        gm.show_brake = gm.show_lamps = True


if __name__ == "__main__":
    unittest.main()
