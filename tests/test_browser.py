import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import numpy as np
import wpk_edit as E, wpk_view as V, wpk_browser as B
from synth import make_pack


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.path = os.path.join(cls.dir, "maz_TEST.wpk")
        cls.pack = make_pack(cls.path)

    def rows(self, hidden=()):
        return B.build_rows(self.pack, set(hidden), E.tag_name, E.engine_draws)

    def test_roundtrip_bytes_identical(self):
        with open(self.path, "rb") as fh:
            a = fh.read()
        self.assertEqual(self.pack.to_bytes(), a)           # load -> to_bytes reproduces the file exactly

    def test_categories(self):
        for tag, cat in ((0, "Body"), (0x100, "Wheels"), (0x12B, "Wheels"), (0x122, "Wing"), (0x190, "Wing"), (0x126, "Bonnet"),
                         (0x181, "Bonnet"), (0x111, "Lights"), (0x150, "Lights"), (0x121, "Mirrors"), (0x130, "Exhaust"),
                         (0xFFFF, "Other"), (0x1B0, "Other"), (0x205, "Other")):
            self.assertEqual(B.category(tag), cat, hex(tag))

    def test_rows_counts(self):
        r = {x.tag: x for x in self.rows(hidden={0x123})}
        self.assertEqual(r[0].draws, 1); self.assertEqual(r[0].verts, 8); self.assertEqual(r[0].tris, 12)
        self.assertFalse(r[0x123].visible); self.assertTrue(r[0].visible)
        self.assertEqual(r[0x122].name, "Wing A")

    def test_filter(self):
        rows = self.rows()
        tags = lambda q, c=(): sorted(x.tag for x in B.filter_rows(rows, q, c))
        self.assertEqual(tags("wing"), [0x122, 0x123])
        self.assertEqual(tags("0x123"), [0x123])
        self.assertEqual(tags("291"), [0x123])                       # decimal tag
        self.assertEqual(tags("wing a"), [0x122])                    # every word must match
        self.assertEqual(tags("", ("Wheels",)), [0x100, 0x101])
        self.assertEqual(tags("wing", ("Wheels",)), [])
        self.assertIn(0x122, tags("borrowed"))                       # wing A uses texture #1 "borrowed_tex"
        self.assertEqual(tags("nonsense"), [])
        self.assertEqual(len(tags("")), 7)
        self.assertEqual([c for c, _ in B.group_rows(rows)], ["Body", "Wheels", "Wing", "Bonnet", "Lights"])

    def test_valid_selection(self):
        self.assertEqual(B.valid_selection([0, 0x999, 0x100], self.pack), [0, 0x100])
        self.assertEqual(B.valid_selection([0], None), [])

    def test_camera(self):
        c = B.Camera(0, 0)
        c.set_angles(0, 0); c.orbit(0, 100 / 0.4); self.assertAlmostEqual(c.pitch, 100.0)
        c.orbit(0, 10000); self.assertTrue(-180 <= c.pitch < 180)
        c.set_angles(30, 100); c.orbit(10, 0); self.assertLess(c.yaw, 30)      # upside down: the drag direction is flipped
        c.set_angles(30, 20); c.orbit(10, 0); self.assertGreater(c.yaw, 30)
        c.set_angles(0, 170); c.orbit(0, 100); self.assertLess(c.pitch, 0)     # goes over the top and keeps going
        c.set_angles(170, 0); c.orbit(50, 0); self.assertTrue(-180 <= c.yaw < 180)
        for _ in range(100): c.zoom_by(2)
        self.assertEqual(c.zoom, B.Camera.MAX_ZOOM)
        c.fit(); self.assertEqual((c.zoom, c.pan), (1.0, [0.0, 0.0]))
        w, upp = c.window(1, 2, 3, 600, 340)
        self.assertEqual(w, (1, 2, 3))                               # fitted: window is the fitted frame
        hh = 3 * 340 / 600; self.assertAlmostEqual(upp, 1 / min((600 - 24) / 6, (340 - 24) / (2 * hh)))
        c.zoom_by(2); c.pan_px(10, 0, upp)
        w2, upp2 = c.window(1, 2, 3, 600, 340)
        self.assertAlmostEqual(w2[2], 1.5); self.assertLess(w2[0], 1); self.assertAlmostEqual(upp2, upp / 2)

    def render(self, yaw=-35, pitch=14, hidden=(), W=480, H=300, window=None):
        model = E.view_model(self.pack, None)
        show = {c.tag for c in self.pack.chunks if c.tag not in hidden}
        vis = [c for c in model[1] if c[8] in show]
        g = V.rasterize((model[0], vis, model[2]), yaw, pitch, W, H, frame=model[1], window=window)
        return model, g

    def test_pick_matches_geometry(self):
        model, g = self.render(90, 0)                                # side view: body in the middle
        bmap = B.batch_tags(model[1]); T = B.tag_map(g.M, bmap)
        self.assertEqual(set(np.unique(T)) - {-1} <= {c.tag for c in self.pack.chunks}, True)
        self.assertEqual(B.pick_tag(T, 240, 150) in (0, 0x100, 0x101, 0x122, 0x126, 0x111), True)
        # every pixel's tag must equal the chunk that owns the batch that drew it
        ys, xs = np.nonzero(g.M >= 0)
        for y, x in list(zip(ys, xs))[::997]:
            self.assertEqual(T[y, x], bmap[int(g.M[y, x])])
        # a point projected from each part's own centre picks that part (parts that are not hidden behind others)
        for tag in (0, 0x122):
            c = self.pack.chunk(tag); ctr = (np.array(c.lo) + np.array(c.hi)) / 2
            hit = [(x, y) for y, x in zip(*np.nonzero(T == tag))]
            self.assertTrue(len(hit) > 20, hex(tag))
            x, y = hit[len(hit) // 2]
            self.assertEqual(B.pick_tag(T, x, y, radius=0), tag)
        self.assertIsNone(B.pick_tag(T, 0, 0, radius=0))
        self.assertIsNone(B.pick_tag(T, -5, 10)); self.assertIsNone(B.pick_tag(T, 10, 99999))

    def test_hidden_part_not_pickable(self):
        model, g = self.render(90, 0, hidden={0x122})
        T = B.tag_map(g.M, B.batch_tags(model[1]))
        self.assertFalse((T == 0x122).any())
        model, g2 = self.render(90, 0)
        self.assertTrue((B.tag_map(g2.M, B.batch_tags(model[1])) == 0x122).any())

    def test_overlay(self):
        model, g = self.render(90, 0)
        T = B.tag_map(g.M, B.batch_tags(model[1]))
        o = B.selection_overlay(T, [0])
        self.assertEqual(o.shape, T.shape + (4,))
        self.assertTrue(((o[..., 3] > 0) == (T == 0)).all())          # overlay covers exactly the selected part
        self.assertTrue((o[..., 3] == 255).any() and (o[..., 3] == 70).any())   # outline + fill
        self.assertIsNone(B.selection_overlay(T, []))
        self.assertIsNone(B.selection_overlay(T, [0x999]))

    def test_fit_does_not_move_while_orbiting(self):
        model, _ = self.render(90, 0)
        ext = [B.view_extent(model[1], y, p, V.view_point, 1.6) for y in range(-180, 180, 15) for p in (0, 14, 45, 80)]
        hws = {round(e[2], 6) for e in ext}
        self.assertEqual(len(hws), 1)                                   # same size from every angle
        # the car's own centre projects to the same picture point (centre of the window) from every angle
        lo = np.min([c[0:3] for c in model[1] if c[8] != B.SHADOW], 0); hi = np.max([c[3:6] for c in model[1] if c[8] != B.SHADOW], 0)
        for y in (-120, 0, 60, 170):
            for p in (0, 30):
                cx, cy, _ = B.view_extent(model[1], y, p, V.view_point, 1.6)
                px, py = V.view_point((lo + hi) / 2, y, p)
                self.assertAlmostEqual(cx, px, 6); self.assertAlmostEqual(cy, py, 6)
        # every corner of the car stays inside the frame the camera builds (zoom 1, 640x360), from several angles
        for y, p in ((90, 0), (-35, 14), (0, 0), (180, 60)):
            cx, cy, hw = B.view_extent(model[1], y, p, V.view_point, 640 / 360, fill=1.0)
            (wx, wy, whw), upp = B.Camera(y, p).window(cx, cy, hw, 640, 360)
            for x in (lo[0], hi[0]):
                for yy in (lo[1], hi[1]):
                    for z in (lo[2], hi[2]):
                        qx, qy = V.view_point((x, yy, z), y, p)
                        self.assertLessEqual(abs(qx - wx) / upp, 320); self.assertLessEqual(abs(qy - wy) / upp, 180)

    def test_window_zoom_changes_view(self):
        model, g = self.render(90, 0)
        cx, cy, hw = B.view_extent(model[1], 90, 0, V.view_point, 480 / 300)
        self.assertGreater(hw, 1.0)
        c = B.Camera(90, 0); c.zoom_by(3)
        w, _ = c.window(cx, cy, hw, 480, 300)
        _, g3 = self.render(90, 0, window=w)
        _, g1 = self.render(90, 0, window=B.Camera(90, 0).window(cx, cy, hw, 480, 300)[0])
        self.assertFalse(np.array_equal(g1.M, g3.M))
        # the fitted window keeps the whole car in frame
        T1 = B.tag_map(g1.M, B.batch_tags(model[1]))
        for tag in (0, 0x122, 0x126):
            self.assertTrue((T1 == tag).any(), hex(tag))
        # zoomed on the body centre: body still centred under the cursor
        T3 = B.tag_map(g3.M, B.batch_tags(model[1]))
        self.assertEqual(B.pick_tag(T3, 240, 150), 0)

    def test_part_info(self):
        i = B.part_info(self.pack, [0], {0x123}, E.tag_name, E.engine_draws)
        self.assertEqual((i["name"], i["tag"], i["category"], i["draws"], i["verts"], i["tris"]), ("Body", 0, "Body", 1, 8, 12))
        self.assertAlmostEqual(i["size"][2], 4.0, 4)
        self.assertEqual(i["materials"][0]["tex"], "body_red"); self.assertEqual(i["textures"][0]["status"], "ok")
        w = B.part_info(self.pack, [0x122], set(), E.tag_name, E.engine_draws)
        self.assertTrue(w["textures"][0]["status"].startswith("MISSING"))   # borrowed texture, no stock pack
        multi = B.part_info(self.pack, [0, 0x100, 0x123], {0x123}, E.tag_name, E.engine_draws)
        self.assertEqual((multi["count"], multi["draws"], multi["tris"]), (3, 3, 36)); self.assertTrue(multi["mixed_visibility"])
        self.assertEqual(B.part_info(self.pack, [0x999], set(), E.tag_name, E.engine_draws)["count"], 0)

    def test_oplog(self):
        l = B.OpLog(limit=3)
        for i in range(5): l.add("success" if i % 2 else "bogus", "m%d" % i, "line1\nline2" if i == 4 else "")
        self.assertEqual(len(l.entries), 3)
        self.assertEqual(l.entries[0]["level"], "info")   # unknown level coerced
        self.assertIn("    line2", l.text()); self.assertEqual(l.counts()["success"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
