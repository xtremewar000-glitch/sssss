"""Playtest: the map reader, the wheel postures / camera maths, the car's draw list, the physics bridge (only with the library and a data
folder: VPK_PLAYTEST_DATA) and the module's start / stop / keys with a fake physics and a fake viewport.  NOT a visual test."""
import math, os, struct, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp()
os.environ["HOME"] = TMP
import numpy as np
import faketk
MB = faketk.install()
import stage_wpk as S
import playtest_gl as PG
import playtest as P
REAL_CORE, REAL_FIND = P.PlayCore, P.find_library


def make_wpk2(path):
    """a tiny map: one texture (RGB565 2x2), one chunk, one batch of one triangle"""
    tex = struct.pack("<4H", 0xF800, 0x07E0, 0x001F, 0xFFFF)
    verts = b"".join(struct.pack("<5f4B", x, y, z, u, v, 255, 255, 255, 255) for x, y, z, u, v in ((0, 0, 0, 0, 0), (10, 0, 0, 1, 0), (0, 0, 10, 0, 1)))
    idx = struct.pack("<3H", 0, 1, 2)
    hdr = 32
    tex_off, chunk_off, batch_off = hdr, hdr + 64, hdr + 64 + 36
    tdata = batch_off + 20
    vo = tdata + len(tex)
    io = vo + len(verts)
    out = struct.pack("<4sIIIfIII", b"WPK2", 1, 1, 1, 4000.0, tex_off, chunk_off, batch_off)
    out += struct.pack("<48sHHIII", b"road", 2, 2, 0, tdata, len(tex))
    out += struct.pack("<6fIII", 0, 0, 0, 10, 0, 10, 0, 1, 0)
    out += struct.pack("<HHIIII", 0, 0, vo, 3, io, 3)
    out += tex + verts + idx
    open(path, "wb").write(out)


class Maths(unittest.TestCase):
    def test_stage_reader(self):
        p = os.path.join(TMP, "m.wpk"); make_wpk2(p)
        st = S.load(p)
        self.assertEqual((len(st.textures), len(st.chunks), st.n_tris), (1, 1, 1))
        self.assertEqual(st.textures[0][1].shape, (2, 2, 4))
        self.assertEqual(tuple(st.textures[0][1][0, 0]), (255, 0, 0, 255))          # RGB565 red
        b = st.chunks[0].batches[0]
        self.assertEqual(b.V["pos"][1].tolist(), [10, 0, 0]); self.assertEqual(b.I.tolist(), [0, 1, 2])
        open(p, "wb").write(b"WPK3" + bytes(60))
        with self.assertRaises(S.StageError): S.load(p)

    def test_wheel_matrix_stands_at_the_hub_height(self):
        t = np.eye(4); t[1, 3] = 31.0                              # posture: identity, hub height 31 (numpy layout)
        tyre = t.T.reshape(16)                                     # the physics gives GL column-major
        w = PG.wheel_matrix(tyre, 0.0, (5.0, 20.0, 7.0))
        c = w @ np.array([5.0, 20.0, 7.0, 1.0])
        self.assertTrue(np.allclose(c[:3], [5.0, 31.0, 7.0]))      # the chunk's centre lands at (x, hub height, z)
        w = PG.wheel_matrix(tyre, math.pi / 2, (5.0, 20.0, 7.0))   # spinning keeps the centre fixed too
        self.assertTrue(np.allclose((w @ np.array([5.0, 20.0, 7.0, 1.0]))[:3], [5.0, 31.0, 7.0]))
        self.assertTrue(np.allclose(w[:3, :3] @ w[:3, :3].T, np.eye(3)))

    def test_camera_matches_the_games(self):
        st = dict(cam_view=np.eye(4).reshape(16), cam_fov=1.27, cam_near=10.0, cam_pos=[1, 2, 3])
        v = PG.PlayView(st, (800, 450))
        self.assertEqual(v.size, (800, 450))
        fovy = 2 * math.atan(math.tan(1.27 / 2) / (4 / 3))
        self.assertAlmostEqual(v.P[1, 1], 1 / math.tan(fovy / 2), 6)
        self.assertAlmostEqual(v.P[0, 0], v.P[1, 1] / (800 / 450), 6)       # a wider window sees more at the sides
        self.assertEqual(v.R.shape, (3, 3))

    def test_box_distance_and_env(self):
        lo, hi = np.array([0, 0, 0.]), np.array([10, 10, 10.])
        self.assertEqual(PG.box_distance(lo, hi, np.array([5, 5, 5.])), 0.0)
        self.assertAlmostEqual(PG.box_distance(lo, hi, np.array([13, 14, 5.])), 5.0)
        self.assertEqual(PG.env_of("hakone", True)[0], 3600.0)
        self.assertEqual(PG.env_of("nowhere", False)[0], 25000.0)

    def test_car_draw_list(self):
        def vb(n): 
            V = np.zeros(n, [("pos", "<f4", 3), ("uv", "<f4", 2), ("rgba", "u1", 4), ("nrm", "<i2", 3), ("pad", "<i2")]); V["rgba"] = 255; return V
        batch = lambda: dict(tex=0, flags=0, V=vb(3), I=np.array([[0, 1, 2]]), prog=1, env=0, spec=0, power=0.0)
        tex = [("t", np.full((2, 2, 4), 255, np.uint8))]
        chunks = [(0,)*6 + (0, 1, tag) for tag in (0, 0x100, 0x110, 0x111, 0x112, 0x113, 0xFFFF, 0x141)]
        model = (tex, chunks, [batch() for _ in chunks])
        model = (tex, [c[:6] + (i, 1, c[8]) for i, c in enumerate(chunks)], model[2])
        car = PG.prepare_car(model, hidden=set())
        self.assertEqual(car["shown"](False), {0, 0x100, 0x111, 0x112})            # unbraked: running lamps, no stop glow, no high brake
        self.assertEqual(car["shown"](True), {0, 0x100, 0x111, 0x113, 0x141})
        self.assertEqual(PG.prepare_car(model, hidden={0x100})["shown"](False), {0, 0x111, 0x112})

    def test_pack_names_and_files(self):
        self.assertEqual(P.pack_name_of("C:/x/maz_FD3S_aeroB.wpk"), "maz_FD3S")
        self.assertEqual(P.pack_name_of("nis_Z33.wpk"), "nis_Z33")
        d = os.path.join(TMP, "data"); os.makedirs(os.path.join(d, "carconfig"))
        self.assertEqual(P.check_data(d, "hakone", False), ["phys.img", "hakone.wpk"])
        open(os.path.join(d, "phys.img"), "wb").close(); open(os.path.join(d, "hakone_night.wpk"), "wb").close()
        self.assertEqual(P.check_data(d, "hakone", True), [])
        self.assertEqual(P.map_files(d, "hakone", True)[1], None)


class Real(unittest.TestCase):
    """the library and the game's data (skipped without them)"""
    def test_drive(self):
        data, lib = os.environ.get("VPK_PLAYTEST_DATA"), REAL_FIND()
        if not (data and lib and os.path.isfile(os.path.join(data, "phys.img"))):
            self.skipTest("needs VPK_PLAYTEST_DATA and the physics library")
        core = REAL_CORE(lib); core.init(data)
        info = core.car_info("nis_Z33"); self.assertEqual(info[1], 6)
        core.start(9, info)
        s0 = core.read()
        for f in range(400): core.step(0, 1.0, 0)
        s1 = core.read()
        self.assertGreater(s1["total"], 2.0)
        self.assertGreater(np.linalg.norm(s1["body"][12:15] - s0["body"][12:15]), 1000)       # it drove (units of 0.1 m)
        self.assertAlmostEqual(s1["cam_fov"], 1.27, 1)


class FakeGL:
    def __init__(self): self.extras, self.view_fn, self.draws, self.bg = [], None, 0, None
    def request_draw(self): self.draws += 1
    def set_background(self, t, b): self.bg = (t, b)


class FakeCore:
    def __init__(self, lib): self.steps, self.started = [], None
    def init(self, d): pass
    def car_info(self, name): return None if name == "zzz_none" else (5, 6, [1.0] * 6, 5)
    def start(self, course, info, manual, power, handling, traffic): self.started = (course, info, manual, power, handling, traffic)
    def step(self, *a): self.steps.append(a)
    def read(self):
        return dict(body=np.eye(4).reshape(16), tyre=np.zeros((4, 16)), tyre_rot=np.zeros(4), cam_view=np.eye(4).reshape(16), cam_pos=np.zeros(3),
                    cam_fov=1.27, cam_near=10.0, kmh=100.0, rpm=5000.0, gear=3, speed=1.0, on_ground=1, countdown=0.0, total=65.5, time_left=30.0,
                    remain=5000.0, section=0, play_state=2, brake=0.0)
    def reset(self): self.steps.append("reset")
    def view(self, v): self.steps.append(("view", v))
    def manual(self, m): self.steps.append(("manual", m))


class Module(unittest.TestCase):
    def setUp(self):
        self.d = os.path.join(TMP, "gd"); os.makedirs(os.path.join(self.d, "carconfig"), exist_ok=True)
        for n in ("phys.img", "hakone.wpk"): open(os.path.join(self.d, n), "wb").close()
        make_wpk2(os.path.join(self.d, "hakone.wpk"))
        host = types.SimpleNamespace(pack=object(), path="C:/cars/nis_Z33.wpk", stock=None, hidden=set(), gl=FakeGL(), log=lambda *a, **k: None,
                                     render=lambda reuse=False: None, modules={}, undo=[])
        host.module_left = lambda k: faketk.W(); host.module_bar = lambda k: faketk.W(); host.module_right = lambda k: types.SimpleNamespace(
            outer=faketk.W(), body=faketk.W(), canvas=faketk.W(), bar=faketk.W())
        host.register_module = lambda k, o: host.modules.__setitem__(k, o)
        self.host = host
        P.find_library = lambda extra=None: "fake.so"
        P.PlayCore = FakeCore
        PG_view_model = __import__("wpk_edit").view_model
        __import__("wpk_edit").view_model = lambda pack, stock=None: ([], [], [])
        self.root = faketk.W()
        self.t = P.build_tab(faketk.W(), self.root, None, host=host)
        self.t.v_data.set(self.d); self.t.active = True

    def test_start_and_drive(self):
        t = self.t
        t.start()
        self.assertTrue(t.running, t.status.text if hasattr(t.status, "text") else "")
        self.assertIn(t.layer, self.host.gl.extras); self.assertIsNotNone(self.host.gl.view_fn)
        self.assertEqual(t.core.started[0], 9)                                   # Hakone
        t.keys.update({"up", "left"}); t.last -= 0.05
        t.tick()
        self.assertTrue(t.core.steps)
        s = next(x for x in t.core.steps if isinstance(x, tuple) and len(x) == 5)
        self.assertEqual(s[:3], (-1.0, 1.0, 0.0))                                # steer left, gas
        self.assertGreater(self.host.gl.draws, 0)
        self.assertIn("100 km/h", t.hud.get()) if t.frames % 6 == 0 else None
        t.on_leave()
        self.assertIsNone(self.host.gl.view_fn); self.assertNotIn(t.layer, self.host.gl.extras)
        t.on_enter(); self.assertIsNotNone(self.host.gl.view_fn)
        t.stop(); self.assertFalse(t.running); self.assertIsNone(self.host.gl.view_fn)

    def test_refuses_politely(self):
        t = self.t
        self.host.pack = None; t.start(); self.assertFalse(t.running)
        self.host.pack = object(); self.host.gl = None; t.start(); self.assertFalse(t.running)
        self.host.gl = FakeGL(); t.v_data.set(os.path.join(TMP, "nothing")); t.start(); self.assertFalse(t.running)

    def test_unknown_car_drives_like_the_rx7_and_open_stops(self):
        t = self.t
        self.host.path = "C:/cars/zzz_none.wpk"
        t.start(); self.assertTrue(t.running)
        t.pack_changed("open"); self.assertFalse(t.running)

    def test_shown_tags_hide_the_parts_model_while_running(self):
        t = self.t
        self.assertEqual(t.shown_tags({1, 2}), {1, 2})
        t.start(); self.assertEqual(t.shown_tags({1, 2}), set())


if __name__ == "__main__":
    unittest.main()
