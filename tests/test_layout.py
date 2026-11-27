"""vpk_layout: the wrapping maths, and Flow driven by stub widgets (no display)."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import faketk
faketk.install()
import vpk_layout as VL


class Stub:
    def __init__(self, w, h=24, padx=0, pady=0):
        self.w, self.h, self._pad = w, h, dict(padx=padx, pady=pady)
        self.grid_args, self.gone = None, False
    def pack_info(self): return dict(side="left", **self._pad)
    def pack_forget(self): pass
    def winfo_reqwidth(self): return self.w
    def winfo_reqheight(self): return self.h
    def grid(self, **kw): self.grid_args, self.gone = kw, False
    def grid_remove(self): self.gone = True


class Frame:
    def __init__(self, kids): self.kids = kids
    def pack_slaves(self): return self.kids
    def winfo_rootx(self): return 100


class Limit:
    def __init__(self, w): self.w = w
    def winfo_width(self): return self.w
    def winfo_rootx(self): return 100


class T(unittest.TestCase):
    def test_flow_layout(self):
        pos, widest = VL.flow_layout([(100, 20)] * 5, 250)
        self.assertEqual(pos, [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)])
        self.assertLessEqual(widest, 250 + 0)
        self.assertEqual(VL.flow_layout([(500, 20), (10, 20)], 100)[0], [(0, 0), (1, 0)])    # too wide: its own row
        self.assertEqual(VL.flow_layout([(100, 20)] * 4, 0)[0], [(0, i) for i in range(4)])  # no width known: no wrapping
        self.assertEqual(VL.flow_layout([], 100), ([], 0))

    def test_pad(self):
        self.assertEqual(VL._pad(4), (4, 4)); self.assertEqual(VL._pad((2, 8)), (2, 8)); self.assertEqual(VL._pad("3"), (3, 3))
        self.assertEqual(VL._pad(("1.0", "5")), (1, 5)); self.assertEqual(VL._pad("0 6"), (0, 6))

    def test_flow_wraps_and_follows_the_width(self):
        kids = [Stub(100, padx=(0, 6)) for _ in range(5)]
        lim = Limit(400)
        f = VL.Flow(Frame(kids), lim, margin=0)
        rows = [k.grid_args["row"] for k in kids]
        self.assertEqual(rows, [0, 0, 0, 1, 1])                    # 106 each: three fit in 400
        lim.w = 250
        f.layout()
        self.assertEqual([k.grid_args["row"] for k in kids], [0, 0, 1, 1, 2])   # two fit in 250
        lim.w = 200
        f.layout()
        self.assertEqual(max(k.grid_args["row"] for k in kids), 4)               # one per row
        lim.w = 2000
        f.layout()
        self.assertEqual({k.grid_args["row"] for k in kids}, {0})

    def test_hide_and_show_keep_working(self):
        kids = [Stub(100) for _ in range(3)]
        f = VL.Flow(Frame(kids), Limit(1000), margin=0)
        kids[1].pack_forget()                                      # the old code hiding a widget in the row
        self.assertTrue(kids[1].gone)
        self.assertEqual(kids[2].grid_args["col"] if "col" in kids[2].grid_args else kids[2].grid_args["column"], 1)
        kids[1].pack(side="left")                                  # ... and showing it again
        self.assertFalse(kids[1].gone)
        self.assertEqual(kids[2].grid_args["column"], 2)

    def test_unknown_width_does_not_wrap(self):
        kids = [Stub(300) for _ in range(4)]
        VL.Flow(Frame(kids), Limit(10), margin=0)
        self.assertEqual({k.grid_args["row"] for k in kids}, {0})

    def test_workspace_builds_under_the_fake_tk(self):
        """wiring only: the panes, a collapsible section and a row can be made and toggled without raising"""
        ws = VL.Workspace(faketk.W())
        sec = ws.section("Things")
        sec.toggle(); sec.toggle(True)
        self.assertTrue(sec.opened)
        sec.toggle(False)
        self.assertFalse(sec.opened)
        ws.flow(faketk.W())
        ws.reflow()


if __name__ == "__main__":
    unittest.main()
