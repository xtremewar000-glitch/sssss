"""
Wheels: browse, preview and edit the wheel packs of a "wheels" folder (the 4th tab of the VPK tool).

What the packs are (from the Re3DX+ port, data/wheels):
    <name>_<set>.wpk   in-race wheel pack: part 0x100 = left wheel, part 0x101 = right wheel
                       (the right wheel is the left one turned 180 degrees about the up axis, so a custom wheel
                       only has to be made once: the right one is built from it)
    <model>_high.wpk   preview model for the GET screen: one part, tag 0

Axes: the wheel turns about X, +Y up, +Z front, 10 units = 1 m (a 0.5 m wheel is 5 units across).

Everything is done with wpk_edit.Pack, so a pack written here is byte-for-byte what the Models tab would write.
Command line:
    python vpk_tool.py wheels list  WHEELS_FOLDER
    python vpk_tool.py wheels swap  PACK.wpk MODEL.obj [OUT.wpk] [--axis x|y|z] [--scale N] [--rot 0|90|180|270]
                                    [--flip-faces] [--no-fit] [--side both|left|right] [--png TEXTURE.png] [--keep-shine]
"""
import copy
import glob
import os
import queue
import threading

import numpy as np

from PIL import Image
try:
    from PIL import ImageTk
except ImportError:                                       # pragma: no cover (no Tk in this Python)
    ImageTk = None

import wpk_edit
import vpk_layout as VL                   # Blender-style list | viewport | panels layout
from wpk_edit import EditError, Pack

try:
    import wpk_view
except ImportError:                                       # pragma: no cover
    wpk_view = None

try:
    import backfire_edit
except ImportError:                                       # pragma: no cover
    backfire_edit = None

try:
    import wpk_browser as WB
except ImportError:                                       # pragma: no cover
    WB = None
try:
    import gl_viewport as GLV
    GLV_WHY = GLV.import_error()
except Exception as _ex:                                  # no moderngl / OpenGL here: the software picture stays
    GLV, GLV_WHY = None, "gl_viewport.py could not be loaded (%s)" % _ex

LEFT, RIGHT = 0x100, 0x101
MIRROR = np.array([-1, 1, -1])


# ------------------------------------------------------------------------------------------------
# what a pack is
# ------------------------------------------------------------------------------------------------
import vpk_ui as UI                       # shared confirmations / look (Milestone 6)


def kind_of(pack):
    tags = {c.tag for c in pack.chunks}
    if LEFT in tags or RIGHT in tags:
        return "Race wheel"
    if 0 in tags:
        return "Preview (_high)"
    return "Other"


def main_tags(pack):
    """the part groups that make the wheel (not the shadow)"""
    return [c.tag for c in pack.chunks if c.tag != wpk_edit.WPK_SHADOW]


def info(path):
    pk = Pack.load(path)
    tr = sum(c.counts()[2] for c in pk.chunks)
    return dict(name=os.path.basename(path), path=path, size=os.path.getsize(path), kind=kind_of(pk), tris=tr,
                tex=len(pk.tex), parts=",".join("0x%x" % t for t in main_tags(pk)))


# ------------------------------------------------------------------------------------------------
# editing (no GUI in here)
# ------------------------------------------------------------------------------------------------
def mirror_batches(batches):
    """the right wheel from the left one: turn 180 degrees about Y (x and z change sign; winding and UVs stay)"""
    out = []
    for b in batches:
        c = b.copy()
        c.V["pos"] = c.V["pos"] * MIRROR.astype(np.float32)
        c.V["normal"] = c.V["normal"] * MIRROR.astype(np.int16)
        out.append(c)
    return out


def _axle_to_x(meshes, axis):
    """turn the model so its axle runs along X. axis None = the thinnest direction (a wheel is a short cylinder)"""
    if axis is None:
        P = np.concatenate([m.V["pos"] for m in meshes]).astype(np.float64)
        axis = int(np.argmin(P.max(0) - P.min(0)))
    if axis == 0:
        return 0
    R = {1: np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], np.float64),
         2: np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], np.float64)}[axis]
    for m in meshes:
        m.V["pos"] = (m.V["pos"].astype(np.float64) @ R.T).astype(np.float32)
        n = (m.V["normal"].astype(np.float64) @ R.T)
        m.V["normal"] = np.clip(np.round(n), -32767, 32767).astype(np.int16)
    return axis


def _place(meshes, old, inner, fit):
    """scale to the old wheel's diameter (fit) and put the hub where the old one's was.
    inner: 'min' (the inner face of a left wheel is at its lowest x), 'max' (right wheel) or 'center'"""
    P = np.concatenate([m.V["pos"] for m in meshes]).astype(np.float64)
    lo, hi = P.min(0), P.max(0)
    f = 1.0
    if old is not None and old.batches:
        olo, ohi = np.array(old.lo, np.float64), np.array(old.hi, np.float64)
        if fit:
            nd = max(hi[1] - lo[1], hi[2] - lo[2])
            if nd > 1e-9:
                f = max(ohi[1] - olo[1], ohi[2] - olo[2]) / nd
        lo2, hi2 = lo * f, hi * f
        sh = np.zeros(3)
        for k in (1, 2):
            sh[k] = (olo[k] + ohi[k]) / 2 - (lo2[k] + hi2[k]) / 2
        sh[0] = {"min": olo[0] - lo2[0], "max": ohi[0] - hi2[0], "center": (olo[0] + ohi[0]) / 2 - (lo2[0] + hi2[0]) / 2}[inner]
    else:
        sh = np.array([0.0, -(lo[1] + hi[1]) / 2, -(lo[2] + hi[2]) / 2])
    for m in meshes:
        m.V["pos"] = (m.V["pos"].astype(np.float64) * f + sh).astype(np.float32)
    return f


def _mtl_textures(objpath):
    """{material: picture path} from the OBJ's .mtl (map_Kd lines); {} when there is none"""
    import re
    mtl = None
    try:
        with open(objpath, "r", encoding="utf-8", errors="replace") as fh:
            for _ in range(200):
                ln = fh.readline()
                if not ln:
                    break
                if ln.startswith("mtllib"):
                    mtl = ln.split(None, 1)[1].strip()
                    break
    except OSError:
        return {}
    if not mtl:
        return {}
    mp = os.path.join(os.path.dirname(objpath), mtl)
    out, cur = {}, None
    try:
        with open(mp, "r", encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                t = ln.strip().split(None, 1)
                if len(t) < 2:
                    continue
                if t[0] == "newmtl":
                    cur = re.sub(r"\.\d{3}$", "", t[1].strip())
                elif t[0].lower() == "map_kd" and cur:
                    f = t[1].strip().split()[-1].replace("\\", "/")
                    f = os.path.join(os.path.dirname(mp), f)
                    if os.path.isfile(f):
                        out[cur] = f
    except OSError:
        return {}
    return out


def swap_wheel(pack, objpath, scale=10.0, rot=0, flip_faces=False, flip_v=True, axis=None, fit=True,
               side="both", tex=None, png=None, use_mtl=True, keep_shine=False):
    """replace the wheel with the model in an OBJ. side: 'both' (right built from left), 'left', 'right' (race packs only).
    tex: a texture slot of the pack to use for every material of the OBJ (None = by material name).
    use_mtl: pictures the OBJ's .mtl names (map_Kd) for materials the pack has no texture for are added to the pack.
    keep_shine: new pictures keep the old wheel's shader, env and gloss map (the gloss map follows the OLD UV layout, so it
    looks wrong on a new model); default: plain textured, like the stock wheels that have no shine (prog 0, no env / gloss map).
    png: a picture to add to the pack as a new texture and use for the whole wheel (wins over tex).
    -> dict(tags, tris, missing, scale, axis)"""
    if png:
        tex = pack.add_texture("custom_wheel.tga", Image.open(png), max_size=512)
    meshes = wpk_edit.parse_obj(objpath, scale=scale, rot=rot, flip_v=flip_v, flip_faces=flip_faces)
    race = pack.chunk(LEFT) is not None or pack.chunk(RIGHT) is not None
    if race:
        target = RIGHT if side == "right" else LEFT
        inner = "max" if target == RIGHT else "min"
    else:
        mt = main_tags(pack)
        if not mt:
            raise EditError("This pack has no wheel part to replace.")
        target = 0 if 0 in mt else mt[0]
        inner = "center"
    for m in meshes:
        m.tag = target
    old = pack.chunk(target)
    look = wpk_edit.dominant_material(pack, old)
    ax = _axle_to_x(meshes, axis)
    f = _place(meshes, old, inner, fit)
    texmap = {m.material: tex for m in meshes} if tex is not None else None
    restyle = {tex} if tex is not None else set()
    if tex is None and use_mtl:
        pics = _mtl_textures(objpath)
        texmap = {}
        for m in meshes:
            if m.material in texmap or m.material not in pics:
                continue
            stem = wpk_edit.split_material(m.material)[0]
            if pack.tex_index(stem) is not None:
                continue                                         # the pack has it: by name
            i = pack.add_texture(os.path.splitext(os.path.basename(pics[m.material]))[0][:43] + ".tga", Image.open(pics[m.material]), max_size=512)
            texmap[m.material] = i
            restyle.add(i)
        texmap = texmap or None
    res = wpk_edit.replace_parts(pack, meshes, default_tag=target, texmap=texmap)
    if restyle:                                               # new pictures
        for b in pack.chunk(target).batches:
            if b.tex in restyle:
                if keep_shine and look is not None:
                    b.flags, b.prog, b.env, b.spec, b.power = look[1:]
                else:                                         # plain lit texture: the look of the stock wheels without shine
                    b.flags, b.prog, b.env, b.spec, b.power = 0, 0, 0xFFFF, 0xFFFF, 0
    tags = [target]
    if race and side == "both":
        pack.set_chunk(RIGHT, mirror_batches(pack.chunk(LEFT).batches))
        tags = [LEFT, RIGHT]
    tris, missing = res[target]
    return dict(tags=tags, tris=tris, missing=missing, scale=f, axis=ax)


def tex_image(pack, i):
    return Image.fromarray(pack.decode_tex(i), "RGBA")


def set_tex_image(pack, i, im):
    """new picture for texture i, keeping its size and format"""
    t = pack.tex[i]
    if not t["raw"]:
        raise EditError("%s is borrowed from the stock pack and has no picture of its own here." % t["name"])
    if backfire_edit is None:
        raise EditError("backfire_edit.py is missing next to this file.")
    t["raw"] = backfire_edit.encode_tex(im, t["w"], t["h"], t["fmt"])


def recolour(im, hue=0.0, sat=1.0, val=1.0, swap_rb=False):
    """hue in degrees, sat / val as factors; alpha untouched"""
    im = im.convert("RGBA")
    a = im.getchannel("A")
    rgb = im.convert("RGB")
    if swap_rb:
        r, g, b = rgb.split()
        rgb = Image.merge("RGB", (b, g, r))
    if hue or sat != 1.0:
        h, s, v = rgb.convert("HSV").split()
        sh = int(round(hue / 360.0 * 256))
        h = h.point(lambda x: (x + sh) % 256)
        s = s.point(lambda x: min(255, int(x * sat)))
        rgb = Image.merge("HSV", (h, s, v)).convert("RGB")
    if val != 1.0:
        rgb = rgb.point(lambda x: min(255, int(x * val)))
    rgb.putalpha(a)
    return rgb


def fill_image(pack, i, rgb):
    t = pack.tex[i]
    return Image.new("RGBA", (t["w"], t["h"]), tuple(rgb) + (255,))


def render(pack, which, view, w=520, h=340):
    """PIL image of the wheel. which: 'left' / 'right' / 'both'; view: index into wpk_view.VIEWS"""
    model = wpk_edit.view_model(pack, None)
    tags = {"left": {LEFT}, "right": {RIGHT}}.get(which)
    allc = [c for c in model[1] if c[8] != wpk_edit.WPK_SHADOW]
    vis = [c for c in allc if tags is None or c[8] in tags] or allc
    yaw, pit = wpk_view.VIEWS[view][1:]
    g = wpk_view.rasterize((model[0], vis, model[2]), yaw, pit, w, h, frame=allc)
    return wpk_view.compose(g, model[0])


# ------------------------------------------------------------------------------------------------
# command line
# ------------------------------------------------------------------------------------------------
def cli(argv):
    if not argv or argv[0] not in ("list", "swap"):
        print(__doc__)
        return 0
    if argv[0] == "list":
        for f in sorted(glob.glob(os.path.join(argv[1], "*.wpk"))):
            d = info(f)
            print("%-28s %-16s %8d tris %2d tex  parts %s" % (d["name"], d["kind"], d["tris"], d["tex"], d["parts"]))
        return 0
    pos, opt, i = [], {}, 1
    while i < len(argv):
        a = argv[i]
        if a in ("--flip-faces", "--no-fit", "--keep-shine"):
            opt[a] = True
        elif a in ("--axis", "--scale", "--rot", "--side", "--png"):
            opt[a] = argv[i + 1]
            i += 1
        else:
            pos.append(a)
        i += 1
    if len(pos) < 2:
        print(__doc__)
        return 1
    pk = Pack.load(pos[0])
    r = swap_wheel(pk, pos[1], scale=float(opt.get("--scale", 10)), rot=int(opt.get("--rot", 0)),
                   flip_faces="--flip-faces" in opt, fit="--no-fit" not in opt, side=opt.get("--side", "both"),
                   axis={"x": 0, "y": 1, "z": 2}.get(opt.get("--axis", "").lower()), png=opt.get("--png"), keep_shine="--keep-shine" in opt)
    out = pos[2] if len(pos) > 2 else pos[0]
    pk.save(out)
    print("written %s: parts %s, %d triangles, scale x%.3f, axle %s" % (out, ", ".join("0x%x" % t for t in r["tags"]), r["tris"],
                                                                         r["scale"], "XYZ"[r["axis"]]))
    if r["missing"]:
        print("materials the pack does not know (given the old wheel's look): " + ", ".join(r["missing"]))
    return 0


# ------------------------------------------------------------------------------------------------
# the tab
# ------------------------------------------------------------------------------------------------
def build_tab(tab_parent, root, app=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk, colorchooser

    parent = tab_parent                     # no page scrolling: the panels scroll themselves (vpk_layout)
    prefs = wpk_view.load_prefs() if wpk_view else {}

    class Tab:
        def __init__(self):
            self.folder = prefs.get("wheels_dir", "")
            self.rows = []                     # info dicts of the folder
            self.pack = None
            self.path = None
            self.undo = []
            self.dirty = False
            self.gen = 0
            self.q = queue.Queue()
            self.photo = None
            self.thumb = None
            self.filter = tk.StringVar()
            self.which = tk.StringVar(value="Left wheel (0x100)")
            self.view = tk.IntVar(value=1)
            self.status = UI.StatusLine("Choose the wheels folder.")
            self.gl = self.cam = None              # the GPU viewport (see build_gl); None = the software picture
            self.model_dirty = True                # the pack changed: the GPU copy is stale
            self._drag = None
            self.build()
            parent.after(80, self.pump)
            if self.folder and os.path.isdir(self.folder):
                self.scan()

        # ---------------------------------------------------------------- layout
        def build(self):
            # Blender-style: toolbar on top, save bar + status at the bottom, between them list | viewport | panels (vpk_layout)
            top = ttk.Frame(parent, padding=(12, 8, 12, 2))
            top.pack(fill="x")
            ttk.Button(top, text="Choose folder…", command=self.choose).pack(side="left")
            ttk.Button(top, text="Open one .wpk…", command=self.open_one).pack(side="left", padx=6)
            self.b_undo = ttk.Button(top, text="Undo", command=self.do_undo)
            self.b_undo.pack(side="left")
            self.b_save = ttk.Button(top, text="Save", command=self.save)
            self.b_save.pack(side="left", padx=6)
            ttk.Button(top, text="Save as…", command=self.save_as).pack(side="left")
            ttk.Button(top, text="Help", command=self.help).pack(side="left", padx=6)
            fl = ttk.Frame(parent, padding=(12, 0, 12, 4))
            fl.pack(fill="x")
            ttk.Label(fl, text="Wheels folder:").pack(side="left")
            self.folder_lbl = ttk.Label(fl, text=self.folder or "(none)", foreground="#8f9eb2")
            self.folder_lbl.pack(side="left", padx=6)
            bot = ttk.Frame(parent, padding=(12, 4, 12, 6))
            bot.pack(fill="x", side="bottom")
            self.status.bar(bot, ttk, tk, wraplength=900, side="left", fill="x", expand=True)
            ttk.Separator(parent).pack(fill="x", side="bottom")
            ws = self.ws = VL.Workspace(parent, left_w=390, right_w=400)

            left = ws.left
            fr = ttk.Frame(left)
            fr.pack(fill="x")
            ttk.Label(fr, text="Filter:").pack(side="left")
            e = ttk.Entry(fr, textvariable=self.filter, width=22)
            e.pack(side="left", padx=4, fill="x", expand=True)
            self.filter.trace_add("write", lambda *_: self.fill())
            lf = ttk.Frame(left)
            lf.pack(fill="both", expand=True, pady=4)
            cols = ("kind", "tris", "tex")
            self.tree = ttk.Treeview(lf, columns=cols, height=16, selectmode="browse")
            self.tree.heading("#0", text="Pack")
            self.tree.heading("kind", text="Kind")
            self.tree.heading("tris", text="Tris")
            self.tree.heading("tex", text="Tex")
            self.tree.column("#0", width=150, stretch=True)
            self.tree.column("kind", width=90, stretch=False)
            self.tree.column("tris", width=50, anchor="e", stretch=False)
            self.tree.column("tex", width=34, anchor="e", stretch=False)
            sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())

            right = ws.center
            self.canvas = tk.Canvas(right, width=520, height=340, background="#1a2635", highlightthickness=0)
            self.canvas.pack(anchor="nw")
            bar = ttk.Frame(right)
            bar.pack(fill="x", side="bottom", pady=(4, 0))
            ttk.Label(bar, text="View:").pack(side="left")
            names = [v[0] for v in wpk_view.VIEWS] if wpk_view else ["Side"]
            self.view_cb = ttk.Combobox(bar, values=names, state="readonly", width=12)
            self.view_cb.current(min(1, len(names) - 1))
            self.view_cb.pack(side="left", padx=4)
            self.view_cb.bind("<<ComboboxSelected>>", lambda _e: self.on_view())
            ttk.Label(bar, text="Wheel:").pack(side="left", padx=(10, 0))
            self.which_cb = ttk.Combobox(bar, textvariable=self.which, state="readonly", width=20,
                                         values=["Left wheel (0x100)", "Right wheel (0x101)", "Both"])
            self.which_cb.pack(side="left", padx=4)
            self.which_cb.bind("<<ComboboxSelected>>", lambda _e: self.draw())
            ws.flow(bar, right)
            self.warn = ttk.Label(right, text="", foreground="#f85149", wraplength=400)
            self.warn.pack(fill="x", side="bottom")
            self.gl_info = ttk.Label(right, text="", foreground="#8f9eb2", wraplength=400, justify="left")
            self.gl_info.pack(fill="x", side="bottom")
            self.bar = bar
            self.build_gl(right)

            tx = ws.section("Textures and colours").body
            self.tlist = tk.Listbox(tx, height=5, exportselection=False)
            self.tlist.pack(fill="x")
            self.tlist.bind("<<ListboxSelect>>", lambda _e: self.show_thumb())
            row = ttk.Frame(tx)
            row.pack(fill="x", pady=(6, 0))
            self.thumb_cv = tk.Canvas(row, width=96, height=96, background="#141b26", highlightthickness=0)
            self.thumb_cv.pack(side="left", padx=(0, 8))
            bt = ttk.Frame(row)
            bt.pack(side="left", fill="x", expand=True)
            bt.columnconfigure(0, weight=1)
            for i, (txt, fn) in enumerate([("Export PNG…", self.tex_export), ("Replace from PNG…", self.tex_replace),
                                           ("Swap red / blue", self.tex_swap), ("Colour…", self.tex_colour),
                                           ("Fill with one colour…", self.tex_fill)]):
                ttk.Button(bt, text=txt, command=fn).grid(row=i, column=0, sticky="ew", padx=2, pady=1)

            cw = ws.section("Custom wheel").body
            ttk.Button(cw, text="Export wheel as OBJ…", command=self.export_obj).pack(fill="x", pady=2)
            ttk.Button(cw, text="Replace with an OBJ…", command=self.replace_obj).pack(fill="x", pady=2)
            self.set_buttons()

        def help(self):
            messagebox.showinfo("Wheels", (
                "Choose the wheels folder (the data/wheels one of the game).\n\n"
                "<name>_<n>.wpk  = in-race wheels. Part 0x100 is the left wheel, 0x101 the right one. "
                "<model>_high.wpk = the preview model of the GET screen.\n\n"
                "Replace with an OBJ: the model is turned so its axle runs along X, scaled to the old wheel's "
                "size and put on the old hub. For a race pack the right wheel is built from the left one. "
                "Outer face the wrong way round? Try Rotate 180. Looks inside-out? tick Flip faces.\n\n"
                "Textures: Replace keeps the size and format of the old picture; Colour changes hue / saturation / brightness.\n"
                "Save keeps the original once as <file>.bak."), parent=root)

        # ---------------------------------------------------------------- folder
        def choose(self):
            d = filedialog.askdirectory(title="Wheels folder", initialdir=self.folder or None, parent=root)
            if d:
                self.folder = d
                if wpk_view:
                    wpk_view.save_prefs(wheels_dir=d)
                self.scan()

        def scan(self):
            self.folder_lbl.configure(text=self.folder)
            files = sorted(glob.glob(os.path.join(self.folder, "*.wpk")))
            self.rows = []
            self.fill()
            self.status.set("Reading %d packs…" % len(files))
            self.scan_gen = getattr(self, "scan_gen", 0) + 1
            sg = self.scan_gen

            def work():
                for f in files:
                    try:
                        self.q.put(("row", sg, info(f)))
                    except Exception:
                        self.q.put(("row", sg, dict(name=os.path.basename(f), path=f, size=0, kind="(cannot read)", tris=0, tex=0, parts="")))
                self.q.put(("scanned", sg, len(files)))
            threading.Thread(target=work, daemon=True).start()

        def fill(self):
            self.tree.delete(*self.tree.get_children())
            f = self.filter.get().strip().lower()
            for r in self.rows:
                if f in r["name"].lower() or f in r["kind"].lower():
                    self.tree.insert("", "end", iid=r["path"], text=r["name"], values=(r["kind"], r["tris"], r["tex"]))

        def open_one(self):
            f = filedialog.askopenfilename(title="Wheel pack", filetypes=[("WPK", "*.wpk"), ("All", "*.*")], parent=root)
            if f:
                self.load(f)

        def on_select(self):
            s = self.tree.selection()
            if s and s[0] != self.path:
                if not self.confirm_discard():
                    self.tree.selection_set(self.path) if self.path and self.tree.exists(self.path) else None
                    return
                self.load(s[0])

        def confirm_discard(self):
            return UI.discard_changes(messagebox, root, os.path.basename(self.path or "") or "this wheel pack", self.dirty)

        def load(self, f):
            try:
                self.pack = Pack.load(f)
            except Exception as ex:
                messagebox.showerror("Wheels", "Cannot open %s:\n%s" % (os.path.basename(f), ex), parent=root)
                return
            self.path, self.undo, self.dirty = f, [], False
            race = kind_of(self.pack) == "Race wheel"
            self.which_cb.configure(state="readonly" if race else "disabled")
            self.which.set("Left wheel (0x100)")
            self.refresh()

        # ---------------------------------------------------------------- state
        def refresh(self):
            self.model_dirty = True
            self.tlist.delete(0, "end")
            for i, t in enumerate(self.pack.tex):
                self.tlist.insert("end", "%d  %s   %dx%d  fmt %d" % (i, t["name"], t["w"], t["h"], t["fmt"]))
            if self.pack.tex:
                self.tlist.selection_set(0)
            self.show_thumb()
            self.draw()
            self.set_buttons()
            self.say()

        def say(self, extra=""):
            if self.pack:
                tr = sum(c.counts()[2] for c in self.pack.chunks)
                self.status.set("%s%s — %s, %d triangles%s" % (os.path.basename(self.path or ""), " *" if self.dirty else "",
                                                               kind_of(self.pack), tr, ("  |  " + extra) if extra else ""))

        def set_buttons(self):
            self.b_undo.configure(state="normal" if self.undo else "disabled")
            self.b_save.configure(state="normal" if self.pack and self.dirty else "disabled")

        def snap(self):
            self.undo.append(copy.deepcopy(self.pack))
            del self.undo[:-8]

        def changed(self, msg=""):
            self.dirty = True
            self.refresh()
            self.say(msg)

        def do_undo(self):
            if self.undo:
                self.pack = self.undo.pop()
                self.dirty = bool(self.undo)
                self.refresh()
                self.say("undone")

        # ---------------------------------------------------------------- drawing
        # ---- the GPU viewport
        def pref(self, k):
            try:
                return wpk_view.load_prefs().get(k) if wpk_view else None
            except Exception:
                return None

        def build_gl(self, right):
            import os as _os
            want = (GLV is not None and not GLV_WHY and WB is not None and wpk_view is not None
                    and _os.environ.get("VPK_VIEWPORT", "gl").lower() != "soft" and self.pref("gpu_viewport") is not False)
            if not want:
                self.gl_info.configure(text="Software picture (%s). The GPU viewport with the free 360 orbit needs moderngl + OpenGL 3.3." % (
                    GLV_WHY or ("switched off" if self.pref("gpu_viewport") is False else "not available")))
                return
            try:
                v = wpk_view.VIEWS[min(1, len(wpk_view.VIEWS) - 1)]
                self.cam = WB.Camera(*v[1:])
                self.gl = GLV.create_viewport(right, self.cam, self.gl_failed, width=400, height=300, on_draw=self.gl_drawn)
            except Exception:
                self.gl = self.cam = None
                return
            w = self.gl.widget
            self.canvas.pack_forget()
            w.pack(before=self.bar, fill="both", expand=True)
            for ev, fn in (("<ButtonPress-1>", self.vp_press), ("<B1-Motion>", self.vp_motion), ("<ButtonRelease-1>", self.vp_release),
                           ("<MouseWheel>", self.vp_wheel), ("<Button-4>", self.vp_wheel), ("<Button-5>", self.vp_wheel),
                           ("<Double-Button-1>", lambda _e: self.fit_view())):
                w.bind(ev, fn)
            for btn in (2, 3):
                w.bind("<ButtonPress-%d>" % btn, self.vp_press_pan)
                w.bind("<B%d-Motion>" % btn, self.vp_motion)
                w.bind("<ButtonRelease-%d>" % btn, self.vp_release)
            w.bind("<Shift-ButtonPress-1>", self.vp_press_pan)
            self.gl_info.configure(text="Drag = orbit all the way round, right-drag or Shift-drag = move, wheel = zoom, double-click = fit.")

        def gl_failed(self, reason, detail=""):
            gl, self.gl = self.gl, None
            if gl is not None:
                try:
                    gl.widget.pack_forget()
                    gl.widget.destroy()
                except Exception:
                    pass
            self.canvas.pack(before=self.bar)
            self.gl_info.configure(text="Software picture (%s: %s)" % (reason, (detail.strip().splitlines() or [""])[-1]))
            self.draw()

        def gl_drawn(self, gl):
            if gl.info and "GPU:" not in self.gl_info.cget("text"):
                self.gl_info.configure(text="GPU: %s.  Drag = orbit 360, right-drag = move, wheel = zoom, double-click = fit." % gl.info)

        def on_view(self):
            """the View box: turn the camera to that angle (the GPU picture), or just draw again (the software one)"""
            if self.gl is not None and wpk_view:
                v = wpk_view.VIEWS[max(self.view_cb.current(), 0)]
                self.cam.set_angles(*v[1:])
                self.cam.fit()
            self.draw()

        def fit_view(self):
            if self.cam is not None:
                self.cam.fit()
                self.draw()

        def shown_tags(self):
            tags = [c.tag for c in self.pack.chunks if c.tag != wpk_edit.WPK_SHADOW]
            which = self.which.get()
            if kind_of(self.pack) == "Race wheel" and which != "Both":
                one = {LEFT} if which.startswith("Left") else {RIGHT}
                return [t for t in tags if t in one] or tags
            return tags

        def draw_gl(self):
            gl = self.gl
            try:
                if self.model_dirty:
                    self._model = wpk_edit.view_model(self.pack, None)
                    ao = os.path.splitext(self.path)[0] + ".ao" if self.path else None
                    gl.set_model(self._model, ao)
                    self.model_dirty = False
                tags = set(self.shown_tags())
                gl.set_visible(tags)
                allc = [c for c in self._model[1] if c[8] != wpk_edit.WPK_SHADOW]
                vis = [c for c in allc if c[8] in tags] or allc
                if vis:
                    lo = np.min([c[0:3] for c in vis], 0)
                    hi = np.max([c[3:6] for c in vis], 0)
                    gl.set_focus((lo + hi) / 2, max(float(np.linalg.norm(hi - lo)) / 2, 1.0))
                else:
                    gl.set_focus(None)
                self.warn.configure(text="")
                gl.request_draw()
            except Exception as ex:
                self.warn.configure(text="Cannot draw the wheel: %s" % ex)

        def vp_press(self, e):
            self._drag = dict(x=e.x, y=e.y, x0=e.x, y0=e.y, moved=False, btn=1)

        def vp_press_pan(self, e):
            self._drag = dict(x=e.x, y=e.y, x0=e.x, y0=e.y, moved=False, btn=2)

        def vp_motion(self, e):
            d = self._drag
            if not d or self.gl is None:
                return
            if d["btn"] == 1:
                if not d["moved"] and abs(e.x - d["x0"]) + abs(e.y - d["y0"]) < 4:
                    return
                d["moved"] = True
                self.cam.orbit(e.x - d["x"], e.y - d["y"])           # nothing stops the orbit: a full turn on both axes
            else:
                d["moved"] = True
                self.cam.pan_px(e.x - d["x"], e.y - d["y"], self.gl.upp)
            d["x"], d["y"] = e.x, e.y
            self.gl.request_draw()

        def vp_release(self, e):
            self._drag = None

        def vp_wheel(self, e):
            if self.gl is not None:
                up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
                self.cam.zoom_by(1.2 if up else 1 / 1.2)
                self.gl.request_draw()
            return "break"                                           # the page's scroll bar must not move with the wheel

        def draw(self):
            if not (wpk_view and self.pack):
                return
            if self.gl is not None:
                self.draw_gl()
                return
            self.gen += 1
            gen = self.gen
            which = {0: "left", 1: "right", 2: "both"}.get(
                ["Left wheel (0x100)", "Right wheel (0x101)", "Both"].index(self.which.get()), "left")
            view = max(self.view_cb.current(), 0)
            pk = self.pack
            w, h = 520, 340

            def work():
                try:
                    im = render(pk, which, view, w, h)
                except Exception as ex:
                    self.q.put(("err", gen, str(ex)))
                    return
                self.q.put(("img", gen, im))
            threading.Thread(target=work, daemon=True).start()

        def pump(self):
            try:
                while True:
                    kind, gen, val = self.q.get_nowait()
                    if kind == "row":
                        if gen == getattr(self, "scan_gen", 0):
                            self.rows.append(val)
                            f = self.filter.get().strip().lower()
                            if f in val["name"].lower() or f in val["kind"].lower():
                                self.tree.insert("", "end", iid=val["path"], text=val["name"], values=(val["kind"], val["tris"], val["tex"]))
                    elif kind == "scanned":
                        if gen == getattr(self, "scan_gen", 0):
                            self.status.set("%d packs in the folder. Pick one." % val if val else "No .wpk packs in this folder.", "info" if val else "warning")
                    elif gen == self.gen:
                        if kind == "img":
                            self.photo = ImageTk.PhotoImage(val)
                            self.canvas.delete("all")
                            self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
                            self.warn.configure(text="")
                        else:
                            self.warn.configure(text="Cannot draw the wheel: %s" % val)
            except queue.Empty:
                pass
            if parent.winfo_exists():
                parent.after(80, self.pump)

        def sel_tex(self):
            s = self.tlist.curselection()
            if not (self.pack and s):
                messagebox.showinfo("Wheels", "Pick a texture in the list first.", parent=root)
                return None
            return int(s[0])

        def show_thumb(self):
            self.thumb_cv.delete("all")
            s = self.tlist.curselection()
            if not (self.pack and s):
                return
            try:
                im = tex_image(self.pack, int(s[0]))
                im.thumbnail((96, 96))
                bg = Image.new("RGBA", im.size, (68, 68, 68, 255))
                bg.alpha_composite(im)
                self.thumb = ImageTk.PhotoImage(bg)
                self.thumb_cv.create_image(48, 48, image=self.thumb)
            except Exception:
                pass

        # ---------------------------------------------------------------- textures
        def tex_export(self):
            i = self.sel_tex()
            if i is None:
                return
            n = os.path.splitext(self.pack.tex[i]["name"])[0] + ".png"
            f = filedialog.asksaveasfilename(title="Export texture", initialfile=n, defaultextension=".png", parent=root)
            if f:
                tex_image(self.pack, i).save(f)
                self.say("exported %s" % os.path.basename(f))

        def tex_replace(self):
            i = self.sel_tex()
            if i is None:
                return
            f = filedialog.askopenfilename(title="New picture", filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tga"), ("All", "*.*")], parent=root)
            if not f:
                return
            self.edit_tex(i, lambda: set_tex_image(self.pack, i, Image.open(f)), "texture replaced")

        def edit_tex(self, i, fn, msg):
            self.snap()
            try:
                fn()
            except Exception as ex:
                self.undo.pop()
                messagebox.showerror("Wheels", str(ex), parent=root)
                return
            self.changed(msg)
            self.tlist.selection_set(i)
            self.show_thumb()

        def tex_swap(self):
            i = self.sel_tex()
            if i is not None:
                self.edit_tex(i, lambda: set_tex_image(self.pack, i, recolour(tex_image(self.pack, i), swap_rb=True)), "red and blue swapped")

        def tex_fill(self):
            i = self.sel_tex()
            if i is None:
                return
            c = colorchooser.askcolor(parent=root, title="Colour")
            if c and c[0]:
                self.edit_tex(i, lambda: set_tex_image(self.pack, i, fill_image(self.pack, i, [int(x) for x in c[0]])), "texture filled")

        def tex_colour(self):
            i = self.sel_tex()
            if i is None:
                return
            d = tk.Toplevel(root)
            d.title("Colour")
            d.transient(root)
            v = dict(h=tk.DoubleVar(value=0), s=tk.DoubleVar(value=100), b=tk.DoubleVar(value=100), r=tk.BooleanVar(value=False))
            for r, (txt, key, lo, hi) in enumerate([("Hue (degrees)", "h", -180, 180), ("Saturation %", "s", 0, 200), ("Brightness %", "b", 20, 200)]):
                ttk.Label(d, text=txt).grid(row=r, column=0, sticky="w", padx=8, pady=4)
                ttk.Scale(d, from_=lo, to=hi, variable=v[key], length=240).grid(row=r, column=1, padx=8)
                ttk.Label(d, textvariable=v[key], width=5).grid(row=r, column=2)
            ttk.Checkbutton(d, text="Swap red / blue first", variable=v["r"]).grid(row=3, column=0, columnspan=3, sticky="w", padx=8)

            def go():
                p = (v["h"].get(), v["s"].get() / 100.0, v["b"].get() / 100.0, v["r"].get())
                d.destroy()
                self.edit_tex(i, lambda: set_tex_image(self.pack, i, recolour(tex_image(self.pack, i), p[0], p[1], p[2], p[3])), "colour changed")
            ttk.Button(d, text="Apply", command=go).grid(row=4, column=1, pady=8, sticky="e")
            ttk.Button(d, text="Cancel", command=d.destroy).grid(row=4, column=2, padx=8)

        # ---------------------------------------------------------------- model
        def export_obj(self):
            if not self.pack:
                return
            f = filedialog.asksaveasfilename(title="Export wheel", initialfile=os.path.splitext(os.path.basename(self.path))[0] + ".obj",
                                             defaultextension=".obj", parent=root)
            if not f:
                return
            try:
                tags = main_tags(self.pack)
                tags = [LEFT] if LEFT in tags else tags
                r = wpk_edit.export_obj(self.pack, f, tags=tags, scale=0.1)
            except Exception as ex:
                messagebox.showerror("Wheels", str(ex), parent=root)
                return
            self.say("exported %d triangles to %s" % (r["triangles"], os.path.basename(f)))

        def replace_obj(self):
            if not self.pack:
                return
            f = filedialog.askopenfilename(title="Wheel model", filetypes=[("OBJ", "*.obj"), ("All", "*.*")], parent=root)
            if f:
                self.obj_dialog(f)

        def obj_dialog(self, objpath):
            d = tk.Toplevel(root)
            d.title("Replace the wheel")
            d.transient(root)
            race = kind_of(self.pack) == "Race wheel"
            v = dict(axis=tk.StringVar(value="Auto"), rot=tk.StringVar(value="0"), flip=tk.BooleanVar(value=False),
                     fit=tk.BooleanVar(value=True), scale=tk.StringVar(value="10"), side=tk.StringVar(value="Both (right built from left)"),
                     tex=tk.StringVar(value="(by material name in the OBJ)"), png=tk.StringVar(value=""), shine=tk.BooleanVar(value=False))
            ttk.Label(d, text=os.path.basename(objpath), font=("", 10, "bold")).grid(row=0, column=0, columnspan=2, padx=8, pady=6, sticky="w")
            rows = [("Axle runs along", ttk.Combobox(d, textvariable=v["axis"], values=["Auto", "X", "Y", "Z"], state="readonly", width=8)),
                    ("Rotate (about the up axis)", ttk.Combobox(d, textvariable=v["rot"], values=["0", "90", "180", "270"], state="readonly", width=8))]
            if race:
                rows.append(("Replace", ttk.Combobox(d, textvariable=v["side"], state="readonly", width=30,
                                                     values=["Both (right built from left)", "Left only", "Right only"])))
            rows.append(("Texture", ttk.Combobox(d, textvariable=v["tex"], state="readonly", width=30,
                                                 values=["(by material name in the OBJ)"] + ["%d  %s" % (i, t["name"]) for i, t in enumerate(self.pack.tex)])))
            rows.append(("…or a new picture (PNG)", None))
            r = 1
            for txt, w in rows:
                ttk.Label(d, text=txt).grid(row=r, column=0, sticky="w", padx=8, pady=3)
                if w is None:
                    pf = ttk.Frame(d)
                    pf.grid(row=r, column=1, sticky="w", padx=8)
                    ttk.Entry(pf, textvariable=v["png"], width=22).pack(side="left")
                    ttk.Button(pf, text="…", width=3, command=lambda: v["png"].set(
                        filedialog.askopenfilename(title="Wheel texture", filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tga")], parent=d) or v["png"].get())
                    ).pack(side="left", padx=3)
                else:
                    w.grid(row=r, column=1, sticky="w", padx=8)
                r += 1
            ttk.Checkbutton(d, text="Fit to the old wheel's size and hub position", variable=v["fit"]).grid(row=r, column=0, columnspan=2, sticky="w", padx=8)
            ttk.Label(d, text="…or the OBJ's scale (OBJ unit = 10 game units):").grid(row=r + 1, column=0, sticky="w", padx=8)
            ttk.Entry(d, textvariable=v["scale"], width=8).grid(row=r + 1, column=1, sticky="w", padx=8)
            ttk.Checkbutton(d, text="Flip faces (inside-out)", variable=v["flip"]).grid(row=r + 2, column=0, columnspan=2, sticky="w", padx=8)
            ttk.Checkbutton(d, text="New textures: keep the old wheel's shine (its gloss map fits the old UVs only)", variable=v["shine"]).grid(
                row=r + 3, column=0, columnspan=2, sticky="w", padx=8)

            def go():
                try:
                    sc = float(v["scale"].get())
                    tx = v["tex"].get()
                    tex = int(tx.split()[0]) if tx[:1].isdigit() else None
                    side = {"Left only": "left", "Right only": "right"}.get(v["side"].get(), "both")
                    ax = {"Auto": None, "X": 0, "Y": 1, "Z": 2}[v["axis"].get()]
                except ValueError:
                    messagebox.showerror("Wheels", "The scale must be a number.", parent=d)
                    return
                d.destroy()
                self.snap()
                try:
                    res = swap_wheel(self.pack, objpath, scale=sc, rot=int(v["rot"].get()), flip_faces=v["flip"].get(),
                                     axis=ax, fit=v["fit"].get(), side=side, tex=tex, png=v["png"].get() or None, keep_shine=v["shine"].get())
                except Exception as ex:
                    self.pack = self.undo.pop()                    # drop any half-done change
                    messagebox.showerror("Wheels", str(ex), parent=root)
                    return
                msg = "wheel replaced (%d triangles, x%.2f)" % (res["tris"], res["scale"])
                if res["missing"]:
                    msg += "; unknown materials got the old look"
                if race and res["tris"] > 4000:
                    messagebox.showwarning("Wheels", "This wheel has %d triangles. Stock in-race wheels have a few hundred to a few thousand, "
                                           "and the game draws four of them per car: a lighter model is safer on a phone." % res["tris"], parent=root)
                self.changed(msg)
            bb = ttk.Frame(d)
            bb.grid(row=r + 4, column=0, columnspan=2, pady=8, sticky="e")
            ttk.Button(bb, text="Replace", command=go).pack(side="left", padx=4)
            ttk.Button(bb, text="Cancel", command=d.destroy).pack(side="left", padx=8)

        # ---------------------------------------------------------------- save
        def save(self):
            self._save(self.path)

        def save_as(self):
            if not self.pack:
                return
            f = filedialog.asksaveasfilename(title="Save wheel pack", initialfile=os.path.basename(self.path or "wheel.wpk"),
                                             defaultextension=".wpk", parent=root)
            if f:
                self._save(f)

        def _save(self, f):
            if not (self.pack and f):
                return
            try:
                r = self.pack.save(f)
            except Exception as ex:
                messagebox.showerror("Wheels", "Not saved: %s" % ex, parent=root)
                return
            self.path, self.dirty = f, False
            self.set_buttons()
            self.say("saved%s" % (" (original kept as .bak)" if r["bak"] else ""))

    return Tab()
