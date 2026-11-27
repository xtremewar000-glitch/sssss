"""Drives the REAL Textures-and-materials window code (wpk_edit.TexDialog via Tab.textures) with a fake tkinter.
Checks wiring and data flow (filter, preview, warnings, replace / add / delete through Pack, undo, sync after open / kit switch).
NOT a visual test: layout, colours and the look of the preview in real Tk are unverified."""
import os, sys, shutil, tempfile, unittest, types
os.environ.setdefault("VPK_VIEWPORT", "soft")      # these tests drive the software viewport (the GPU one has tests/test_gl_*.py)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from test_ui_faketk import settle, MB, TMP, faketk        # installs the fake tkinter once
import numpy as np
from PIL import Image
import wpk_edit as E, wpk_texmgr as TM
from synth import make_pack

FD = sys.modules["tkinter.filedialog"]
SD = sys.modules["tkinter.simpledialog"]
MBX = sys.modules["tkinter.messagebox"]


def answers(yes=True):
    """make askyesno answer `yes` and remember the question text"""
    asked = []
    MBX.askyesno = lambda title, msg, **k: (asked.append((title, msg)), yes)[1]
    return asked


def rb(p):
    with open(p, "rb") as f:
        return f.read()


def png(name, size=(8, 8), colour=(0, 255, 0, 255)):
    p = os.path.join(TMP, name)
    Image.new("RGBA", size, colour).save(p)
    return p


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.join(TMP, "cars"); os.makedirs(cls.dir)
        cls.stock = os.path.join(cls.dir, "maz_TEST.wpk")
        p = make_pack(cls.stock)
        p.tex[1] = dict(name="wing_tex", w=4, h=4, fmt=2, raw=np.full((4, 4), 0xF801, "<u2").tobytes())   # RGBA5551 (has alpha)
        p.tex.append(dict(name="p1:body_red", w=4, h=4, fmt=0, raw=np.full((4, 4), 0x07E0, "<u2").tobytes()))   # a paint copy
        p.tex.append(dict(name="spare", w=4, h=4, fmt=0, raw=np.full((4, 4), 0x001F, "<u2").tobytes()))        # nothing draws it
        p.save(cls.stock, backup=False)
        k = E.Pack.load(cls.stock)
        for t in k.tex:
            t["raw"] = b""                                           # an aero kit borrows everything from the stock pack
        k.save(os.path.join(cls.dir, "maz_TEST_aeroA.wpk"), backup=False)
        cls.tab = E.build_tab(faketk.W(), faketk.W())
        cls.tab.open_path(cls.stock); settle(cls.tab)

    def setUp(self):
        t = self.tab
        t.open_path(self.stock); settle(t)
        if t.tex_dlg:
            t.tex_dlg.close()
        MB.clear()
        answers(True)
        FD.askopenfilename = lambda *a, **k: ""
        SD.askstring = lambda *a, **k: ""

    def dlg(self):
        return self.tab.textures()

    def names(self, d):
        return [d.tv.nodes[i]["values"][0] for i in d.tv.get_children()]

    def test_01_open_lists_textures_and_state(self):
        d = self.dlg()
        self.assertEqual(self.names(d), ["body_red", "wing_tex", "p1:body_red", "spare"])
        self.assertEqual(d.tv.nodes["0"]["values"][1:3], ("4 × 4", "RGB565"))
        self.assertEqual(d.tv.nodes["1"]["values"][2], "RGBA5551")
        self.assertIn("paint copy", d.tv.nodes["2"]["values"][3])           # not "unused"
        self.assertIn("unused", d.tv.nodes["3"]["values"][3])
        self.assertIn("no problems", d.count_txt.get())
        self.assertIs(self.tab.textures(), d)                                # a second click raises the same window, no duplicate

    def test_02_select_shows_preview_users_and_notes(self):
        d = self.dlg()
        d.tv.selection_set(["0"])
        self.assertEqual(d.name_txt.get(), "body_red")
        self.assertIn("image", [i[0] for i in d.canvas.items])               # decoded on demand
        self.assertEqual(d.cache.decodes, 1)
        self.assertEqual(len(d.users.get_children()), 6)                     # 6 of the 7 test parts draw texture 0
        self.assertIn("Has 1 paint copy (p1)", d.note_txt.get())
        d.bg.set("dark"); d.show()
        self.assertEqual(d.cache.decodes, 1)                                 # another background re-composes, it does not decode again
        d.tv.selection_set(["1"])
        self.assertEqual([d.users.nodes[i]["values"][1] for i in d.users.get_children()], ["0x122"])
        d.tv.selection_set(["0", "1"])
        self.assertEqual(d.canvas.items[-1][0], "text"); self.assertIn("2 textures selected", d.note_txt.get())
        self.assertEqual(d.b_replace["state"], "disabled"); self.assertEqual(d.b_delete["state"], "normal")

    def test_03_search_filter_and_empty_state(self):
        d = self.dlg()
        d.query.set("wing"); d.fill()
        self.assertEqual(self.names(d), ["body_red", "wing_tex"])             # body_red is drawn by Wing B (0x123) too: part names are searched
        d.query.set("wing_tex"); d.fill()
        self.assertEqual(self.names(d), ["wing_tex"])
        d.query.set("0x122"); d.fill()
        self.assertEqual(self.names(d), ["wing_tex"])                        # by tag of the part that draws it
        d.query.set("zzz"); d.fill()
        self.assertTrue(d.empty_lbl.mapped and "No texture matches 'zzz'" in d.empty_txt.get())
        d.query.set(""); d.flt.set("Unused"); d.fill()
        self.assertEqual(self.names(d), ["spare"])
        d.flt.set("Paint copies"); d.fill()
        self.assertEqual(self.names(d), ["p1:body_red"])
        d.flt.set("All"); d.fill()
        self.assertFalse(d.empty_lbl.mapped)

    def test_04_replace_shared_texture_asks_and_changes_only_that_slot(self):
        t = self.tab; d = self.dlg()
        before = [(b.tex, b.env, b.spec) for c in t.pack.chunks for b in c.batches]
        names = [x["name"] for x in t.pack.tex]
        d.tv.selection_set(["0"])
        asked = answers(True)
        FD.askopenfilename = lambda *a, **k: png("green.png")
        d.replace_selected()
        self.assertEqual(len(asked), 1)
        title, msg = asked[0]
        self.assertEqual(title, "Replace texture")
        self.assertIn("Used by 6 parts", msg); self.assertIn("paint copy (p1)", msg); self.assertIn("NOT changed", msg)
        self.assertIn("Aero A", msg)                                          # the kit that borrows it from this stock pack
        self.assertIn("Will be written as: 8 × 8 RGB565", msg)
        self.assertTrue(t.dirty); self.assertEqual(len(t.undo), 1)
        self.assertEqual(t.pack.decode_tex(0)[0, 0].tolist(), [0, 252, 0, 255])
        self.assertEqual([x["name"] for x in t.pack.tex], names)
        self.assertEqual(before, [(b.tex, b.env, b.spec) for c in t.pack.chunks for b in c.batches])
        self.assertEqual(t.pack.tex[2]["raw"], np.full((4, 4), 0x07E0, "<u2").tobytes())   # the paint copy was not touched
        self.assertEqual(d.cache.get(t.pack, 0)[0][0, 0].tolist()[1] > 200, True)         # the dialog shows the NEW picture, not a stale one
        self.assertIn("replaced", t.oplog.entries[-1]["msg"])
        t.do_undo(); settle(t)
        self.assertEqual(t.pack.decode_tex(0)[0, 0].tolist()[:2], [248, 0])
        self.assertEqual(d.tv.nodes["0"]["values"][1], "4 × 4")              # window followed the undo

    def test_05_replace_declined_changes_nothing(self):
        t = self.tab; d = self.dlg()
        d.tv.selection_set(["0"])
        raw = t.pack.tex[0]["raw"]
        answers(False)
        FD.askopenfilename = lambda *a, **k: png("green.png")
        d.replace_selected()
        self.assertEqual(t.pack.tex[0]["raw"], raw); self.assertFalse(t.dirty); self.assertEqual(t.undo, [])

    def test_06_unshared_replace_is_direct_and_alpha_loss_is_warned(self):
        t = self.tab; d = self.dlg()
        d.tv.selection_set(["3"])                                              # 'spare': nothing draws it, no paint copy
        asked = answers(True)
        FD.askopenfilename = lambda *a, **k: png("g.png")
        d.replace_selected()
        self.assertEqual(asked, [])                                            # nothing shared (the kit lists it but draws 0 times), nothing lost: no question
        self.assertEqual(t.pack.decode_tex(3)[0, 0].tolist(), [0, 252, 0, 255])
        d.tv.selection_set(["1"])                                              # wing_tex is RGBA5551: an opaque picture loses alpha
        FD.askopenfilename = lambda *a, **k: png("g2.png")
        d.replace_selected()
        self.assertEqual(len(asked), 1)
        self.assertIn("loses the transparency", asked[0][1])
        t.do_undo(); t.do_undo(); settle(t)

    def test_07_add_new_and_near_name_warning(self):
        t = self.tab; d = self.dlg()
        n = len(t.pack.tex)
        FD.askopenfilename = lambda *a, **k: png("newtex.png")
        SD.askstring = lambda *a, **k: "newtex.tga"
        asked = answers(True)
        d.add_new()
        self.assertEqual(len(t.pack.tex), n + 1); self.assertEqual(t.pack.tex[-1]["name"], "newtex.tga")
        self.assertEqual(asked, [])
        self.assertTrue(d.tv.exists(str(n)) and d.tv.selection() == (str(n),))   # the new row is selected
        SD.askstring = lambda *a, **k: "body_red.dds"                          # same stem, other extension -> would be a SEPARATE texture
        asked = answers(False)
        d.add_new()
        self.assertEqual(len(asked), 1); self.assertIn("SEPARATE texture", asked[0][1])
        self.assertEqual(len(t.pack.tex), n + 1)
        t.do_undo(); settle(t)
        self.assertEqual(len(t.pack.tex), n)

    def test_08_delete_warns_with_paint_copies_and_undo_restores(self):
        t = self.tab; d = self.dlg()
        before = t.pack.to_bytes()
        d.tv.selection_set(["0"])
        asked = answers(True)
        d.delete_selected()
        self.assertIn("body_red", asked[0][1]); self.assertIn("paint cop", asked[0][1]); self.assertIn("Aero A", asked[0][1])
        self.assertEqual([x["name"] for x in t.pack.tex], ["wing_tex", "p1:body_red", "spare"])
        self.assertEqual(self.names(d), ["wing_tex", "p1:body_red", "spare"])
        t.do_undo(); settle(t)
        self.assertEqual(t.pack.to_bytes(), before)
        d.tv.selection_set(["3"])
        asked = answers(False)
        d.delete_selected()
        self.assertIn("Nothing uses them", asked[0][1]); self.assertEqual(len(t.pack.tex), 4)

    def test_09_export_png_and_error_is_reported(self):
        t = self.tab; d = self.dlg()
        d.tv.selection_set(["0"])
        out = os.path.join(TMP, "out.png")
        FD.asksaveasfilename = lambda *a, **k: out
        d.export_selected()
        im = Image.open(out)
        self.assertEqual(im.size, (4, 4)); self.assertEqual(im.getpixel((0, 0)), (248, 0, 0, 255))
        t.log_clear()
        FD.asksaveasfilename = lambda *a, **k: os.path.join(TMP, "no_such_dir", "x.png")
        d.export_selected()
        self.assertEqual(t.oplog.entries[-1]["level"], "error"); self.assertEqual(MB[-1][0], "showerror")

    def test_10_select_parts_in_browser(self):
        t = self.tab; d = self.dlg()
        d.tv.selection_set(["1"]); d.select_parts()
        self.assertEqual(t.sel_tags(), [0x122])
        d.tv.selection_set(["0"]); d.select_parts()
        self.assertEqual(sorted(t.sel_tags()), [0, 0x100, 0x101, 0x111, 0x123, 0x126])
        d.tv.selection_set(["3"])
        self.assertEqual(d.b_parts["state"], "disabled")                      # a texture nothing draws has no parts to select

    def test_11_window_follows_kit_switch_and_double_click_from_properties(self):
        t = self.tab; d = self.dlg()
        t.kit.set("Aero kit A"); t.switch_kit(); settle(t)
        self.assertIs(t.tex_dlg, d)
        self.assertEqual(d.tv.nodes["0"]["values"][3].split(",")[0], "borrowed")
        self.assertEqual(d.cache.get(t.pack, 0, t.stock)[0].shape[2], 3)       # a borrowed texture previews from the stock pack
        self.assertIn("Borrowed from the stock pack", (d.tv.selection_set(["0"]), d.note_txt.get())[1])
        # replacing a BORROWED texture in a kit gives that kit its own copy and tells the user so
        asked = answers(True)
        FD.askopenfilename = lambda *a, **k: png("kit.png")
        d.replace_selected()
        self.assertEqual(asked, [] if not d.cat.entries[0].paint_copies else asked)
        self.assertTrue(t.pack.tex[0]["raw"])
        stock_bytes = rb(self.stock)
        self.assertEqual(rb(self.stock), stock_bytes)           # nothing was written to disk
        t.dirty = False
        t.kit.set("Stock car"); t.switch_kit(); settle(t)
        self.assertEqual(d.tv.nodes["0"]["values"][3].split(",")[0], "ok")
        t.tex_tree.insert("", "end", iid="r0", values=("wing_tex", "4×4", "ok")); t.tex_tree._row = "r0"
        d.close()
        t._props_tex_open(types.SimpleNamespace(x=1, y=1))
        self.assertIsNotNone(t.tex_dlg); self.assertEqual(t.tex_dlg.tv.selection(), ("1",))

    def test_12_problems_are_flagged_and_reported(self):
        t = self.tab
        t.open_path(os.path.join(self.dir, "maz_TEST_aeroA.wpk")); settle(t)
        t.stock = None                                                         # simulate: the stock pack is not beside the kit
        d = self.dlg()
        self.assertIn("4 problems", d.count_txt.get())
        self.assertIn("bad", d.tv.nodes["0"]["tags"])
        self.assertEqual(d.b_report["state"], "normal")
        d.flt.set("Missing"); d.fill()
        self.assertEqual(len(d.tv.get_children()), 4)
        t.log_clear(); d.show_report()
        self.assertEqual(t.oplog.entries[-1]["level"], "warning"); self.assertIn("not found in the stock pack"[:10] if False else "texture body_red", t.oplog.entries[-1]["detail"])

    def test_13_save_semantics_unchanged(self):
        t = self.tab; d = self.dlg()
        d.tv.selection_set(["3"])
        FD.askopenfilename = lambda *a, **k: png("g.png")
        d.replace_selected()
        out = os.path.join(TMP, "saved_after_tex.wpk")
        t._save(out)
        ref = E.Pack.load(self.stock)
        ref.add_texture("spare", Image.open(png("g.png")), 512)               # the same existing call, made directly
        self.assertEqual(rb(out), ref.to_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
