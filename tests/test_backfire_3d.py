"""Backfire tab: live play maths, slider edits and the tab wiring, with a synthetic backfire.wpk / .bfx pair and the fake tkinter.
Pure logic + wiring: the GPU drawing itself (gl_viewport.FlameLayer) needs a real OpenGL context and is NOT exercised here."""
import os, struct, sys, tempfile, types, unittest
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from test_ui_m6 import build_app, set_main_prefs
import backfire_edit as BE

TMP = tempfile.mkdtemp()


def make_pair(variants=("SAMLL_A", "MIDDLE_A", "SMALL_B", "MIDDLE_B")):
    """one flame (a triangle) per variant; frame f scales the flame by (f + 1) / 8 on the diagonal"""
    w = BE.BackfireWpk()
    w.tex = [dict(name="image_0", w=4, h=4, fmt=1, raw=bytes(4 * 4 * 2))]
    for gi, _v in enumerate(variants):
        V = np.zeros(3, BE.VDT)
        V["pos"] = [[0, 0, 0], [1, 0, -4], [0, 1, -4]]
        V["uv"] = [[0, 0], [1, 0], [0, 1]]
        w.flames.append(dict(tag=gi + 1, tex=0, flags=2, V=V, I=np.array([0, 1, 2], "<u2")))
    wpk = os.path.join(TMP, "backfire.wpk")
    open(wpk, "wb").write(w.to_bytes())

    def s(t):
        b = t.encode()
        return struct.pack("<H", len(b)) + b
    out = struct.pack("<4sHHIII", b"BFX1", 1, 0, 17, len(variants), 4)
    for gi, v in enumerate(variants):
        out += struct.pack("<III", gi + 1, gi, 0) + s(v) + s(".")
        for f in range(17):
            m = np.eye(4, dtype="<f4")
            m[0, 0] = m[1, 1] = m[2, 2] = (f + 1) / 8.0
            out += m.reshape(-1).tobytes()
    for car, cid in (("cars/maz_FD3S|12", 12), ("cars/nis_R34|37", 37)):
        for mu, slot, x in (("MUFFLER1", "MF_NULL_R1", -3.0), ("MUFFLER1", "MF_NULL_L1", 3.0)) if cid == 12 else (("MUFFLER1", "MF_NULL_R1", -2.0), ("MUFFLER2", "MF_NULL_R2", -1.0)):
            m = np.eye(4, dtype="<f4")
            m[3, :3] = [x, 5.0, -20.0]
            out += s(car) + s(mu) + s(slot) + s(".") + m.reshape(-1).tobytes()
    bfx = os.path.join(TMP, "backfire.bfx")
    open(bfx, "wb").write(out)
    return wpk, bfx


class Pure(unittest.TestCase):
    def setUp(self):
        self.wpk_path, self.bfx_path = make_pair()
        self.w, self.b = BE.BackfireWpk.load(self.wpk_path), BE.Bfx.load(self.bfx_path)

    def test_variant_for_follows_the_game(self):
        self.assertEqual(BE.variant_for(12, "Small"), "SAMLL_A")
        self.assertEqual(BE.variant_for(12, "Middle"), "MIDDLE_A")
        self.assertEqual(BE.variant_for(0x25, "Small"), "SMALL_B")
        self.assertEqual(BE.variant_for(0x75, "Middle"), "MIDDLE_B")
        self.assertEqual(BE.variant_for(12, "Small", "LARGE_A"), "LARGE_A")

    def test_instances_are_car_x_outlet_x_half_x_frame(self):
        inst = BE.instances_for(self.b, self.w, "cars/maz_FD3S|12", "MIDDLE_A", 7)
        self.assertEqual(len(inst), 2)                                   # the two MUFFLER1 outlets, one MIDDLE_A flame each
        fi, M = inst[0]
        self.assertEqual(fi, 1)                                          # flame with tag 2 = the MIDDLE_A group
        np.testing.assert_allclose(M[:3, 3], [-3.0, 5.0, -20.0])         # outlet position
        np.testing.assert_allclose(np.diag(M)[:3], 0.5 * (7 + 1) / 8.0)  # 0.5 x the frame's size
        p = M @ np.array([1, 0, -4, 1.0])                                # a flame vertex lands behind the outlet
        np.testing.assert_allclose(p[:3], [-3.0 + 0.5, 5.0, -20.0 - 2.0])

    def test_no_flame_off_range_or_muffler2(self):
        self.assertEqual(BE.instances_for(self.b, self.w, "cars/maz_FD3S|12", "MIDDLE_A", None), [])
        self.assertEqual(BE.instances_for(self.b, self.w, "cars/maz_FD3S|12", "MIDDLE_A", 17), [])
        inst = BE.instances_for(self.b, self.w, "cars/nis_R34|37", "MIDDLE_B", 0)
        self.assertEqual(len(inst), 1)                                   # MUFFLER2 is not drawn by the game
        self.assertEqual(BE.instances_for(self.b, self.w, "cars/none|1", "MIDDLE_A", 0), [])

    def test_scale_is_absolute_and_reversible(self):
        self.w.set_scale_abs(0, 2.0, 0.5)
        self.assertEqual(self.w.scale_factors(0), (2.0, 0.5))
        self.w.set_scale_abs(0, 2.0, 0.5)                                # again: no drift
        self.assertEqual(self.w.scale_factors(0), (2.0, 0.5))
        np.testing.assert_allclose(self.w.flames[0]["V"]["pos"][1], [2.0, 0.0, -2.0])
        self.w.set_scale_abs(0, 1.0, 1.0)
        self.assertEqual(self.w.flames[0]["V"].tobytes(), self.w.orig[0].tobytes())
        self.assertEqual(self.w.to_bytes() == self.w.raw_file, True)     # back to the opened file, byte for byte

    def test_anim_factor_scales_only_the_size_numbers(self):
        before = bytes(self.b.b)
        self.b.set_anim_factor(0, 2.0)
        self.assertAlmostEqual(self.b.anim_factor(0), 2.0, places=5)
        self.assertEqual(len(self.b.b), len(before))
        np.testing.assert_allclose(np.diag(self.b.frame_matrix(0, 16))[:3], 2.0 * 17 / 8.0, rtol=1e-6)
        self.assertEqual(self.b.frame_matrix(0, 16)[3, 3], 1.0)
        self.b.set_anim_factor(0, 1.0)
        self.assertEqual(bytes(self.b.b), before)                        # and back, byte for byte

    def test_flame_layer_marker_quads(self):
        try:
            import gl_viewport as G
        except Exception as ex:
            self.skipTest("gl_viewport not importable: %s" % ex)
        cam = types.SimpleNamespace(yaw=150.0, pitch=12.0, zoom=1.0, pan=[0.0, 0.0])
        view = G.View(cam, np.zeros(3), 10.0, (400, 300))
        layer = G.FlameLayer()
        layer.markers = [((1, 2, 3), 0.6, (0, 1, 1)), ((0, 0, 0), 0.9, (1, 1, 0))]
        tri = layer._marker_triangles(view)
        self.assertEqual(tri.shape, (2 * 3 * 2 * 6, 6))                  # markers x axes x quads x 6 vertices, xyz + rgb
        self.assertTrue(np.isfinite(tri).all())


class Tab(unittest.TestCase):
    def setUp(self):
        set_main_prefs({})
        self.root, self.app = build_app()
        self.tab = self.app.backfire
        self.tab.open_path(make_pair()[0])
        self.drawn = []
        t = self.tab
        t.layer = types.SimpleNamespace(instances=[], markers=[], gain=1.0, updated=[],
                                        update_mesh=lambda k, V: t.layer.updated.append(k),
                                        set_assets=lambda m, tx: None)
        self.app.models.gl = types.SimpleNamespace(request_draw=lambda: self.drawn.append(1), upp=0.01, extras=[],
                                                   widget=types.SimpleNamespace(winfo_ismapped=lambda: True))
        self.app.models.path = "C:/cars/maz_FD3S.wpk"                   # the car open in Parts decides the outlets
        t.active = True

    def test_opening_lists_cars_and_flames(self):
        t = self.tab
        self.assertEqual(t.names, ["cars/maz_FD3S|12", "cars/nis_R34|37"])
        self.assertEqual(len(t.tree.get_children()), 4)

    def test_play_sets_instances_and_crosses(self):
        t = self.tab
        t.names = ["cars/maz_FD3S|12", "cars/nis_R34|37"]
        t.variant.set("Auto")
        t.size.set("Middle")
        t.set_frame(5)
        self.assertEqual(len(t.layer.instances), 2)
        self.assertEqual(len(t.layer.markers), 2)
        t.set_frame(None)
        self.assertEqual(t.layer.instances, [])
        self.assertTrue(self.drawn)

    def test_shape_slider_edits_live_and_undoes(self):
        t = self.tab
        t.scope.set("all")
        t.shape_press()
        t.sw.set(1.5)
        t.sl.set(2.0)
        t.shape_move()
        self.assertEqual(sorted(t.layer.updated), [0, 1, 2, 3])
        self.assertEqual(t.wpk.scale_factors(2), (1.5, 2.0))
        t.do_undo()
        self.assertEqual(t.wpk.scale_factors(2), (1.0, 1.0))

    def test_outlet_slider_moves_mirror_too(self):
        t = self.tab
        t.names = ["cars/maz_FD3S|12", "cars/nis_R34|37"]
        nz = t.key_nozzles()
        t.sel_noz = nz[0]                                                # R1 at x -3
        t.mir.set(1)
        t.outlet_press()
        t.outlet_move(0, -4.0)
        self.assertAlmostEqual(t.bfx.pos(nz[0])[0], -4.0, places=5)
        self.assertAlmostEqual(t.bfx.pos(nz[1])[0], 4.0, places=5)       # L1 follows, mirrored
        t.outlet_move(1, 6.5)
        self.assertAlmostEqual(t.bfx.pos(nz[1])[1], 6.5, places=5)
        t.do_undo()
        self.assertAlmostEqual(t.bfx.pos(nz[0])[0], -3.0, places=5)

    def test_tick_advances_frames_and_pauses_between_pops(self):
        t = self.tab
        t.names = ["cars/maz_FD3S|12", "cars/nis_R34|37"]
        t.gap.set(10)
        t.playing.set(True)
        seen = set()
        import time as _t
        for k in range(40):
            t.last = _t.perf_counter() - 1 / 60.0                          # one game tick per call
            t.tick()
            seen.add(t.frame)
        self.assertIn(None, seen)                                         # the flame goes off between pops
        self.assertTrue(all(f is None or 0 <= f < 17 for f in seen))
        self.assertGreater(len(seen), 10)

    def test_save_writes_slider_changes(self):
        t = self.tab
        t.scope.set("sel")
        t.shape_press()
        t.sw.set(2.0)
        t.shape_move()
        t.save()
        re = BE.BackfireWpk.load(t.wpk_path)
        self.assertAlmostEqual(float(re.flames[0]["V"]["pos"][:, 0].max()), 2.0, places=5)


if __name__ == "__main__":
    unittest.main()
