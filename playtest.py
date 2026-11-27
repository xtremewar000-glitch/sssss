"""
playtest.py  -  drive the car you are editing on the game's own map, with the game's own physics and camera.

The arcade's race code (recompiled to C in the Re3DXPlus repo: car.c + phys_rc.c) is built as a small library (vpk_playtest.c wraps it:
see playtest_native/); this module loads it with ctypes, feeds it the keys 60 times a second and reads back where the car, its wheels and
the camera are.  The Models page's GPU viewport draws the map (playtest_gl.py) and the car you have open in Parts.

    PlayCore       no window: library + physics (testable:  python playtest.py selftest DATA_DIR [LIB])
    build_tab      the "Playtest" module of the Vehicle Models page

Needs, from the game: <data>/phys.img, <data>/carconfig/, <data>/<city>.wpk (+ <city>_night.wpk, sky_<city>.wpk, sky_<city>_night.wpk).
"""
import ctypes as C
import os
import sys
import time

import numpy as np

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    tk = ttk = filedialog = messagebox = None

HERE = os.path.dirname(os.path.abspath(__file__))

# car.h's course order (the first column of the table of the C code): label, index, city file stem
COURSES = [
    ("Tokyo: C1 inner loop", 0, "tokyo"), ("Tokyo: C1 outer loop", 1, "tokyo"), ("Tokyo: Ring, left", 2, "tokyo"),
    ("Tokyo: Ring, right", 3, "tokyo"), ("Tokyo: Wangan east", 4, "tokyo"), ("Kanagawa: K1 down", 5, "tokyo"),
    ("Nagoya", 6, "nagoya"), ("Osaka: Tonbori", 7, "oosaka"), ("Fukuoka", 8, "fukuoka"), ("Hakone: outward", 9, "hakone"),
    ("Tokyo: Shutoko Tennouzu up", 10, "tokyo"), ("Kanagawa: Shutoko Tokyoko down", 11, "tokyo"),
]
LIB_NAMES = {"win32": ("vpkplay.dll",), "darwin": ("libvpkplay.dylib",)}.get(sys.platform, ("libvpkplay.so",))
N_STATE = 122 + 12 * 17

# offsets in the float block pt_read fills (vpk_playtest.c)
BODY, TYRE, TYRE_ROT, CAM_VIEW, CAM_POS, CAM_FOV, CAM_NEAR = 0, 16, 80, 84, 100, 103, 104
KMH, RPM, GEAR, SPEED, ON_GROUND, COUNTDOWN, TOTAL, TIME_LEFT, REMAIN, SECTION, PLAY_STATE = 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115
BRAKE = 117


def _bases():
    """folders to look in: the bundle's, the program's, the folder holding a macOS .app, this file's, playtest_native"""
    b = []
    if getattr(sys, "_MEIPASS", None):
        b.append(sys._MEIPASS)
    if getattr(sys, "frozen", False):
        exe = os.path.dirname(sys.executable)
        b.append(exe)
        if sys.platform == "darwin" and exe.endswith(os.path.join("Contents", "MacOS")):
            b += [os.path.dirname(os.path.dirname(os.path.dirname(exe))), os.path.dirname(os.path.dirname(exe))]    # beside the .app, and Contents/
    return b + [HERE, os.path.join(HERE, "playtest_native")]


def find_library(extra=None):
    """the physics library: a path the user chose, then beside the program / this file / playtest_native"""
    cands = [extra] if extra else []
    env = os.environ.get("VPK_PLAYTEST_LIB")
    if env:
        cands.append(env)
    bases = _bases()
    for b in bases:
        for n in LIB_NAMES:
            cands.append(os.path.join(b, n))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def find_data(extra=None):
    """the game's data folder: the one chosen, else a `playtest_data` folder beside the program (so a copy shared with a friend can carry it)"""
    bases = _bases()
    for c in ([extra] if extra else []) + [os.path.join(b, "playtest_data") for b in bases]:
        if c and os.path.isfile(os.path.join(c, "phys.img")):
            return c
    return extra or ""


class PlayError(Exception):
    pass


class PlayCore:
    """the game's race code behind a few calls.  One at a time (the library keeps the race in static memory)."""

    def __init__(self, lib_path):
        try:
            if sys.platform == "win32":
                try:
                    os.add_dll_directory(os.path.dirname(os.path.abspath(lib_path)))     # its neighbours (a runtime DLL beside it)
                except (AttributeError, OSError):
                    pass
                self.lib = C.CDLL(os.path.abspath(lib_path), winmode=0)
            else:
                self.lib = C.CDLL(lib_path)
        except OSError as ex:
            raise PlayError("cannot load the physics library %s: %s%s" % (lib_path, ex, "\nIt needs a 64-bit build, and every DLL it depends on, beside it." if sys.platform == "win32" else ""))
        L = self.lib
        L.pt_size.restype = C.c_int
        if L.pt_size() != N_STATE:
            raise PlayError("the physics library is from another version of the tool (state size %d, expected %d)" % (L.pt_size(), N_STATE))
        L.pt_init.argtypes = [C.c_char_p, C.c_char_p, C.c_int]
        L.pt_start.argtypes = [C.c_int, C.c_int, C.c_uint, C.c_int, C.POINTER(C.c_float), C.c_int, C.c_int, C.c_int, C.c_int]
        L.pt_step.argtypes = [C.c_float, C.c_float, C.c_float, C.c_int, C.c_int]
        L.pt_read.argtypes = [C.POINTER(C.c_float)]
        L.pt_car_info.argtypes = [C.c_char_p, C.POINTER(C.c_uint), C.POINTER(C.c_int), C.POINTER(C.c_float), C.POINTER(C.c_int)]
        L.pt_manual.argtypes = [C.c_int]
        L.pt_view.argtypes = [C.c_int]
        self.buf = (C.c_float * N_STATE)()
        self.started = False

    def init(self, data_dir):
        err = C.create_string_buffer(512)
        if self.lib.pt_init(data_dir.encode("utf-8"), err, 512) != 0:
            raise PlayError(err.value.decode("utf-8", "replace") or "cannot start the physics")

    def car_info(self, pack):
        """(ECarType, gears, tyres[6], spokes) of a car pack name such as "nis_Z33"; None for a car the arcade does not know"""
        t, g, sp = C.c_uint(), C.c_int(), C.c_int()
        ty = (C.c_float * 6)()
        if not self.lib.pt_car_info(pack.encode("ascii", "replace"), C.byref(t), C.byref(g), ty, C.byref(sp)):
            return None
        return t.value, g.value, list(ty), sp.value

    def start(self, course, info, manual=False, power=0, handling=0, traffic=False):
        t, g, ty, sp = info
        arr = (C.c_float * 6)(*ty)
        if self.lib.pt_start(int(course), int(bool(manual)), t, g, arr, sp, int(power), int(handling), int(bool(traffic))) != 0:
            raise PlayError("the physics could not start this course (is phys.img the game's, and does the data folder have carconfig?)")
        self.started = True

    def step(self, steer, throttle, brake, up=0, down=0):
        self.lib.pt_step(float(steer), float(throttle), float(brake), int(up), int(down))

    def read(self):
        if not self.started or self.lib.pt_read(self.buf) != 0:
            return None
        a = np.frombuffer(self.buf, np.float32).copy()
        return dict(body=a[BODY:BODY + 16], tyre=a[TYRE:TYRE + 64].reshape(4, 16), tyre_rot=a[TYRE_ROT:TYRE_ROT + 4],
                    cam_view=a[CAM_VIEW:CAM_VIEW + 16], cam_pos=a[CAM_POS:CAM_POS + 3], cam_fov=float(a[CAM_FOV]), cam_near=float(a[CAM_NEAR]),
                    kmh=float(a[KMH]), rpm=float(a[RPM]), gear=int(a[GEAR]), speed=float(a[SPEED]), on_ground=int(a[ON_GROUND]),
                    countdown=float(a[COUNTDOWN]), total=float(a[TOTAL]), time_left=float(a[TIME_LEFT]), remain=float(a[REMAIN]),
                    section=int(a[SECTION]), play_state=int(a[PLAY_STATE]), brake=float(a[BRAKE]))

    def reset(self):
        self.lib.pt_reset()

    def view(self, v):
        self.lib.pt_view(int(v))

    def manual(self, m):
        self.lib.pt_manual(int(bool(m)))


def pack_name_of(path):
    """maz_FD3S_aeroA.wpk -> maz_FD3S"""
    import re
    b = os.path.splitext(os.path.basename(path or ""))[0]
    return re.sub(r"_aero[ABC]$", "", b, flags=re.I)


def map_files(data_dir, city, night):
    """(map pack, sky pack) paths for a city and time of day (the sky may be missing)"""
    z = "_night" if night else ""
    m = os.path.join(data_dir, "%s%s.wpk" % (city, z))
    s = os.path.join(data_dir, "sky_%s%s.wpk" % (city, z))
    return m, (s if os.path.isfile(s) else None)


def check_data(data_dir, city, night):
    """a list of what is missing from the game's data folder (empty: fine)"""
    miss = []
    if not os.path.isfile(os.path.join(data_dir, "phys.img")):
        miss.append("phys.img")
    if not os.path.isdir(os.path.join(data_dir, "carconfig")):
        miss.append("carconfig/ (folder)")
    m, _ = map_files(data_dir, city, night)
    if not os.path.isfile(m):
        miss.append(os.path.basename(m))
    return miss


# =========================================================================================================
# the Models page's Playtest module
# =========================================================================================================
HELP = """Playtest  -  drive the open car on the game's map

You need the game's data folder (phys.img, carconfig, and the city's .wpk files) and the physics library (vpkplay.dll / libvpkplay.dylib
/ libvpkplay.so, built from the Re3DXPlus repo: see playtest_native/README.txt).

Keys:  Up / W  gas      Down / S  brake      Left, Right / A, D  steer      Space  brake
       Z / X  gear down / up (manual gearbox)      V  change view (behind the car / roof)      R  put the car back on the road
       Esc  stop

The physics, the camera and the handling are the arcade's own (the same code the Android port runs).  The map is drawn simply: no gloss,
no reflections, no traffic cars, no HUD."""


def build_tab(tab_parent, root, app=None, host=None):
    import wpk_edit as we
    import vpk_layout as VL
    import vpk_ui as UI
    try:
        import wpk_view
    except ImportError:
        wpk_view = None
    host = host or getattr(app, "models", None)

    def pref(key, default=None):
        try:
            return (wpk_view.load_prefs().get(key) if wpk_view else None) or default
        except Exception:
            return default

    def set_pref(**kv):
        try:
            if wpk_view:
                wpk_view.save_prefs(**kv)
        except Exception:
            pass

    class Tab:
        pack = property(lambda s: host.pack)
        gl = property(lambda s: host.gl)

        def __init__(self):
            self.host = host
            self.core = None
            self.layer = None
            self.active = False
            self.running = False
            self.stage_cache = {}
            self.keys = set()
            self.release_jobs = {}
            self.edge = dict(up=0, down=0)
            self.view_n = 1
            self.last = None
            self.acc = 0.0
            self.frames = 0
            sv, bv = tk.StringVar, tk.BooleanVar
            self.v_data = sv(value=find_data(pref("playtest_data", "")))
            self.v_lib = sv(value=pref("playtest_lib", ""))
            self.v_course = sv(value=COURSES[9][0])
            self.v_night = bv(value=False)
            self.v_manual = bv(value=False)
            self.v_traffic = bv(value=False)
            self.v_power = tk.IntVar(value=0)
            self.v_handling = tk.IntVar(value=0)
            self.status = UI.StatusLine("Open a car in Parts & viewport, pick the game's data folder and press Start.")
            self.hud = tk.StringVar(value="")
            self.build()
            host.register_module("playtest", self)
            root.bind_all("<KeyPress>", self.key_down, add="+")
            root.bind_all("<KeyRelease>", self.key_up, add="+")

        # ------------------------------------------------------------ layout
        def build(self):
            left = host.module_left("playtest")
            bar = host.module_bar("playtest")
            ws = self.ws = VL.Slot(left, host.module_right("playtest"), bar)
            ttk.Label(left, text="PLAYTEST", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
            ttk.Label(left, text="Drive the car open in Parts on the game's map, with the game's physics and camera.", wraplength=250,
                      justify="left", foreground="#8f9eb2").pack(anchor="w", pady=(2, 8))
            sec = ws.section("Game files")
            for lab, var, cmd in (("Data folder", self.v_data, self.pick_data), ("Physics library", self.v_lib, self.pick_lib)):
                row = ttk.Frame(sec.body)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=lab, width=14).pack(side="left")
                ttk.Entry(row, textvariable=var, width=18).pack(side="left", fill="x", expand=True)
                ttk.Button(row, text="…", width=3, command=cmd).pack(side="left", padx=(3, 0))
            self.lib_lbl = ttk.Label(sec.body, text="", wraplength=250, justify="left", foreground="#8f9eb2")
            self.lib_lbl.pack(anchor="w", pady=(4, 0))
            sec2 = ws.section("Race")
            for lab, var, vals in (("Course", self.v_course, [c[0] for c in COURSES]),):
                row = ttk.Frame(sec2.body)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=lab, width=14).pack(side="left")
                ttk.Combobox(row, textvariable=var, values=vals, state="readonly", width=24).pack(side="left", fill="x", expand=True)
            ttk.Checkbutton(sec2.body, text="Night", variable=self.v_night).pack(anchor="w")
            ttk.Checkbutton(sec2.body, text="Manual gearbox", variable=self.v_manual, command=self.manual_changed).pack(anchor="w")
            ttk.Checkbutton(sec2.body, text="Traffic (not drawn yet)", variable=self.v_traffic).pack(anchor="w")
            for lab, var in (("Power tune", self.v_power), ("Handling tune", self.v_handling)):
                row = ttk.Frame(sec2.body)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=lab, width=14).pack(side="left")
                ttk.Spinbox(row, from_=0, to=24, textvariable=var, width=5).pack(side="left")
            sec3 = ws.section("Keys")
            ttk.Label(sec3.body, justify="left", wraplength=300,
                      text="Up / W  gas\nDown / S / Space  brake\nLeft, Right / A, D  steer\nZ / X  gear down / up (manual)\nV  view: behind / roof\n"
                           "R  back on the road\nEsc  stop").pack(anchor="w")
            ttk.Button(sec3.body, text="Help", command=lambda: messagebox.showinfo("Playtest", HELP, parent=root)).pack(anchor="w", pady=(6, 0))
            # bar under the viewport
            b1 = ttk.Frame(bar)
            b1.pack(fill="x")
            self.b_start = ttk.Button(b1, text="Start", width=8, command=self.start)
            self.b_start.pack(side="left")
            self.b_stop = ttk.Button(b1, text="Stop", width=8, command=self.stop, state="disabled")
            self.b_stop.pack(side="left", padx=4)
            ttk.Button(b1, text="Put back on road (R)", command=self.reset).pack(side="left", padx=4)
            ttk.Button(b1, text="View (V)", command=self.next_view).pack(side="left", padx=4)
            ws.flow(b1, bar)
            ttk.Label(bar, textvariable=self.hud, font=("TkFixedFont", 10), foreground="#dbe5f1").pack(fill="x", pady=(4, 0), anchor="w")
            self.status.bar(bar, ttk, tk, wraplength=420, fill="x", pady=(3, 0))
            self.refresh_lib_label()

        # ------------------------------------------------------------ files
        def pick_data(self):
            d = filedialog.askdirectory(parent=root, title="The game's data folder (phys.img, carconfig, hakone.wpk ...)",
                                        initialdir=self.v_data.get() or None)
            if d:
                self.v_data.set(d)
                set_pref(playtest_data=d)

        def pick_lib(self):
            f = filedialog.askopenfilename(parent=root, title="The physics library", filetypes=[("Library", "*.dll *.dylib *.so"), ("All", "*.*")])
            if f:
                self.v_lib.set(f)
                set_pref(playtest_lib=f)
                self.core = None
                self.refresh_lib_label()

        def refresh_lib_label(self):
            p = find_library(self.v_lib.get().strip() or None)
            self.lib_lbl.configure(text=("Library: " + p) if p else "No physics library found (%s). See playtest_native/README.txt." % LIB_NAMES[0])

        # ------------------------------------------------------------ start / stop
        def start(self):
            if not host.pack:
                self.status.set("Open a car in Parts & viewport first.", "warning")
                return
            if self.gl is None:
                self.status.set("Playtest draws in the GPU viewport: switch it on in Parts & viewport (GPU viewport).", "warning")
                return
            data = self.v_data.get().strip()
            course = next(c for c in COURSES if c[0] == self.v_course.get())
            miss = check_data(data, course[2], self.v_night.get()) if data else ["the data folder"]
            if miss:
                self.status.set("The data folder is missing: " + ", ".join(miss), "warning")
                return
            set_pref(playtest_data=data)
            try:
                if self.core is None:
                    lib = find_library(self.v_lib.get().strip() or None)
                    if not lib:
                        raise PlayError("the physics library (%s) is not here: see playtest_native/README.txt" % LIB_NAMES[0])
                    self.core = PlayCore(lib)
                    self.core.init(data)
                info = self.core.car_info(pack_name_of(host.path))
                note = ""
                if info is None:
                    info = self.core.car_info("maz_FD3S")
                    note = " (this car is not one of the arcade's: it drives like the RX-7)"
                self.info = info
                self.core.start(course[1], info, self.v_manual.get(), self.v_power.get(), self.v_handling.get(), self.v_traffic.get())
                self.load_scene(data, course[2], self.v_night.get())
            except PlayError as ex:
                self.status.set(str(ex), "error")
                return
            except Exception as ex:
                import traceback
                host.log("error", "Playtest failed to start", traceback.format_exc())
                self.status.set("Playtest could not start: %s" % ex, "error")
                return
            self.running = True
            self.core.view(self.view_n)
            self.layer.state = self.core.read()
            self.last = time.perf_counter()
            self.acc = 0.0
            self.b_start.configure(state="disabled")
            self.b_stop.configure(state="normal")
            self.status.set("Driving the %s on %s.%s  Z / X shift, V view, R reset, Esc stop." % (pack_name_of(host.path), course[0], note), "success")
            self.attach()
            root.focus_set()
            root.after(5, self.tick)

        def load_scene(self, data, city, night):
            import stage_wpk
            import playtest_gl as PG
            mp, sp = map_files(data, city, night)
            key = (mp, sp)
            if key not in self.stage_cache:
                self.status.set("Loading the map ...")
                root.update_idletasks()
                self.stage_cache = {key: (stage_wpk.load(mp), stage_wpk.load(sp) if sp else None)}
            stage, sky = self.stage_cache[key]
            if self.layer is None:
                self.layer = PG.PlayLayer()
            self.layer.set_stage(stage, sky, city, night)
            hidden = set(getattr(host, "hidden", ()) or ())
            self.layer.set_car(PG.prepare_car(we.view_model(host.pack, host.stock), hidden))
            self.city, self.night = city, night
            self.PG = PG

        def attach(self):
            gl = self.gl
            if gl is None or self.layer is None:
                return
            if self.layer not in gl.extras:
                gl.extras.append(self.layer)
            far, near, fogc = self.PG.env_of(self.city, self.night)[:3]
            gl.set_background(fogc, fogc)
            gl.view_fn = self.make_view
            host.render(reuse=True)

        def detach(self):
            gl = self.gl
            if gl is not None:
                gl.view_fn = None
                if self.layer in gl.extras:
                    gl.extras.remove(self.layer)

        def make_view(self, size):
            st = self.layer.state
            if st is None:
                return None
            return self.PG.PlayView(st, size)

        def stop(self, msg="Stopped."):
            self.running = False
            self.b_start.configure(state="normal")
            self.b_stop.configure(state="disabled")
            self.detach()
            if self.layer is not None:
                self.layer.state = None
            self.hud.set("")
            if self.gl is not None:
                host.render(reuse=True)
            self.status.set(msg)

        def reset(self):
            if self.running:
                self.core.reset()

        def next_view(self):
            self.view_n = 0 if self.view_n else 1
            if self.core:
                self.core.view(self.view_n)

        def manual_changed(self):
            if self.running:
                self.core.manual(self.v_manual.get())

        # ------------------------------------------------------------ keys
        def _typing(self):
            try:
                return root.focus_get().winfo_class() in ("Entry", "TEntry", "TSpinbox", "Spinbox", "TCombobox", "Text")
            except Exception:
                return False

        def key_down(self, e):
            if not (self.running and self.active) or self._typing():
                return
            k = e.keysym.lower()
            job = self.release_jobs.pop(k, None)
            if job:
                root.after_cancel(job)
                return "break"
            if k in self.keys:
                return "break"
            self.keys.add(k)
            if k == "x":
                self.edge["up"] = 1
            elif k == "z":
                self.edge["down"] = 1
            elif k == "v":
                self.next_view()
            elif k == "r":
                self.reset()
            elif k == "escape":
                self.stop()
            return "break"

        def key_up(self, e):
            k = e.keysym.lower()
            if k not in self.keys:
                return
            # X11 repeats a held key as release + press pairs: only believe a release that is not followed by a press
            self.release_jobs[k] = root.after(40, lambda k=k: self._release(k))

        def _release(self, k):
            self.release_jobs.pop(k, None)
            self.keys.discard(k)

        def inputs(self):
            K = self.keys
            steer = (1.0 if ("right" in K or "d" in K) else 0.0) - (1.0 if ("left" in K or "a" in K) else 0.0)
            gas = 1.0 if ("up" in K or "w" in K) else 0.0
            brk = 1.0 if ("down" in K or "s" in K or "space" in K) else 0.0
            up, down = self.edge["up"], self.edge["down"]
            self.edge["up"] = self.edge["down"] = 0
            return steer, gas, brk, up, down

        # ------------------------------------------------------------ the loop
        def tick(self):
            if not self.running:
                return
            now = time.perf_counter()
            if not self.active:
                self.last = now
                root.after(50, self.tick)
                return
            self.acc += min(now - self.last, 0.1)
            self.last = now
            n = 0
            while self.acc >= 1.0 / 60.0 and n < 6:
                s, g, b, up, dn = self.inputs()
                self.core.step(s, g, b, up, dn)
                self.acc -= 1.0 / 60.0
                n += 1
            if n:
                st = self.core.read()
                if st is not None:
                    self.layer.state = st
                    self.layer.braking = st["brake"] > 0.5 or "down" in self.keys or "s" in self.keys
                    self.gl.request_draw()
                    self.frames += 1
                    if self.frames % 6 == 0:
                        self.update_hud(st)
            root.after(4, self.tick)

        def update_hud(self, st):
            if st["countdown"] > 0.05:
                t = "START %.0f" % (st["countdown"] + 0.5)
            else:
                t = "%d:%05.2f" % (int(st["total"]) // 60, st["total"] % 60)
            gear = "R" if st["gear"] < 0 else ("N" if st["gear"] == 0 else str(st["gear"]))
            self.hud.set("%3.0f km/h   gear %s   %5.0f rpm   time %s   left %5.1f s   to goal %4.1f km" %
                         (st["kmh"], gear, st["rpm"], t, st["time_left"], st["remain"] / 1000.0))

        # ------------------------------------------------------------ the module protocol (wpk_edit)
        def on_enter(self):
            self.active = True
            self.refresh_lib_label()
            if self.running:
                self.attach()

        def on_leave(self):
            self.active = False
            self.keys.clear()
            self.detach()

        def pack_changed(self, kind):
            if kind == "open" and self.running:
                self.stop("Stopped: a different car was opened. Press Start to drive it.")

        def shown_tags(self, base):
            return set() if self.running else set(base)             # the car is drawn by the playtest layer while it runs

        def selection_tags(self):
            return []

        def viewport_changed(self):
            pass

        def vp_press(self, e):
            pass

        vp_press_pan = vp_motion = vp_release = vp_press

        def vp_wheel(self, e):
            return "break"

        def fit_view(self):
            pass

    return Tab()


def selftest(data_dir, lib=None):
    lib = lib or find_library()
    core = PlayCore(lib)
    core.init(data_dir)
    info = core.car_info("nis_Z33")
    core.start(9, info)
    for f in range(600):
        core.step(0, 1.0 if f > 240 else 0.0, 0)
    st = core.read()
    print("kmh %.1f gear %d pos %s cam %s fov %.2f" % (st["kmh"], st["gear"], st["body"][12:15], st["cam_pos"], st["cam_fov"]))
    return st


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "selftest":
        selftest(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        print(__doc__)
