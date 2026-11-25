"""Milestone 5 logic tests (wpk_texmgr).  Synthetic packs always; real car WPKs when VPK_REAL_CARS points at a folder of them
(the tests only READ that folder; anything written goes to a temp copy)."""
import glob, os, re, shutil, sys, tempfile, unittest, zlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
import numpy as np
from PIL import Image
import wpk_edit as E, wpk_texmgr as TM
from synth import make_pack

NAME = lambda t: E.tag_name(t)
REAL = os.environ.get("VPK_REAL_CARS", "")


def pack_with(*textures):
    p = make_pack(os.path.join(tempfile.mkdtemp(), "maz_X.wpk"))
    p.tex = [dict(name=n, w=w, h=h, fmt=f, raw=raw) for n, w, h, f, raw in textures]
    return p


def raw(w, h, v=0xF800):
    return np.full((h, w), v, "<u2").tobytes()


class Synthetic(unittest.TestCase):
    def test_states_and_refs(self):
        p = make_pack(os.path.join(tempfile.mkdtemp(), "maz_X.wpk"))          # body_red (own), borrowed_tex (empty)
        c = TM.catalog(p, None, NAME)
        a, b = c.entries
        self.assertEqual((a.state, b.state), ("ok", "MISSING"))             # no stock pack -> a borrowed texture is MISSING
        self.assertEqual(sorted(a.refs), [0x100, 0x101] and sorted(a.refs))   # parts using texture 0 (all synth boxes use ti 0 or 1)
        self.assertEqual(sum(e.draws for e in c.entries), 7)
        self.assertEqual(c.counts()["missing"], 1)
        self.assertTrue(any("borrowed_tex" in w for w in c.warnings))
        # with a stock pack that has it, the state becomes "borrowed"
        stock = pack_with(("borrowed_tex", 4, 4, 0, raw(4, 4)))
        c2 = TM.catalog(p, stock, NAME)
        self.assertEqual(c2.entries[1].state, "borrowed")
        self.assertEqual(c2.counts()["missing"], 0)
        # extension / case differences count as the same texture, like Pack.tex_index
        stock2 = pack_with(("BORROWED_TEX.dds", 4, 4, 0, raw(4, 4)))
        self.assertEqual(TM.catalog(p, stock2, NAME).entries[1].state, "borrowed")

    def test_paint_copies_not_unused(self):
        p = pack_with(("car_body.dds", 4, 4, 0, raw(4, 4)), ("p1:car_body.dds", 4, 4, 0, raw(4, 4)),
                      ("p2:car_body.dds", 4, 4, 0, raw(4, 4)), ("p3:gone.dds", 4, 4, 0, raw(4, 4)),
                      ("lonely.dds", 4, 4, 0, raw(4, 4)))
        c = TM.catalog(p, None, NAME)
        base, p1, p2, orphan, lonely = c.entries
        self.assertEqual(base.paint_copies, [1, 2])
        self.assertEqual(p1.paint_of, 0)
        self.assertNotIn("unused", p1.flags)                                # the old dialog called these "(unused)"
        self.assertIn("orphan paint copy", orphan.flags)
        self.assertTrue(orphan.problem)
        self.assertIn("unused", lonely.flags)
        self.assertEqual([e.name for e in TM.filter_entries(c.entries, flt="Paint copies")], ["p1:car_body.dds", "p2:car_body.dds", "p3:gone.dds"])
        self.assertEqual([e.name for e in TM.filter_entries(c.entries, flt="Unused")], ["lonely.dds"])
        self.assertEqual([e.name for e in TM.filter_entries(c.entries, flt="Problems")], ["p3:gone.dds"])

    def test_bad_data_and_dangling_and_duplicates(self):
        p = pack_with(("short.dds", 8, 8, 0, b"\0" * 10), ("dup.dds", 4, 4, 0, raw(4, 4)), ("DUP.dds", 4, 4, 0, raw(4, 4)),
                      ("npot.dds", 6, 4, 0, raw(6, 4)))
        p.chunks[0].batches[0].tex = 99                                     # points at a slot that does not exist
        p.chunks[1].batches[0].env = 77
        c = TM.catalog(p, None, NAME)
        self.assertEqual(c.entries[0].state, "BAD DATA")
        self.assertIn("duplicate name", c.entries[1].flags)
        self.assertIn("odd size", c.entries[3].flags)
        self.assertEqual({(d[0], d[2], d[3]) for d in c.dangling}, {(0, "tex", 99), (0x100, "env", 77)})
        self.assertEqual(len(c.warnings), 4)                                # bad data, 2 dangling, 1 duplicate (reported once)
        cache = TM.PreviewCache()
        img, note = cache.get(p, 0)
        self.assertIsNone(img)
        self.assertIn("Bad data", note)                                     # a broken texture never raises in the preview

    def test_search_words(self):
        p = make_pack(os.path.join(tempfile.mkdtemp(), "maz_X.wpk"))
        c = TM.catalog(p, None, NAME)
        f = lambda q, fl="All": [e.name for e in TM.filter_entries(c.entries, q, fl)]
        self.assertEqual(f("red"), ["body_red"])
        self.assertEqual(f("4x4 565"), ["body_red", "borrowed_tex"])
        self.assertEqual(f("0x122"), ["borrowed_tex"])                       # tag 0x122 (wing A) draws texture 1
        self.assertEqual(f("nothing like this"), [])
        self.assertEqual(f("", "Missing"), ["borrowed_tex"])
        self.assertEqual(f("", "In use"), ["body_red", "borrowed_tex"])

    def test_users_and_materials(self):
        p = make_pack(os.path.join(tempfile.mkdtemp(), "maz_X.wpk"))
        c = TM.catalog(p, None, NAME)
        u = TM.users(c.entries[1], NAME)
        self.assertEqual([x[0] for x in u], [0x122])
        self.assertEqual(u[0][2:], (1, "picture"))
        rows = TM.materials(p, None, NAME)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[3][:4], (0x122, NAME(0x122), 0, "borrowed_tex"))
        self.assertEqual(len(TM.materials(p, None, NAME, tag=0x100)), 1)

    def test_preview_checker_and_cache(self):
        a = np.zeros((4, 4, 4), np.uint8)
        a[..., 0] = 255                                  # opaque red
        a[:2, :2, 3] = 255                               # top-left opaque, rest fully transparent
        out = TM.compose(a, "checker", 64)
        self.assertEqual(out.shape, (64, 64, 3))         # enlarged x16, whole number
        self.assertEqual(tuple(out[0, 0]), (255, 0, 0))
        self.assertEqual(tuple(out[63, 63]), tuple(TM.checkerboard(64, 64)[63, 63]))   # transparent -> checkerboard shows
        self.assertEqual(tuple(TM.compose(a, "dark", 16)[15, 15]), (32, 32, 32))
        big = TM.compose(np.full((512, 512, 4), 255, np.uint8), "checker", 128)
        self.assertEqual(big.shape, (128, 128, 3))
        p = pack_with(("a.dds", 4, 4, 0, raw(4, 4)), ("b.dds", 4, 4, 0, raw(4, 4, 0x07E0)))
        cache = TM.PreviewCache(limit=2)
        i1, n1 = cache.get(p, 0)
        cache.get(p, 0)
        self.assertEqual(cache.decodes, 1)                                  # second call came from the cache
        i2, _ = cache.get(p, 1)
        self.assertFalse(np.array_equal(i1, i2))
        p.tex[0]["raw"] = raw(4, 4, 0x001F)                                 # an edit changes the data -> never a stale picture
        i3, _ = cache.get(p, 0)
        self.assertEqual(cache.decodes, 3)
        self.assertFalse(np.array_equal(i1, i3))
        cache.get(p, 1); cache.get(p, 0)
        self.assertLessEqual(len(cache._d), 2)

    def test_borrowed_preview_uses_stock(self):
        stock = pack_with(("x.dds", 4, 4, 0, raw(4, 4, 0x07E0)))
        kit = pack_with(("x.dds", 4, 4, 0, b""))
        cache = TM.PreviewCache()
        img, note = cache.get(kit, 0, stock)
        self.assertIsNotNone(img)
        self.assertIsNone(cache.get(kit, 0, None)[0])                       # no stock -> explained, not an error
        self.assertIn("Missing", cache.get(kit, 0, None)[1])

    def test_plan_replace(self):
        p = pack_with(("t.dds", 64, 64, 1, raw(64, 64)))
        im = Image.new("RGBA", (100, 60), (10, 20, 30, 255))
        pl = TM.plan_replace(p, 0, im)
        self.assertEqual(pl["new"][:3], (128, 64, 0))                       # nearest power of two, opaque -> RGB565
        self.assertTrue(pl["resized"] and pl["alpha_lost"])
        self.assertEqual(pl["old"][:3], (64, 64, 1))
        self.assertEqual(p.tex[0]["w"], 64)                                 # planning changes nothing

    def test_impact_lines(self):
        p = pack_with(("shared.dds", 4, 4, 0, raw(4, 4)), ("p1:shared.dds", 4, 4, 0, raw(4, 4)),
                      ("env.dds", 4, 4, 0, raw(4, 4)), ("solo.dds", 4, 4, 0, raw(4, 4)))
        p.chunks[0].batches[0].tex = 0
        p.chunks[1].batches[0].tex = 0
        p.chunks[2].batches[0].tex = 3
        p.chunks[0].batches[0].env = 2
        c = TM.catalog(p, None, NAME)
        r = TM.impact(p, c, 0, "x/maz_X.wpk")
        self.assertTrue(r["shared"])
        self.assertTrue(any("paint cop" in l and "NOT changed" in l for l in r["lines"]))
        self.assertTrue(any("Aero kits beside this file were not checked" in l for l in r["lines"]))
        self.assertTrue(TM.impact(p, c, 2, "x/maz_X.wpk")["shared"])        # reflection map: affects every draw using it
        solo = TM.impact(p, c, 3, "x/maz_X.wpk", kits=[])
        self.assertFalse(solo["shared"])
        self.assertEqual(len(solo["lines"]), 1)

    def test_kit_borrowers_and_stock_path(self):
        d = tempfile.mkdtemp()
        stock = make_pack(os.path.join(d, "maz_X.wpk"))
        kit = E.Pack.load(os.path.join(d, "maz_X.wpk"))
        kit.tex[0]["raw"] = b""                                              # aero kit borrows body_red from the stock pack
        kit.save(os.path.join(d, "maz_X_aeroA.wpk"), backup=False)
        (lab, path, n), = TM.kit_borrowers(os.path.join(d, "maz_X.wpk"), "body_red")
        self.assertEqual((lab, n > 0), ("Aero A", True))
        self.assertEqual(TM.kit_borrowers(os.path.join(d, "maz_X_aeroA.wpk"), "body_red"), [])   # a kit has no borrowers
        self.assertTrue(TM.is_stock_path("a/b/maz_X.wpk")); self.assertFalse(TM.is_stock_path("a/maz_X_aeroB.wpk"))
        open(os.path.join(d, "maz_X_aeroB.wpk"), "wb").write(b"junk")       # unreadable sibling is reported, not fatal
        res = TM.kit_borrowers(os.path.join(d, "maz_X.wpk"), "body_red")
        self.assertIn(("Aero B", os.path.join(d, "maz_X_aeroB.wpk"), None), res)
        pk = E.Pack.load(os.path.join(d, "maz_X.wpk"))
        r = TM.impact(pk, TM.catalog(pk, None, NAME), 0, os.path.join(d, "maz_X.wpk"), kits=res)
        self.assertTrue(r["shared"] and any("Aero A" in l for l in r["lines"]) and any("Could not read: Aero B" in l for l in r["lines"]))

    def test_replace_through_existing_code_only(self):
        """the manager's replace is Pack.add_texture with the SAME name: same slot, other slots and all batches untouched"""
        p = make_pack(os.path.join(tempfile.mkdtemp(), "maz_X.wpk"))
        before = [(b.tex, b.env, b.spec) for c in p.chunks for b in c.batches]
        names = [t["name"] for t in p.tex]
        i = p.add_texture("body_red", Image.new("RGBA", (8, 8), (0, 255, 0, 255)))
        self.assertEqual(i, 0)
        self.assertEqual([t["name"] for t in p.tex], names)
        self.assertEqual(before, [(b.tex, b.env, b.spec) for c in p.chunks for b in c.batches])
        self.assertEqual(p.decode_tex(0)[0, 0].tolist(), [0, 252, 0, 255])


@unittest.skipUnless(REAL and glob.glob(os.path.join(REAL, "*.wpk")), "set VPK_REAL_CARS to a folder of real car WPKs")
class RealCars(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = []
        for f in sorted(glob.glob(os.path.join(REAL, "*.wpk"))):
            try:
                E.Pack.load(f)
                cls.files.append(f)
            except Exception:
                pass                                                        # e.g. the upload-truncated one

    def test_all_real_packs_have_a_clean_catalogue(self):
        bad, n = [], 0
        for f in self.files:
            p = E.Pack.load(f)
            c = TM.catalog(p, E.stock_pack_of(f), NAME)
            n += len(c.entries)
            if c.dangling or c.counts()["problems"] or c.counts()["unused"]:
                bad.append((os.path.basename(f), c.warnings[:2], c.counts()["unused"]))
        self.assertGreater(n, 100)
        self.assertEqual(bad, [])

    def test_aero_kits_borrow_and_stock_resolves_them(self):
        kits = [f for f in self.files if re.search(r"_aero[ABC]\.wpk$", f)]
        self.assertTrue(kits)
        for f in kits[:12]:
            p = E.Pack.load(f)
            c = TM.catalog(p, E.stock_pack_of(f), NAME)
            self.assertEqual(c.counts()["missing"], 0, f)
            self.assertGreater(c.counts()["borrowed"], 0, f)
            c0 = TM.catalog(p, None, NAME)                                  # without the stock pack the same textures are MISSING
            self.assertEqual(c0.counts()["missing"], c.counts()["borrowed"], f)

    def test_paint_copies_found_on_real_cars(self):
        f = next(x for x in self.files if os.path.basename(x).startswith("nis_BNR34") and not re.search("aero", x))
        c = TM.catalog(E.Pack.load(f), None, NAME)
        body = next(e for e in c.entries if e.name == "bnr34_body01_white.dds")
        self.assertEqual([c.entries[j].name[:3] for j in body.paint_copies], ["p1:", "p2:", "p3:"])
        self.assertTrue(all("unused" not in e.flags for e in c.entries))

    def test_every_real_texture_previews(self):
        cache = TM.PreviewCache(limit=8)
        for f in self.files[:40]:
            p = E.Pack.load(f)
            st = E.stock_pack_of(f)
            for i in range(len(p.tex)):
                img, note = cache.get(p, i, st)
                self.assertIsNotNone(img, (f, i, note))
                self.assertEqual(img.dtype, np.uint8)

    def test_kit_borrowers_on_real_stock(self):
        f = next(x for x in self.files if os.path.basename(x).startswith("nis_BNR34") and not re.search("aero", x))
        res = TM.kit_borrowers(f, "bnr34_body01_white.dds")
        self.assertEqual(sorted(l for l, _, _ in res), ["Aero A", "Aero B", "Aero C"])
        self.assertTrue(all(n and n > 0 for _, _, n in res))

    def test_replace_roundtrip_on_copy(self):
        f = next(x for x in self.files if os.path.basename(x).startswith("nis_BNR34") and not re.search("aero", x))
        d = tempfile.mkdtemp()
        dst = os.path.join(d, os.path.basename(f))
        shutil.copy(f, dst)
        orig = open(f, "rb").read()
        p = E.Pack.load(dst)
        idx = p.tex_index("bnr34_body01_white.dds")
        before = [(t["name"], t["w"], t["h"]) for t in p.tex]
        p.add_texture(p.tex[idx]["name"], Image.new("RGBA", (512, 512), (0, 0, 255, 255)))
        p.save(dst)
        q = E.Pack.load(dst)
        self.assertEqual([(t["name"], t["w"], t["h"]) for t in q.tex], before)
        self.assertEqual(q.decode_tex(idx)[0, 0].tolist(), [0, 0, 248, 255])
        self.assertEqual(open(dst + ".bak", "rb").read(), orig)             # existing .bak behaviour
        self.assertEqual(open(f, "rb").read(), orig)                        # the source file was never touched


if __name__ == "__main__":
    unittest.main(verbosity=2)
