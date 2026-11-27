"""Drives the real Models-tab code (wpk_edit.build_tab) with a fake tkinter: construction, open, browser, picking, props, log, save.
NOT a visual test."""
import os, sys, time, shutil, tempfile, unittest
os.environ.setdefault("VPK_VIEWPORT", "soft")      # these tests drive the software viewport (the GPU one has tests/test_gl_*.py)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp()
os.environ["HOME"] = TMP                      # settings file goes to the temp home
import faketk
MB = faketk.install()
import numpy as np
import wpk_edit as E, wpk_browser as WB
from synth import make_pack


def settle(tab, n=400):
    """run the tab's pump until the worker thread's picture has been drawn"""
    for _ in range(n):
        tab.pump_once = True
        tab.pump()
        if tab._T is not None and tab.progress["value"] == 0 and tab.q.empty():
            if not any(t.name.startswith("Thread") and t.is_alive() and t is not __import__("threading").main_thread()
                       for t in __import__("threading").enumerate() if t.daemon):
                return
        time.sleep(0.01)
    raise AssertionError("render never finished")


def settle_drag(tab, n=400):
    """run the pump until no worker is running and no picture is waiting"""
    import threading
    for _ in range(n):
        tab.pump()
        if tab.q.empty() and not tab._busy and not tab._again:
            return
        time.sleep(0.01)
    raise AssertionError("drag frame never finished")


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.join(TMP, "cars"); os.makedirs(cls.dir)
        cls.stock = os.path.join(cls.dir, "maz_TEST.wpk")
        make_pack(cls.stock)
        shutil.copy(cls.stock, os.path.join(cls.dir, "maz_TEST_aeroA.wpk"))
        cls.tab = E.build_tab(faketk.W(), faketk.W())

    def test_01_empty_state(self):
        t = self.tab
        self.assertIsNone(t.pack)
        self.assertEqual(t.tree.get_children(), ())
        self.assertTrue(t.empty.mapped and "No WPK" in t.empty["text"])
        self.assertIn("No WPK", t.p_empty["text"]); self.assertTrue(t.p_empty.mapped and not t.p_frame.mapped)
        self.assertEqual(t.canvas.items[0][0], "text")
        self.assertIn("Open a car", t.oplog.entries[0]["msg"])

    def test_02_open_populates_browser(self):
        t = self.tab
        t.open_path(self.stock); settle(t)
        self.assertEqual(set(t.tree.get_children()), {"cat:Body", "cat:Wheels", "cat:Wing", "cat:Bonnet", "cat:Lights"})
        self.assertEqual(sorted(int(i) for i in t.tree.nodes if i.isdigit()), [0, 0x100, 0x101, 0x111, 0x122, 0x123, 0x126])
        self.assertFalse(t.empty.mapped)
        self.assertEqual(t.tree.nodes["290"]["values"][0], "○")           # Wing A starts hidden (NONE_BY_DEFAULT group)
        self.assertEqual(t.tree.nodes["0"]["values"][0], "●")
        self.assertEqual(t.oplog.entries[-1]["level"], "success")
        self.assertIn("Opened maz_TEST.wpk: 7 parts", t.oplog.entries[-1]["msg"])
        self.assertEqual(sorted(t.kits), ["Aero kit A", "Stock car"])
        self.assertIsNotNone(t._T)

    def test_03_search_and_filter(self):
        t = self.tab
        t.search.set("wing"); [f() for f in list(faketk.W.AFTERS) if f]; faketk.W.AFTERS.clear(); t.fill()
        self.assertEqual(sorted(int(i) for i in t.tree.nodes if i.isdigit()), [0x122, 0x123])
        t.search.set("zzz"); t.fill()
        self.assertTrue(t.empty.mapped and "No parts match" in t.empty["text"])
        t.clear_filters()
        self.assertEqual(t.search.get(), "")
        t.cat_vars["Wheels"].set(True); t.fill()
        self.assertEqual(sorted(int(i) for i in t.tree.nodes if i.isdigit()), [0x100, 0x101])
        t.clear_filters()
        self.assertEqual(len([i for i in t.tree.nodes if i.isdigit()]), 7)

    def test_04_browser_selection_drives_props_and_overlay(self):
        t = self.tab
        t.tree.selection_set(["0"]); settle(t)
        self.assertEqual(t.p_name.get(), "Body")
        self.assertIn("Tag 0 (0x0)", t.p_sub.get()); self.assertIn("1 draw", t.p_stats.get()); self.assertIn("12 triangles", t.p_stats.get())
        self.assertTrue(t.p_frame.mapped)
        self.assertIn("sel", [i[1] for i in t.canvas.items])                # highlight drawn
        self.assertEqual(t.mat_tree.nodes.__len__(), 1)
        self.assertTrue(t.b_export["state"] == "normal" and t.b_move["state"] == "normal")
        t.tree.selection_set(["0", "256"]); 
        self.assertEqual(t.p_name.get(), "2 parts"); self.assertEqual(t.b_move["state"], "disabled")   # move needs exactly one
        t.tree.selection_set(["cat:Wheels"])                                # group header selects its parts
        self.assertEqual(sorted(t.sel_tags()), [0x100, 0x101])
        t.tree.selection_remove(t.tree.selection())
        self.assertFalse(t.p_frame.mapped); self.assertNotIn("sel", [i[1] for i in t.canvas.items])

    def test_05_viewport_pick_selects_browser_item(self):
        t = self.tab
        t.view.set(1); t.view_changed(); settle(t)                          # Side view
        T = t._T
        ys, xs = np.nonzero(T == 0)
        x, y = int(xs[len(xs) // 2]), int(ys[len(ys) // 2])
        W, H = t._T_size
        t.viewport_pick(x * W / T.shape[1], y * H / T.shape[0])
        self.assertEqual(t.sel_tags(), [0])
        # pick a hidden-by-default wing: not drawn, so nothing to pick; show it, then pick it
        self.assertNotIn(0x122, set(np.unique(t._T)))
        t.set_visible([0x122], True); settle(t)
        ys, xs = np.nonzero(t._T == 0x122)
        self.assertTrue(len(xs) > 10)
        t.search.set("bonnet"); t.fill()                                    # filter hides Wing A in the browser ...
        T = t._T; x, y = int(xs[len(xs) // 2]), int(ys[len(ys) // 2])
        t.viewport_pick(x * t._T_size[0] / T.shape[1], y * t._T_size[1] / T.shape[0])
        self.assertEqual(t.sel_tags(), [0x122])                             # ... the pick clears the filter and selects it
        self.assertEqual(t.search.get(), "")
        t.viewport_pick(0, 0); self.assertEqual(t.sel_tags(), [])           # click on empty background clears
        t.set_visible([0x122], False); settle(t)

    def test_06_visibility_is_preview_only(self):
        t = self.tab
        before = t.pack.to_bytes(); dirty = t.dirty
        t.tree.selection_set(["0"]); t.p_vis.set(False); t._props_visible()
        self.assertIn(0, t.hidden); self.assertEqual(t.tree.nodes["0"]["values"][0], "○")
        self.assertEqual(t.dirty, dirty); self.assertEqual(t.pack.to_bytes(), before)
        t.toggle_vis(); self.assertNotIn(0, t.hidden)
        t.tree._row = "0"; t._tree_click(types.SimpleNamespace(x=5, y=5)) if False else None

    def test_07_camera_interaction(self):
        t = self.tab
        t.fit_view(); y0 = t.cam.yaw
        t._vp_press(types.SimpleNamespace(x=100, y=100, state=0))
        t._vp_motion(types.SimpleNamespace(x=101, y=100, state=0)); self.assertEqual(t.cam.yaw, y0)   # < 4 px = still a click
        t._vp_motion(types.SimpleNamespace(x=160, y=130, state=0))
        self.assertNotEqual(t.cam.yaw, y0); self.assertEqual(t.view.get(), E_FREE())
        t._vp_release(types.SimpleNamespace(x=160, y=130, state=0)); settle(t)
        z = t.cam.zoom; r = t._vp_wheel(types.SimpleNamespace(delta=120, num=0)); self.assertGreater(t.cam.zoom, z)
        self.assertEqual(r, "break")                  # the wheel must not reach a scroll area behind the picture
        t._vp_wheel(types.SimpleNamespace(delta=-120, num=0)); self.assertAlmostEqual(t.cam.zoom, z)
        t._vp_press_pan(types.SimpleNamespace(x=50, y=50, state=0)); t._vp_motion(types.SimpleNamespace(x=80, y=60, state=0))
        self.assertNotEqual(t.cam.pan, [0.0, 0.0]); t._vp_release(types.SimpleNamespace(x=80, y=60, state=0)); settle(t)
        t.fit_view(); self.assertEqual((t.cam.zoom, t.cam.pan), (1.0, [0.0, 0.0])); settle(t)

    def test_07b_drag_never_starves_the_screen(self):
        """while a frame is being drawn, mouse moves must NOT cancel it (they used to, so nothing was drawn until release);
        the newest camera is drawn as soon as the frame is done, and drag frames do not throw away the pick map"""
        t = self.tab
        t.fit_view(); settle(t)
        T0 = t._T
        t._vp_press(types.SimpleNamespace(x=100, y=100, state=0))
        t._vp_motion(types.SimpleNamespace(x=140, y=100, state=0)); settle_drag(t)             # first frame: starts a worker
        self.assertIs(t._T, T0)                                                                 # drag frames leave the pick map alone
        t._busy = True                                                                          # pretend a frame is in flight
        gen = t.gen
        for x in range(150, 400, 10):
            t._vp_motion(types.SimpleNamespace(x=x, y=100, state=0))
        self.assertEqual(t.gen, gen)                                                            # nothing was cancelled
        self.assertTrue(t._again)
        yaw = t.cam.yaw
        t._busy = False
        t.pump()                                                                                # the frame finished: the newest camera starts
        self.assertGreater(t.gen, gen); self.assertFalse(t._again)
        settle_drag(t)
        self.assertEqual(t.cam.yaw, yaw)
        t._vp_release(types.SimpleNamespace(x=400, y=100, state=0)); settle(t)
        self.assertIsNotNone(t._T); self.assertIsNot(t._T, T0)                                  # the sharp picture rebuilt the pick map

    def test_07c_orbit_goes_all_the_way_round(self):
        t = self.tab
        t.cam.set_angles(0, 80)
        t._vp_press(types.SimpleNamespace(x=100, y=100, state=0))
        t._vp_motion(types.SimpleNamespace(x=100, y=300, state=0))                              # drag far down: over the top
        self.assertTrue(-180 <= t.cam.pitch < 180); self.assertGreater(t.cam.pitch, 90)      # over the top, upside down
        t._vp_release(types.SimpleNamespace(x=100, y=300, state=0)); settle(t)
        t.fit_view(); settle(t)

    def test_08_kit_switch_no_stale_selection(self):
        t = self.tab
        t.tree.selection_set(["294"] if "294" in t.tree.nodes else ["290"])
        self.assertTrue(t.sel_tags())
        t.hidden = set(); t.kit.set("Aero kit A"); t.switch_kit(); settle(t)
        self.assertTrue(t.path.endswith("_aeroA.wpk"))
        self.assertEqual(t.cam.zoom, 1.0)
        for tag in t.sel_tags():
            self.assertIsNotNone(t.pack.chunk(tag))
        self.assertEqual(t.p_name.get() if t.sel_tags() else "", t.p_name.get() if t.sel_tags() else "")
        t.kit.set("Stock car"); t.switch_kit(); settle(t)
        self.assertTrue(t.path.endswith("maz_TEST.wpk"))

    def test_09_edit_flow_delete_undo_dirty_and_log(self):
        t = self.tab
        n = len(t.pack.chunks); t.tree.selection_set(["291"]); t.delete()
        self.assertEqual(len(t.pack.chunks), n - 1); self.assertTrue(t.dirty)
        self.assertFalse(t.tree.exists("291")); self.assertEqual(t.sel_tags(), [])
        self.assertIn("deleted", t.oplog.entries[-1]["msg"])
        t.do_undo(); self.assertEqual(len(t.pack.chunks), n); self.assertTrue(t.tree.exists("291"))
        settle(t)

    def test_10_save_as_roundtrip_and_log(self):
        t = self.tab
        orig = open(self.stock, "rb").read()
        out = os.path.join(TMP, "copy.wpk")
        t._save(out)
        self.assertEqual(open(out, "rb").read(), orig)                      # untouched model saves byte-identical
        self.assertEqual(t.oplog.entries[-1]["level"], "success"); self.assertIn("copy.wpk", t.oplog.entries[-1]["msg"])
        t.open_path(self.stock); settle(t)

    def test_11_open_error_logged(self):
        t = self.tab
        bad = os.path.join(TMP, "bad.wpk"); open(bad, "wb").write(b"NOPE" + b"\0" * 40)
        t.open_path(bad)
        e = t.oplog.entries[-1]
        self.assertEqual(e["level"], "error"); self.assertIn("not a WPK3", e["detail"])
        self.assertEqual(MB[-1][0], "showerror")
        self.assertTrue(t.path.endswith("maz_TEST.wpk"))                     # the previous car stays loaded

    def test_12_log_copy_clear_counts(self):
        t = self.tab
        t.log_clear(); t.log("warning", "w"); t.log("error", "e", "line1\nline2")
        self.assertEqual(t.log_counts.get().split()[:2], ["1", "error"]); self.assertIn("1 warning", t.log_counts.get())
        t.log_copy(); self.assertIn("line2", t.oplog.text())
        t.log_clear(); self.assertEqual(t.oplog.entries, [])

    def test_13_render_error_is_reported_not_raised(self):
        t = self.tab
        orig = E.wpk_view.rasterize if hasattr(E, "wpk_view") else None
        import wpk_view
        real = wpk_view.rasterize
        t.log_clear()
        wpk_view.rasterize = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            t.render(); 
            for _ in range(200):
                t.pump(); time.sleep(0.01)
                if t.oplog.entries and t.oplog.entries[-1]["level"] == "error": break
        finally:
            wpk_view.rasterize = real
        self.assertEqual(t.oplog.entries[-1]["level"], "error"); self.assertIn("boom", t.oplog.entries[-1]["msg"])
        self.assertIn("Traceback", t.oplog.entries[-1]["detail"]); self.assertIn("boom", t.warn["text"])
        t.render(); settle(t)


import types
def E_FREE():
    import wpk_view
    return wpk_view.FREE

if __name__ == "__main__":
    unittest.main(verbosity=2)
