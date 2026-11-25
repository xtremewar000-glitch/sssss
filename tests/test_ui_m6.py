"""Milestone 6 tests: vpk_ui (pure rules) + the whole application window built and driven through a fake tkinter
(theme calls, menu bar, keyboard routing, saved window / panel layout, confirmation wording).
NOT a visual test: how the dark theme, sashes and menus actually look in real Tk is unverified."""
import json, os, sys, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from test_ui_faketk import MB, TMP, faketk
import vpk_ui as UI
import vpk_tool, wpk_view

MBX = sys.modules["tkinter.messagebox"]
FW = faketk.W


class Pure(unittest.TestCase):
    def test_confirm_text_order_and_limit(self):
        t = UI.confirm_text("Delete 2 parts?", ["Body (0x0)", "", "Wing A (0x122)"], "Nothing is saved until you Save.", True)
        self.assertEqual(t, "Delete 2 parts?\n\n• Body (0x0)\n• Wing A (0x122)\n\nNothing is saved until you Save.\n\nUndo brings it back.")
        self.assertTrue(UI.confirm_text("Q", undoable=False).endswith("This cannot be undone."))
        self.assertEqual(UI.confirm_text("Q"), "Q")
        long = UI.confirm_text("Q", ["d%d" % i for i in range(20)], limit=5)
        self.assertIn("• … and 15 more", long); self.assertNotIn("d5", long)

    def test_discard_only_asks_when_dirty(self):
        asked = []
        mb = types.SimpleNamespace(askyesno=lambda title, msg, **k: (asked.append((title, msg, k)), False)[1])
        self.assertTrue(UI.discard_changes(mb, None, "this car", dirty=False)); self.assertEqual(asked, [])
        self.assertFalse(UI.discard_changes(mb, None, "this car", dirty=True))
        self.assertEqual(asked[0][0], "Unsaved changes"); self.assertIn("Throw away the unsaved changes to this car?", asked[0][1])
        self.assertEqual(asked[0][2]["icon"], "warning")

    def test_clamp_panes(self):
        self.assertEqual(UI.clamp_panes(1400, 300, 300), (300, 300))
        self.assertEqual(UI.clamp_panes(1400, 10, 10), (220, 240))                    # minimum widths
        l, r = UI.clamp_panes(1000, 900, 900)                                       # saved on a bigger screen
        self.assertGreaterEqual(1000 - l - r, 360); self.assertGreaterEqual(l, 220); self.assertGreaterEqual(r, 240)
        l, r = UI.clamp_panes(900, 400, 300)
        self.assertEqual((l, r, 900 - l - r), (300, 240, 360))
        for total in (500, 800, 819):                                                # smaller than the three minimums: shared in proportion
            l, r = UI.clamp_panes(total, 300, 300)
            self.assertTrue(0 < l < total and 0 < r < total and l + r < total)
        self.assertEqual(UI.clamp_panes(1400, "junk", None), (300, 300))

    def test_parse_layout(self):
        self.assertEqual(UI.parse_layout(None, 1400), (300, 300, True))
        self.assertEqual(UI.parse_layout("x", 1400), (300, 300, True))
        self.assertEqual(UI.parse_layout(dict(left=410, right=260, log=False), 1400), (410, 260, False))
        self.assertEqual(UI.parse_layout(dict(left=[1], right={}), 1400), (300, 300, True))

    def test_fit_geometry(self):
        ok = dict(width=1200, height=800, x=100, y=50)
        self.assertEqual(UI.fit_geometry(ok, 1920, 1080), "1200x800+100+50")
        self.assertEqual(UI.fit_geometry(dict(width=1200, height=800), 1920, 1080), "1200x800")        # an older prefs file: no place saved
        self.assertEqual(UI.fit_geometry(dict(width=1200, height=800, x=3000, y=50), 1920, 1080), "1200x800+720+50")   # monitor is gone: pulled back
        self.assertEqual(UI.fit_geometry(dict(width=1200, height=800, x=-500, y=-9), 1920, 1080), "1200x800+0+0")
        self.assertEqual(UI.fit_geometry(dict(width=2400, height=1500, x=0, y=0), 1366, 768), "1366x768+0+0")
        self.assertIsNone(UI.fit_geometry(dict(width=300, height=200), 1920, 1080))                   # smaller than the window minimum
        for junk in (None, "x", {}, dict(width="a", height=1)):
            self.assertIsNone(UI.fit_geometry(junk, 1920, 1080))
        self.assertEqual(UI.fit_geometry(dict(width=1200, height=800, x="a", y=1), 1920, 1080), "1200x800")

    def test_dialog_min_and_recolour(self):
        self.assertEqual(UI.dialog_min(700, 500, 1920, 1080), (700, 500))
        self.assertEqual(UI.dialog_min(100, 50, 1920, 1080), UI.DIALOG_MIN)
        self.assertEqual(UI.dialog_min(5000, 5000, 1000, 800), (850, 680))
        self.assertEqual(UI.recolour("#333"), UI.TEXT_SOFT); self.assertEqual(UI.recolour("#B00020"), UI.ERROR)
        self.assertEqual(UI.recolour("#123456"), "#123456")

    def test_shortcut_table_is_consistent(self):
        ids = [s[0] for s in UI.SHORTCUTS]; seqs = [s[1] for s in UI.SHORTCUTS]
        self.assertEqual(len(ids), len(set(ids))); self.assertEqual(len(seqs), len(set(seqs)))
        for sid, seq, text, what, scope, safe in UI.SHORTCUTS:
            self.assertIn(scope, ("all", "models", "stickers"))
            self.assertTrue(text and what)
        # keys that edit text must not run while a text box has the focus
        self.assertFalse(UI.shortcut("delete")[5]); self.assertFalse(UI.shortcut("undo")[5])
        rows = UI.help_rows(["A", "B"])
        self.assertIn(("Ctrl+2", "Go to B", "everywhere"), rows); self.assertEqual(len(rows), len(UI.SHORTCUTS) + 2)

    def test_dispatch_scope_and_typing(self):
        ran = []
        scope = ["models"]
        d = UI.make_dispatch({"undo": lambda: ran.append("undo"), "open": lambda: ran.append("open")}, lambda: scope[0],
                             typing=lambda w: getattr(w, "typing", False))
        ev = lambda typing=False: types.SimpleNamespace(widget=types.SimpleNamespace(typing=typing))
        self.assertEqual(d("undo", ev()), "break"); self.assertEqual(ran, ["undo"])
        self.assertIsNone(d("undo", ev(True)))                       # typing in a box: the box's own undo wins
        self.assertEqual(d("open", ev(True)), "break")               # Ctrl+O is fine while typing
        scope[0] = "stickers"
        self.assertIsNone(d("undo", ev()))                           # wrong page
        self.assertIsNone(d("delete", ev()))                         # no handler
        self.assertEqual(ran, ["undo", "open"])
        self.assertTrue(UI.typing_in_text(types.SimpleNamespace(winfo_class=lambda: "TEntry")))
        self.assertFalse(UI.typing_in_text(types.SimpleNamespace(winfo_class=lambda: "Treeview")))
        self.assertFalse(UI.typing_in_text(None))


class FakeBody(FW):
    def __init__(self, width, pos):
        super().__init__()
        self.w, self.pos = width, list(pos)
    def winfo_width(self):
        return self.w
    def sashpos(self, i, v=None):
        if v is None:
            return self.pos[i]
        self.pos[i] = v


def build_app():
    got = {}
    vpk_tool.run_gui(_hook=lambda root, app: got.update(root=root, app=app))
    return got["root"], got["app"]


def write_prefs(d):
    with open(wpk_view.PREFS_PATH, "w") as f:
        json.dump(d, f)


def set_main_prefs(main):
    d = wpk_view.load_prefs()
    d["main"] = main
    write_prefs(d)


class App(unittest.TestCase):
    def setUp(self):
        MB.clear()
        faketk.Style.conf.clear(); faketk.Style.maps.clear()
        set_main_prefs({})
        MBX.askyesno = lambda title, msg, **k: (MB.append(("askyesno", (title, msg))), True)[1]
        self.root, self.app = build_app()

    def test_01_theme_is_applied_in_one_place(self):
        c = faketk.Style.conf
        self.assertEqual(faketk.Style.theme, "clam")
        for name in ("TFrame", "TButton", "VPK.Primary.TButton", "VPK.Danger.TButton", "VPK.Nav.TButton", "Toolbutton", "Treeview",
                     "TCombobox", "TEntry", "TSpinbox", "TPanedwindow", "Sash", "TProgressbar", "TSeparator", "TMenubutton"):
            self.assertIn(name, c, name)
        self.assertEqual(c["TFrame"]["background"], UI.BG)
        self.assertEqual(c["VPK.Primary.TButton"]["background"], UI.ACCENT)
        self.assertEqual(c["TButton"]["background"], "#202a38")                      # the colours the app already used are unchanged
        self.assertIn(("*Toplevel.background", UI.BG), self.root.options)          # dialogs follow the theme
        self.assertIn(("Toplevel", "<Map>"), self.root.class_binds)

    def test_02_dialog_minsize_hook(self):
        hook = self.root.class_binds[("Toplevel", "<Map>")]
        calls = []
        dlg = FW()
        dlg.winfo_toplevel = lambda: dlg
        dlg.minsize = lambda *a: calls.append(a) if a else (1, 1)
        dlg.winfo_reqwidth, dlg.winfo_reqheight = (lambda: 700), (lambda: 500)
        dlg.winfo_screenwidth, dlg.winfo_screenheight = (lambda: 1920), (lambda: 1080)
        hook(types.SimpleNamespace(widget=dlg))
        self.assertEqual(calls, [(700, 500)])
        calls.clear(); dlg.minsize = lambda *a: calls.append(a) if a else (400, 300)           # a dialog that set its own minimum keeps it
        hook(types.SimpleNamespace(widget=dlg)); self.assertEqual(calls, [])
        calls.clear(); dlg.minsize = lambda *a: calls.append(a) if a else (1, 1)
        hook(types.SimpleNamespace(widget=self.root)); self.assertEqual(calls, [])             # the main window is not a dialog

    def menus(self):
        bar = self.root._o["menu"]
        return {e[1]["label"]: e[1]["menu"] for e in bar.entries if e[0] == "cascade"}

    def test_03_menu_bar_shows_every_shortcut(self):
        m = self.menus()
        self.assertEqual(list(m), ["File", "Edit", "View", "Models", "Go", "Help"])
        shown = {e[1]["label"]: e[1].get("accelerator") for e in m["Models"].entries if e[0] == "command"}
        self.assertEqual(shown["Save as…"], "Ctrl+Shift+S"); self.assertEqual(shown["Undo"], "Ctrl+Z")
        self.assertEqual(shown["Delete selected part(s)"], "Delete"); self.assertEqual(shown["Textures and materials…"], "Ctrl+T")
        # every shortcut that is bound is shown in a menu, and the menu text equals the table
        in_menus = {e[1]["accelerator"] for mm in m.values() for e in mm.entries if e[0] == "command" and e[1].get("accelerator")}
        for sid, seq, text, *_ in UI.SHORTCUTS:
            if sid not in ("open", "save"):                                      # (the File menu keeps its older 'Ctrl+O' in the label)
                self.assertIn(text, in_menus, sid)
            self.assertIn(seq, self.root._b)
        go = [e[1]["label"] + "|" + e[1]["accelerator"] for e in m["Go"].entries]
        self.assertEqual(go[:3], ["Livery & Stickers|Ctrl+1", "Vehicle Models|Ctrl+2", "Backfire|Ctrl+3"])

    def test_04_every_models_menu_command_exists_on_the_tab(self):
        for e in self.menus()["Models"].entries:
            if e[0] != "command":
                continue
            cmd = e[1]["command"]
            ran = []
            self.app.models_cmd = lambda meth, ran=ran: ran.append(meth)
            cmd()
            meth = ran[0]
            self.assertTrue(meth == "@search" or callable(getattr(self.app.models, meth)), meth)

    def test_05_ctrl_s_goes_to_the_page_in_front(self):
        a = self.app
        hits = []
        a.save = lambda: hits.append("stickers")
        a.models.save = lambda: hits.append("models")
        a.wheels.save = lambda: hits.append("wheels")
        a.lamps.save = lambda: hits.append("lamps")
        ev = types.SimpleNamespace(widget=FW())
        for page in (0, 1, 3, 4):
            a.show_page(page)
            self.assertEqual(self.root._b["<Control-s>"](ev), "break")
        self.assertEqual(hits, ["stickers", "models", "wheels", "lamps"])               # before: wheels / lamps / ... saved the MODELS tab
        a.show_page(6)                                                                  # engine page has no save: nothing happens, no error
        self.root._b["<Control-s>"](ev)
        self.assertEqual(len(hits), 4)

    def test_06_models_keys_only_on_models_page_and_not_while_typing(self):
        a = self.app
        hits = []
        a.models.do_undo = lambda: hits.append("undo")
        a.models.delete = lambda: hits.append("delete")
        a.models.pack = object()                                                         # something is open
        tree, entry = FW(), FW(); tree.klass, entry.klass = "Treeview", "TEntry"
        a.show_page(0)
        self.assertIsNone(self.root._b["<Control-z>"](types.SimpleNamespace(widget=tree)))
        a.show_page(1)
        self.assertEqual(self.root._b["<Control-z>"](types.SimpleNamespace(widget=tree)), "break")
        self.assertEqual(self.root._b["<Delete>"](types.SimpleNamespace(widget=tree)), "break")
        self.assertIsNone(self.root._b["<Delete>"](types.SimpleNamespace(widget=entry)))        # Delete edits the text box
        self.assertIsNone(self.root._b["<Control-z>"](types.SimpleNamespace(widget=entry)))
        a.show_page(3)
        self.assertIsNone(self.root._b["<Delete>"](types.SimpleNamespace(widget=tree)))         # not the Models page
        self.assertEqual(hits, ["undo", "delete"])

    def test_07_menu_command_with_nothing_open_explains_itself(self):
        a = self.app
        a.models.pack = None
        a.models_cmd("save_as")
        self.assertIn("Open a car .wpk first", a.models.status.get())
        self.assertEqual(a.current_page, 1)                                              # it brought the Models page forward

    def test_08_page_keys_and_shortcut_window(self):
        for i in range(7):
            self.root._b["<Control-Key-%d>" % (i + 1)](types.SimpleNamespace(widget=FW()))
            self.assertEqual(self.app.current_page, i)
        self.assertNotIn("<Control-Key-8>", self.root._b)                                # only as many keys as pages
        self.assertEqual(self.root._b["<F1>"](types.SimpleNamespace(widget=FW())), "break")

    def test_09_window_geometry_is_remembered(self):
        self.app.quit()
        self.assertTrue(self.root.destroyed)
        main = wpk_view.load_prefs()["main"]
        self.assertEqual(main, dict(width=640, height=360, x=120, y=60, maximized=False))   # (the fake window is 640 x 360)
        set_main_prefs(dict(width=1300, height=800, x=40, y=30, maximized=False))
        root, _ = build_app()
        self.assertEqual(root.geom, "1300x800+40+30")
        set_main_prefs(dict(width=1300, height=800, x=5000, y=30))                        # saved on a bigger desktop
        root, _ = build_app()
        self.assertEqual(root.geom, "1300x800+620+30")
        set_main_prefs(dict(width=1300, height=800, x=40, y=30, maximized=True))
        root, _ = build_app()
        self.assertEqual(root.st, "zoomed")
        root.st = "zoomed"                                                                # quitting while maximised keeps the old normal size
        _, app = root, _
        app = build_app()[1]
        app.root.st = "zoomed"; app.quit()
        main = wpk_view.load_prefs()["main"]
        self.assertTrue(main["maximized"]); self.assertEqual((main["width"], main["height"]), (1300, 800))

    def test_10_panel_layout_is_restored_clamped_and_saved(self):
        m = self.app.models
        d = wpk_view.load_prefs(); d["models_layout"] = dict(left=410, right=260, log=False)
        write_prefs(d)
        m._body = FakeBody(1400, (300, 1100)); m._sashes_set = False
        m._place_sashes()
        self.assertEqual(m._body.pos, [410, 1140]); self.assertTrue(m._sashes_set)
        self.assertFalse(m.log_body.winfo_ismapped())                                    # the log was closed last time
        m._body.pos = [60, 1390]                                                         # the user drags both sashes to the edges
        m._clamp_sashes()
        self.assertEqual(m._body.pos, [220, 1160])                                       # minimum sizes (browser 220, inspector 240)
        self.assertEqual(wpk_view.load_prefs()["models_layout"], dict(left=220, right=240, log=False))
        m._body.w = 900; m._body.pos = [410, 1140]; m._place_sashes()                    # window made smaller: panes keep their minimums
        self.assertEqual((m._body.pos[0], 900 - m._body.pos[1]), (300, 240))
        m._toggle_log()                                                                  # showing the log is remembered too
        self.assertTrue(wpk_view.load_prefs()["models_layout"]["log"])

    def test_11_bad_saved_layout_falls_back(self):
        m = self.app.models
        d = wpk_view.load_prefs(); d["models_layout"] = "garbage"
        write_prefs(d)
        m._body = FakeBody(1400, (0, 0)); m._sashes_set = False
        m._place_sashes()
        self.assertEqual(m._body.pos, [300, 1100])

    def test_12_confirmations_share_one_wording(self):
        a = self.app
        m = a.models
        for tab, what in ((m, "this car"), (a.lamps, "this car"), (a.meters, "this file")):
            tab.dirty = False
            MB.clear()
            self.assertTrue(tab.confirm_discard()); self.assertEqual(MB, [])
            tab.dirty = True
            self.assertTrue(tab.confirm_discard())
            title, msg = MB[-1][1][:2]
            self.assertEqual(title, "Unsaved changes")
            self.assertEqual(msg, "Throw away the unsaved changes to %s?\n\nThis cannot be undone." % what)
        a.wheels.dirty, a.wheels.path = True, "C:/cars/wheels.wpk"
        a.wheels.confirm_discard()
        self.assertIn("unsaved changes to wheels.wpk?", MB[-1][1][1])
        # delete a part (Models tab): names the parts, says Undo works
        from test_ui_faketk import settle
        import wpk_edit as E
        from synth import make_pack
        p = make_pack(os.path.join(TMP, "m6_X.wpk")); p.save(os.path.join(TMP, "m6_X.wpk"), backup=False)
        m.open_path(os.path.join(TMP, "m6_X.wpk")); settle(m)
        MB.clear()
        m.tree.selection_set([i for i in m.tree.get_children() if "0x122" in str(m.tree.item(i).get("text", "")) or i == "290"][:1] or ["290"])
        tags = m.sel_tags()
        self.assertTrue(tags)
        m.delete()
        title, msg = MB[-1][1][:2]
        self.assertEqual(title, "Delete")
        self.assertTrue(msg.startswith("Remove 1 part from the car?\n\n• "))
        self.assertTrue(msg.endswith("Nothing is saved until you Save.\n\nUndo brings it back."))
        self.assertIsNone(m.pack.chunk(tags[0]))

    def test_13_button_hierarchy(self):
        m = self.app.models
        self.assertEqual(m.b_save["style"] if "style" in m.b_save._o else m.b_save._o.get("style"), "VPK.Primary.TButton")
        self.assertEqual(m.b_del._o.get("style"), "VPK.Danger.TButton")


if __name__ == "__main__":
    unittest.main(verbosity=2)
