"""Backfire flame editor for the VPK tool (the 3rd tab, "Backfire flame").

The game's exhaust flame is two files that sit together (shared by every car):
  backfire.wpk  (magic WPK2)  3 textures + 17 flame meshes (part tags 1..17)
  backfire.bfx  (magic BFX1)  17 flame animations (one 4x4 matrix per frame, 17 frames) + 760 OUTLETS: for every car body (key like
                              cars/maz_FD3S or cars/maz_FD3S:aero_b) where its MUFFLER1 outlets sit (MF_NULL_R1 R2 L1 L2: x y z in the car's space)
The game (src/backfire.c, app.c backfire_draw_car) draws a flame at every MUFFLER1 outlet of the car's key.  The outlets are the STOCK car's:
an imported or edited body whose exhaust is somewhere else gets its flames in the old place.  That is what "Exhaust outlets..." fixes.

What this file does: look at the flames, export / replace the 3 textures, change the width and length of the meshes, edit the animation
numbers, and move the outlets of any car (with a rear / side view of the car's body to line them up).  The .bfx keeps its size (only numbers
change); the .wpk is rewritten in the same layout (an unchanged file is written back byte for byte - see `selftest`).

NOT known / NOT checked: what each animation number does beyond "a 4x4 matrix per frame" (the diagonal grows from ~0 to ~2.5: the flame
growing), and loading an edited pair on the phone.  The game checks both files when it starts and drops the effect if either is invalid.

  python backfire_edit.py info     backfire.wpk [backfire.bfx]
  python backfire_edit.py selftest backfire.wpk [backfire.bfx]
  python backfire_edit.py outlets  backfire.bfx [CAR_KEY]                 list the outlets (e.g. cars/maz_FD3S)
  python backfire_edit.py setoutlet backfire.bfx CAR_KEY SLOT X Y Z [OUT.bfx]   move one MUFFLER1 outlet (SLOT = MF_NULL_R1 R2 L1 L2)
"""
import os
import queue
import re
import shutil
import struct
import sys
import threading

import numpy as np
import vpk_ui                      # shared status line / look
from PIL import Image

try:
    import wpk_edit as E
except ImportError:                                   # still usable for info / selftest
    E = None

VDT = np.dtype([("pos", "<f4", 3), ("uv", "<f4", 2), ("rgba", "u1", 4)])


class BackfireError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ pictures (16 bit, fixed format)
def decode_tex(t):
    w, h, fmt = t["w"], t["h"], t["fmt"]
    px = np.frombuffer(t["raw"], "<u2", count=w * h).reshape(h, w)
    if fmt == 0:
        r, g, b, a = (px >> 11) << 3, ((px >> 5) & 63) << 2, (px & 31) << 3, np.full(px.shape, 255)
    elif fmt == 2:
        r, g, b, a = (px >> 11) << 3, ((px >> 6) & 31) << 3, ((px >> 1) & 31) << 3, (px & 1) * 255
    else:
        r, g, b, a = (px >> 12) << 4, ((px >> 8) & 15) << 4, ((px >> 4) & 15) << 4, (px & 15) << 4
    return Image.fromarray(np.stack([r, g, b, a], -1).astype(np.uint8), "RGBA")


def encode_tex(im, w, h, fmt):
    """PIL image -> raw bytes in the picture's own size and format (0 = RGB565, 2 = RGBA5551, anything else = RGBA4444)"""
    im = im.convert("RGBA")
    if im.size != (w, h):
        im = im.resize((w, h), Image.LANCZOS)
    a = np.asarray(im, dtype=np.uint16)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    if fmt == 0:
        px = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    elif fmt == 2:
        px = ((r >> 3) << 11) | ((g >> 3) << 6) | ((b >> 3) << 1) | (al >> 7)
    else:
        px = ((r >> 4) << 12) | ((g >> 4) << 8) | ((b >> 4) << 4) | (al >> 4)
    return px.astype("<u2").tobytes()


# ------------------------------------------------------------------------------------------------ backfire.wpk (WPK2)
class BackfireWpk:
    def __init__(self):
        self.path = None
        self.chunk_size = 0.0
        self.tex = []          # dicts: name, w, h, fmt, raw
        self.flames = []       # dicts: tag, tex, flags, V (VDT array), I (u16)
        self.orig = None
        self.raw_file = b""

    @classmethod
    def load(cls, path):
        try:
            with open(path, "rb") as fh:
                b = fh.read()
            magic, nt, nc, nb, cs, to, co, bo = struct.unpack_from("<4sIIIfIII", b, 0)
        except (OSError, struct.error) as ex:
            raise BackfireError("Cannot read %s: %s" % (os.path.basename(path), ex))
        if magic != b"WPK2":
            raise BackfireError("%s is not a backfire pack (WPK2), it says %r." % (os.path.basename(path), magic))
        p = cls()
        p.path, p.chunk_size, p.raw_file = path, cs, b
        try:
            for i in range(nt):
                nm, w, h, fmt, off, sz = struct.unpack_from("<48sHHIII", b, to + 64 * i)
                if off + sz > len(b) or sz < w * h * 2:
                    raise BackfireError("texture %d runs past the end of the file" % i)
                p.tex.append(dict(name=nm.split(b"\0")[0].decode("latin1"), w=w, h=h, fmt=fmt, raw=bytes(b[off:off + sz])))
            batches = []
            for i in range(nb):
                t, fl, vo, vn, io, inn = struct.unpack_from("<HHIIII", b, bo + 20 * i)
                if vn == 0 or inn == 0:
                    batches.append((t, fl, np.zeros(0, VDT), np.zeros(0, "<u2")))
                    continue
                batches.append((t, fl, np.frombuffer(b, VDT, count=vn, offset=vo).copy(),
                                np.frombuffer(b, "<u2", count=inn, offset=io).copy()))
            for i in range(nc):
                v = struct.unpack_from("<6fIII", b, co + 36 * i)
                first, n, tag = v[6], v[7], v[8]
                if n != 1 or first >= nb:
                    raise BackfireError("flame %d does not hold exactly one batch (%d)" % (i, n))
                t, fl, V, I = batches[first]
                p.flames.append(dict(tag=tag, tex=t, flags=fl, V=V, I=I))
        except struct.error as ex:
            raise BackfireError("%s is damaged: %s" % (os.path.basename(path), ex))
        p.orig = [f["V"].copy() for f in p.flames]
        return p

    def bbox(self, f):
        V = f["V"]
        if not len(V):
            return (0.0,) * 3, (0.0,) * 3
        return tuple(float(x) for x in V["pos"].min(0)), tuple(float(x) for x in V["pos"].max(0))

    def to_bytes(self):
        nt, nc = len(self.tex), len(self.flames)
        to, co = 32, 32 + 64 * nt
        bo = co + 36 * nc
        data_off = bo + 20 * nc
        blob = bytearray()

        def put(data):
            while (data_off + len(blob)) % 4:
                blob.append(0)
            off = data_off + len(blob)
            blob.extend(data)
            return off

        tt = bytearray()
        for t in self.tex:
            tt += struct.pack("<48sHHIII", t["name"].encode("latin1")[:47], t["w"], t["h"], t["fmt"], put(t["raw"]), len(t["raw"]))
        bt, ct = bytearray(), bytearray()
        for i, f in enumerate(self.flames):
            if len(f["V"]):
                vo, io = put(f["V"].tobytes()), put(f["I"].tobytes())
            else:
                vo = io = 0
            bt += struct.pack("<HHIIII", f["tex"], f["flags"], vo, len(f["V"]), io, len(f["I"]))
            lo, hi = self.bbox(f)
            ct += struct.pack("<6fIII", *lo, *hi, i, 1, f["tag"])
        head = struct.pack("<4sIIIfIII", b"WPK2", nt, nc, nc, self.chunk_size, to, co, bo)
        return bytes(head + tt + ct + bt + blob)

    def save(self, path, backup=True):
        data = self.to_bytes()
        made = False
        if backup and os.path.isfile(path) and not os.path.exists(path + ".bak"):
            shutil.copy2(path, path + ".bak")
            made = True
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        self.path, self.raw_file = path, data
        return dict(bytes=len(data), bak=made)

    # ---- editing
    def scale_flame(self, i, width=1.0, length=1.0):
        """multiply a flame's X/Y (width) and Z (length) around the origin where it starts"""
        V = self.flames[i]["V"]
        if len(V):
            V["pos"][:, 0] *= width
            V["pos"][:, 1] *= width
            V["pos"][:, 2] *= length

    def reset_flame(self, i):
        self.flames[i]["V"] = self.orig[i].copy()

    def snapshot(self):
        return ([dict(f, V=f["V"].copy(), I=f["I"].copy()) for f in self.flames], [dict(t) for t in self.tex])

    def restore(self, snap):
        self.flames, self.tex = [dict(f, V=f["V"].copy(), I=f["I"].copy()) for f in snap[0]], [dict(t) for t in snap[1]]

    def replace_tex(self, i, image):
        t = self.tex[i]
        t["raw"] = encode_tex(image, t["w"], t["h"], t["fmt"])


# ------------------------------------------------------------------------------------------------ backfire.bfx (BFX1)
FRAMES = 17
MAX_ABS = 1e12


class Bfx:
    """Read like the game's src/backfire.c (bfx_parse). Layout, little endian:
         'BFX1' u16 version=1  u16 flags  u32 frames=17  u32 n_mesh  u32 n_nozzle
         n_mesh   x { u32 group, u32 geometry, u32 ordinal, str variant, str path, 17 x 16 floats (one 4x4 matrix per frame) }
         n_nozzle x { str car ("cars/<pack>[:aero_a|b|c]|<id>"), str muffler, str slot, str path, 16 floats (translation matrix) }
       str = u16 length + bytes.  The game draws a flame at every MUFFLER1 nozzle of the car's key: car_body x nozzle matrix x 0.5 x frame matrix,
       so the nozzle's x y z (floats 12..14, car space, 10 units = 1 m, +Z front) is where the flame sits on that car.
       Only numbers are ever changed here: the file size and every other byte stay as they were."""

    def __init__(self, data, path=None):
        self.path = path
        self.b = bytearray(data)
        self.orig = bytes(data)
        d = self.b
        try:
            magic, ver, flags, frames, nm, nn = struct.unpack_from("<4sHHIII", d, 0)
        except struct.error:
            raise BackfireError("not a BFX1 file")
        if magic != b"BFX1" or ver != 1 or frames != FRAMES or flags & ~1 or not nm:
            raise BackfireError("not a BFX1 file the game accepts")
        self.meshes, self.nozzles = [], []
        o = 20
        for _ in range(nm):
            g, ge, od = struct.unpack_from("<III", d, o)
            variant, o2 = self._str(o + 12)
            path_, o3 = self._str(o2)
            if o3 + FRAMES * 64 > len(d):
                raise BackfireError("mesh record runs past the end of the file")
            self.meshes.append(dict(group=g, geometry=ge, ordinal=od, variant=variant, path=path_, off=o3))
            o = o3 + FRAMES * 64
        for _ in range(nn):
            car, o = self._str(o)
            mu, o = self._str(o)
            sl, o = self._str(o)
            pa, o = self._str(o)
            if o + 64 > len(d):
                raise BackfireError("nozzle record runs past the end of the file")
            body, _, cid = car.rpartition("|")
            self.nozzles.append(dict(car=car, body=body, id=cid, muffler=mu, slot=sl, path=pa, off=o))
            o += 64
        if o != len(d):
            raise BackfireError("%d unexpected bytes at the end of the .bfx" % (len(d) - o))

    def _str(self, o):
        n = struct.unpack_from("<H", self.b, o)[0]
        s = bytes(self.b[o + 2:o + 2 + n])
        if not n or n > 255 or len(s) != n:
            raise BackfireError("unexpected text at 0x%x" % o)
        return s.decode("utf-8"), o + 2 + n

    @classmethod
    def load(cls, path):
        try:
            with open(path, "rb") as fh:
                return cls(fh.read(), path)
        except OSError as ex:
            raise BackfireError("Cannot read %s: %s" % (os.path.basename(path), ex))

    # ---- flame animations: 17 frames x a 4x4 matrix (16 floats, as stored)
    def frames(self, i):
        return np.frombuffer(bytes(self.b), "<f4", count=FRAMES * 16, offset=self.meshes[i]["off"]).reshape(FRAMES, 16).tolist()

    def set_frame_value(self, i, frame, k, v):
        _check(v)
        struct.pack_into("<f", self.b, self.meshes[i]["off"] + 64 * frame + 4 * k, float(v))

    # ---- outlets (nozzles)
    def car_keys(self):
        """ordered {car key: [nozzle indexes]}"""
        out = {}
        for i, n in enumerate(self.nozzles):
            out.setdefault(n["car"], []).append(i)
        return out

    def pos(self, i):
        return struct.unpack_from("<3f", self.b, self.nozzles[i]["off"] + 48)

    def set_pos(self, i, x, y, z):
        for v in (x, y, z):
            _check(v)
        struct.pack_into("<3f", self.b, self.nozzles[i]["off"] + 48, float(x), float(y), float(z))

    def changed(self):
        return bytes(self.b) != self.orig

    def save(self, path, backup=True):
        made = False
        if backup and os.path.isfile(path) and not os.path.exists(path + ".bak"):
            shutil.copy2(path, path + ".bak")
            made = True
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(bytes(self.b))
        os.replace(tmp, path)
        self.path, self.orig = path, bytes(self.b)
        return dict(bytes=len(self.b), bak=made)


def _check(v):
    v = float(v)
    if not np.isfinite(v) or abs(v) > MAX_ABS:
        raise BackfireError("%r is not a usable number" % v)


def car_key_of(path):
    """the .bfx key body of a car file: maz_FD3S.wpk -> cars/maz_FD3S, maz_FD3S_aeroB.wpk -> cars/maz_FD3S:aero_b"""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^(.*)_aero([ABCabc])$", stem)
    return ("cars/%s:aero_%s" % (m.group(1), m.group(2).lower())) if m else "cars/" + stem


def find_pair(path):
    """backfire.wpk <-> backfire.bfx in the same folder (same stem)"""
    stem = os.path.splitext(path)[0]
    other = stem + (".bfx" if path.lower().endswith(".wpk") else ".wpk")
    return other if os.path.isfile(other) else None


# ------------------------------------------------------------------------------------------------ command line
def info(wpk, bfx=None):
    p = BackfireWpk.load(wpk)
    out = ["%s: %d textures, %d flames" % (os.path.basename(wpk), len(p.tex), len(p.flames))]
    for i, t in enumerate(p.tex):
        out.append("  texture %d  %-12s %dx%d fmt %d" % (i, t["name"], t["w"], t["h"], t["fmt"]))
    b = Bfx.load(bfx) if bfx else None
    by_group = {m["group"]: m for m in b.meshes} if b else {}
    for i, f in enumerate(p.flames):
        lo, hi = p.bbox(f)
        m = by_group.get(f["tag"])
        out.append("  flame %2d tag %2d  %3d verts %3d tris  tex %d  z %.1f..%.1f  width %.2f  %s"
                   % (i + 1, f["tag"], len(f["V"]), len(f["I"]) // 3, f["tex"], lo[2], hi[2], hi[0] - lo[0], ("%s %s" % (m["variant"], m["path"])) if m else ""))
    if b:
        keys = b.car_keys()
        out.append("  .bfx: %d flame animations, %d outlets on %d car keys" % (len(b.meshes), len(b.nozzles), len(keys)))
    return "\n".join(out)


def outlets(bfx, key=None):
    b = Bfx.load(bfx)
    out = []
    for k, idx in b.car_keys().items():
        if key and k.split("|")[0] != key and k != key:
            continue
        for i in idx:
            n = b.nozzles[i]
            out.append("%-34s %-9s %-11s x %8.4f  y %8.4f  z %9.4f%s" % (k, n["muffler"], n["slot"], *b.pos(i), "" if n["muffler"] == "MUFFLER1" else "   (not drawn)"))
    return "\n".join(out) or "no outlets for %r" % key


def set_outlet(bfx, key, slot, x, y, z, out=None):
    b = Bfx.load(bfx)
    hit = [i for i, n in enumerate(b.nozzles) if n["car"].split("|")[0] == key and n["muffler"] == "MUFFLER1" and n["slot"] == slot]
    if not hit:
        raise BackfireError("no MUFFLER1 %s outlet for %r" % (slot, key))
    b.set_pos(hit[0], x, y, z)
    b.save(out or bfx, backup=out is None)
    return "%s %s -> %g %g %g" % (key, slot, x, y, z)


def selftest(wpk, bfx=None):
    p = BackfireWpk.load(wpk)
    ok = p.to_bytes() == p.raw_file
    msg = ["wpk rewritten byte for byte: %s" % ok]
    for i, t in enumerate(p.tex):                   # a picture goes in and out with only the 16-bit rounding
        im = decode_tex(t)
        msg.append("texture %d re-encode identical: %s" % (i, encode_tex(im, t["w"], t["h"], t["fmt"]) == t["raw"]))
    if bfx:
        b = Bfx.load(bfx)
        msg.append("bfx: %d flame animations, %d outlets, parsed to the last byte, saved unchanged: %s" % (len(b.meshes), len(b.nozzles), bytes(b.b) == b.orig))
        groups = sorted(m["group"] for m in b.meshes)
        msg.append("wpk part tags match the bfx groups: %s" % (groups == sorted(f["tag"] for f in p.flames)))
    return "\n".join(msg)


def cli(argv):
    try:
        if argv and argv[0] == "outlets" and len(argv) >= 2:
            print(outlets(argv[1], argv[2] if len(argv) > 2 else None))
            return 0
        if argv and argv[0] == "setoutlet" and len(argv) >= 7:
            print(set_outlet(argv[1], argv[2], argv[3], float(argv[4]), float(argv[5]), float(argv[6]), argv[7] if len(argv) > 7 else None))
            return 0
        if len(argv) < 2 or argv[0] not in ("info", "selftest"):
            print(__doc__)
            return 1
        wpk = argv[1]
        bfx = argv[2] if len(argv) > 2 else find_pair(wpk)
        print((info if argv[0] == "info" else selftest)(wpk, bfx))
        return 0
    except (BackfireError, ValueError) as ex:
        print("error:", ex)
        return 1


# ------------------------------------------------------------------------------------------------ car wireframe for the outlet views
def render_wire(tris, ax, ay, flip_x, W, H, lo, hi, depth_axis, near_is_low, dlo=-1e9, dhi=1e9):
    """Wireframe picture of a car body. tris = [(vertices (n,3), indices (m,3))]; the view looks along the axis that is not ax/ay;
    lo / hi = the (ax, ay) window drawn; only triangles whose centre is between dlo and dhi along depth_axis are drawn (a slice, so the
    car's far side does not clutter the view); edges are brighter the nearer they are. -> PIL RGB image W x H."""
    from PIL import ImageDraw
    im = Image.new("RGB", (W, H), (22, 26, 38))
    d = ImageDraw.Draw(im)
    sc = min(W / max(hi[0] - lo[0], 1e-3), H / max(hi[1] - lo[1], 1e-3))
    mx, my = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    allz = np.concatenate([V[:, depth_axis] for V, _ in tris]) if tris else np.zeros(1)
    z0, z1 = float(allz.min()), float(allz.max())
    for V, I in tris:
        X = W / 2 + ((mx - V[:, ax]) if flip_x else (V[:, ax] - mx)) * sc
        Y = H / 2 - (V[:, ay] - my) * sc
        zc = V[:, depth_axis][I].mean(1)
        fr = (zc - z0) / max(z1 - z0, 1e-6)
        fr = 1 - fr if near_is_low else fr
        inside = ~((X[I].max(1) < 0) | (X[I].min(1) > W) | (Y[I].max(1) < 0) | (Y[I].min(1) > H)) & (zc >= dlo) & (zc <= dhi)
        for t, f in zip(I[inside], fr[inside]):
            c = int(70 + 185 * f)
            d.polygon([(float(X[k]), float(Y[k])) for k in t], outline=(c, int(c * 0.55), 70))
    return im


def render_car_views(path, pack=None, ppu=55):
    """The Models tab's own software 3D render (wpk_view) of a car from the rear and from the side, big (about `ppu` pixels per unit),
    without the wing so the pipes show. -> {"rear": dict(img, proj, s), "side": ...}; proj((x, y, z)) = pixel in img, s = pixels per unit."""
    import wpk_view
    pk = pack or E.Pack.load(path)
    model = E.view_model(pk, E.stock_pack_of(path))
    drawn = (model[0], wpk_view.default_chunks(model), model[2])
    Pm = wpk_view._geometry(*drawn)[0]
    out = {}
    for name, (yaw, pitch) in (("rear", (180.0, 0.0)), ("side", (90.0, 0.0))):
        cy_, sy_ = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
        cp, sp = np.cos(np.radians(pitch)), np.sin(np.radians(pitch))
        R = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]]) @ np.array([[cy_, 0, sy_], [0, 1, 0], [-sy_, 0, cy_]])
        P = Pm @ R.T
        lo, hi = P.reshape(-1, 3).min(0), P.reshape(-1, 3).max(0)
        W, H = int((hi[0] - lo[0]) * ppu + 24.5), int((hi[1] - lo[1]) * ppu + 24.5)
        g = wpk_view.rasterize(drawn, yaw, pitch, W, H)                    # the same fit as rasterize: margin 24, centred on the drawn parts
        im = wpk_view.compose(g, model[0]).convert("RGB")
        s = min((W - 24) / max(hi[0] - lo[0], 1e-6), (H - 24) / max(hi[1] - lo[1], 1e-6))
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2

        def proj(p, R=R, s=s, cx=cx, cy=cy, W=W, H=H):
            q = R @ np.asarray(p, dtype=np.float64)
            return (q[0] - cx) * s + W / 2, H / 2 - (q[1] - cy) * s
        out[name] = dict(img=im, proj=proj, s=s)
    return out


# ------------------------------------------------------------------------------------------------ scrolling for small screens
def scrollable(parent):
    """Put a scrollable area into `parent` and return the frame to build in. Bars show only when the content is bigger than the window;
    otherwise the frame is stretched to fill it, so wide screens look exactly as before. Mouse wheel = up / down, Shift + wheel = sideways."""
    import tkinter as tk
    from tkinter import ttk

    outer = ttk.Frame(parent)
    outer.pack(fill="both", expand=True)
    cv = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
    vs = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
    hs = ttk.Scrollbar(outer, orient="horizontal", command=cv.xview)
    cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    cv.pack(side="left", fill="both", expand=True)
    inner = ttk.Frame(cv)
    win = cv.create_window(0, 0, window=inner, anchor="nw")

    def relayout(_e=None):
        rw, rh = inner.winfo_reqwidth(), inner.winfo_reqheight()
        cw, ch = cv.winfo_width(), cv.winfo_height()
        if cw < 2 or ch < 2:
            return
        need_v, need_h = rh > ch, rw > cw
        if need_v != bool(vs.winfo_manager()):
            vs.pack(side="right", fill="y", before=cv) if need_v else vs.pack_forget()
        if need_h != bool(hs.winfo_manager()):
            hs.pack(side="bottom", fill="x", before=cv) if need_h else hs.pack_forget()
        w, h = max(rw, cw), max(rh, ch)
        cv.itemconfigure(win, width=w, height=h)
        cv.configure(scrollregion=(0, 0, w, h))

    inner.bind("<Configure>", relayout)
    cv.bind("<Configure>", relayout)

    def wheel(e):
        w = e.widget
        try:
            if not str(w).startswith(str(outer)) or (w is not cv and w.winfo_class() in ("Canvas", "Listbox", "Treeview", "Text", "Scale", "TCombobox")):
                return                                  # not ours: a picture, list or slider scrolls / zooms itself
        except tk.TclError:
            return
        n = getattr(e, "num", 0)
        d = -1 if (n == 4 or getattr(e, "delta", 0) > 0) else 1
        (cv.xview_scroll if (e.state & 1) else cv.yview_scroll)(3 * d, "units")

    for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        cv.bind_all(ev, wheel, add="+")
    return inner


# ------------------------------------------------------------------------------------------------ the tab
def build_tab(tab_parent, root, app=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from PIL import ImageTk

    try:
        import wpk_view
    except ImportError:
        wpk_view = None
    W_, H_ = 520, 300

    class Tab:
        def __init__(self):
            self.wpk = None
            self.bfx = None
            self.wpk_path = None
            self.bfx_path = None
            self.undo = []
            self.photo = None
            self.status = vpk_ui.StatusLine("Open backfire.wpk (backfire.bfx beside it is opened too).")
            self.view = tk.StringVar(value="side")
            self.texvar = tk.StringVar()
            self.wvar = tk.StringVar(value="1.0")
            self.lvar = tk.StringVar(value="1.0")
            self.build()
            self.autoload()

        # ---- layout
        def build(self):
            parent = self.parent = scrollable(tab_parent)
            top = ttk.Frame(parent, padding=(12, 10, 12, 4))
            top.pack(fill="x")
            ttk.Button(top, text="Open backfire.wpk…", command=self.open_dialog).pack(side="left")
            self.name_lbl = ttk.Label(top, text="", foreground="#8f9eb2")
            self.name_lbl.pack(side="left", padx=10)
            ttk.Button(top, text="How it works", command=self.help).pack(side="right")
            ttk.Separator(parent).pack(fill="x")
            body = ttk.Frame(parent)
            body.pack(fill="both", expand=True)
            body.columnconfigure(1, weight=1)
            body.rowconfigure(0, weight=1)

            left = ttk.Frame(body, padding=(10, 8))
            left.grid(row=0, column=0, sticky="ns")
            cols = ("n", "name", "tex", "tris")
            self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended", height=17)
            for c, t, w, a in (("n", "#", 28, "e"), ("name", "Flame (from the .bfx)", 150, "w"), ("tex", "Tex", 34, "center"), ("tris", "Tris", 40, "e")):
                self.tree.heading(c, text=t)
                self.tree.column(c, width=w, anchor=a, stretch=False)
            self.tree.pack(side="top", fill="y", expand=True)
            self.tree.bind("<<TreeviewSelect>>", lambda _e: self.render())

            right = ttk.Frame(body, padding=(4, 8))
            right.grid(row=0, column=1, sticky="nsew")
            pv = ttk.Frame(right)
            pv.pack(fill="x")
            self.canvas = tk.Canvas(pv, width=W_, height=H_, background="#1d2230", highlightthickness=0)
            self.canvas.pack(side="left")
            self.tcanvas = tk.Canvas(pv, width=H_, height=H_, background="#1d2230", highlightthickness=0)
            self.tcanvas.pack(side="left", padx=(6, 0))
            vr = ttk.Frame(right)
            vr.pack(fill="x", pady=(4, 0))
            for txt, v in (("Side (length)", "side"), ("Front (width)", "front")):
                ttk.Radiobutton(vr, text=txt, variable=self.view, value=v, command=self.render).pack(side="left", padx=(0, 8))
            ttk.Label(vr, text="   Right picture: the texture with the selected flame's UV lines.", foreground="#8f9eb2").pack(side="left")

            ctl = ttk.LabelFrame(right, text="Edit", padding=8)
            ctl.pack(fill="x", pady=(8, 0))
            r1 = ttk.Frame(ctl)
            r1.pack(fill="x")
            ttk.Label(r1, text="Texture:").pack(side="left")
            self.tex_box = ttk.Combobox(r1, textvariable=self.texvar, state="readonly", width=24)
            self.tex_box.pack(side="left", padx=4)
            self.tex_box.bind("<<ComboboxSelected>>", lambda _e: self.render())
            self.b_texout = ttk.Button(r1, text="Export PNG…", command=self.export_tex, state="disabled")
            self.b_texout.pack(side="left", padx=4)
            self.b_texin = ttk.Button(r1, text="Replace with PNG…", command=self.replace_tex, state="disabled")
            self.b_texin.pack(side="left")
            r2 = ttk.Frame(ctl)
            r2.pack(fill="x", pady=(8, 0))
            ttk.Label(r2, text="Width ×").pack(side="left")
            ttk.Entry(r2, textvariable=self.wvar, width=6).pack(side="left", padx=(2, 8))
            ttk.Label(r2, text="Length ×").pack(side="left")
            ttk.Entry(r2, textvariable=self.lvar, width=6).pack(side="left", padx=(2, 8))
            self.b_sel = ttk.Button(r2, text="Apply to selected", command=lambda: self.apply_scale(False), state="disabled")
            self.b_sel.pack(side="left", padx=2)
            self.b_all = ttk.Button(r2, text="Apply to all 17", command=lambda: self.apply_scale(True), state="disabled")
            self.b_all.pack(side="left", padx=2)
            self.b_rst = ttk.Button(r2, text="Reset selected", command=self.reset_sel, state="disabled")
            self.b_rst.pack(side="left", padx=2)
            r3 = ttk.Frame(ctl)
            r3.pack(fill="x", pady=(8, 0))
            self.b_keys = ttk.Button(r3, text="Edit animation…", command=self.edit_keys, state="disabled")
            self.b_keys.pack(side="left")
            self.b_out = ttk.Button(r3, text="Exhaust outlets…", command=self.edit_outlets, state="disabled")
            self.b_out.pack(side="left", padx=6)
            self.info = ttk.Label(r3, text="", foreground="#8f9eb2")
            self.info.pack(side="left", padx=10)

            ttk.Separator(parent).pack(fill="x")
            bar = ttk.Frame(parent, padding=(12, 8))
            bar.pack(fill="x")
            self.status.bar(bar, ttk, tk, side="left", fill="x", expand=True)
            self.b_save = ttk.Button(bar, text="Save to game", command=self.save, state="disabled")
            self.b_save.pack(side="right")
            self.b_undo = ttk.Button(bar, text="Undo", command=self.do_undo, state="disabled")
            self.b_undo.pack(side="right", padx=(0, 8))

        def help(self):
            w = tk.Toplevel(root)
            w.title("Backfire flame - how it works")
            t = tk.Text(w, wrap="word", width=92, height=26, padx=10, pady=8)
            t.insert("1.0", __doc__)
            t.configure(state="disabled")
            t.pack(fill="both", expand=True)

        # ---- loading
        def pref(self, k):
            try:
                return wpk_view.load_prefs().get(k) if wpk_view else None
            except Exception:
                return None

        def autoload(self):
            p = self.pref("backfire_last")
            if p and os.path.isfile(p):
                self.open_path(p)

        def open_dialog(self):
            f = filedialog.askopenfilename(title="Open backfire.wpk", filetypes=[("Backfire pack", "*.wpk"), ("All files", "*.*")])
            if f:
                self.open_path(f)

        def open_path(self, path):
            try:
                w = BackfireWpk.load(path)
                other = find_pair(path)
                b = Bfx.load(other) if other else None
                if b and sorted(m["group"] for m in b.meshes) != sorted(f["tag"] for f in w.flames):
                    raise BackfireError("The .bfx and the .wpk do not belong together (their flame groups differ).")
            except BackfireError as ex:
                messagebox.showerror("Backfire flame", str(ex))
                return
            self.wpk, self.bfx, self.wpk_path, self.bfx_path = w, b, path, other
            self.undo = []
            try:
                if wpk_view:
                    wpk_view.save_prefs(backfire_last=path)
            except Exception:
                pass
            self.name_lbl.configure(text=os.path.basename(path) + (" + " + os.path.basename(other) if other else "  (no .bfx beside it: animation editing off)"))
            self.tex_box.configure(values=["%d  %s  %dx%d" % (i, t["name"], t["w"], t["h"]) for i, t in enumerate(w.tex)])
            self.texvar.set(self.tex_box.cget("values")[0])
            for s in (self.b_texout, self.b_texin, self.b_sel, self.b_all, self.b_rst):
                s.configure(state="normal")
            self.b_keys.configure(state="normal" if b else "disabled")
            self.b_out.configure(state="normal" if b else "disabled")
            self.fill()
            self.status.set("Opened %s." % os.path.basename(path))

        def fill(self, keep=None):
            sel = keep if keep is not None else [int(i) for i in self.tree.selection()]
            self.tree.delete(*self.tree.get_children())
            for i, f in enumerate(self.wpk.flames):
                m = self.mesh_of(i)
                nm = "%s  %s" % (m["variant"], m["path"]) if m else "tag %d" % f["tag"]
                self.tree.insert("", "end", iid=str(i), values=(i + 1, nm, f["tex"], len(f["I"]) // 3))
            ids = [str(i) for i in sel if 0 <= i < len(self.wpk.flames)] or ["0"]
            self.tree.selection_set(ids)
            self.render()

        def selected(self):
            return [int(i) for i in self.tree.selection()] or []

        # ---- drawing
        def render(self):
            if not self.wpk:
                return
            sel = self.selected()
            c = self.canvas
            c.delete("all")
            ax, ay = (2, 1) if self.view.get() == "side" else (0, 1)
            allv = [self.wpk.flames[i]["V"]["pos"] for i in (sel or range(len(self.wpk.flames))) if len(self.wpk.flames[i]["V"])]
            if allv:
                P = np.concatenate(allv)
                x0, x1, y0, y1 = P[:, ax].min(), P[:, ax].max(), P[:, ay].min(), P[:, ay].max()
                span = max(x1 - x0, y1 - y0, 0.5)
                s = 0.84 * min(W_, H_) / span
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                for i in sel:
                    f = self.wpk.flames[i]
                    V, I = f["V"]["pos"], f["I"].reshape(-1, 3)
                    for t in I:
                        pts = []
                        for k in t:
                            pts += [W_ / 2 + (V[k][ax] - cx) * s, H_ / 2 - (V[k][ay] - cy) * s]
                        c.create_polygon(*pts, outline="#ffb347", fill="", width=1)
                c.create_text(8, 8, anchor="nw", fill="#9aa4b8", text="%.1f units across (10 = 1 m)" % span)
            self.draw_tex(sel)
            if sel and self.mesh_of(sel[0]):
                r = self.mesh_of(sel[0])
                lo, hi = self.wpk.bbox(self.wpk.flames[sel[0]])
                self.info.configure(text="%s  %s   length %.1f  width %.2f" % (r["variant"], r["path"], hi[2] - lo[2], hi[0] - lo[0]))

        def mesh_of(self, i):
            """the .bfx flame animation that belongs to the i-th flame of the .wpk (same group number)"""
            if not self.bfx:
                return None
            tag = self.wpk.flames[i]["tag"]
            return next((m for m in self.bfx.meshes if m["group"] == tag), None)

        def tex_index(self):
            try:
                return int(self.texvar.get().split()[0])
            except (ValueError, IndexError):
                return 0

        def draw_tex(self, sel):
            c = self.tcanvas
            c.delete("all")
            ti = self.tex_index()
            t = self.wpk.tex[ti]
            im = decode_tex(t)
            bg = Image.new("RGBA", im.size, (40, 44, 58, 255))
            bg.alpha_composite(im)
            sc = H_ / max(t["w"], t["h"])
            bg = bg.resize((int(t["w"] * sc), int(t["h"] * sc)), Image.NEAREST).convert("RGB")
            self.photo = ImageTk.PhotoImage(bg)
            c.create_image(0, 0, anchor="nw", image=self.photo)
            for i in sel:
                f = self.wpk.flames[i]
                if f["tex"] != ti:
                    continue
                uv, I = f["V"]["uv"], f["I"].reshape(-1, 3)
                for tri in I:
                    pts = []
                    for k in tri:
                        pts += [uv[k][0] * t["w"] * sc, uv[k][1] * t["h"] * sc]
                    c.create_polygon(*pts, outline="#ff4d4d", fill="")

        # ---- editing
        def push(self):
            self.undo.append(self.wpk.snapshot() + (bytes(self.bfx.b) if self.bfx else None,))
            self.b_undo.configure(state="normal")
            self.b_save.configure(state="normal")

        def do_undo(self):
            if not self.undo:
                return
            snap = self.undo.pop()
            self.wpk.restore(snap[:2])
            if self.bfx and snap[2] is not None:
                self.bfx.b = bytearray(snap[2])
            self.b_undo.configure(state="normal" if self.undo else "disabled")
            self.fill()
            self.status.set("Undone.")

        def apply_scale(self, everything):
            try:
                wv, lv = float(self.wvar.get()), float(self.lvar.get())
                if not (0.05 <= wv <= 20 and 0.05 <= lv <= 20):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Backfire flame", "Width and Length must be numbers between 0.05 and 20.")
                return
            idx = list(range(len(self.wpk.flames))) if everything else self.selected()
            if not idx:
                return
            self.push()
            for i in idx:
                self.wpk.scale_flame(i, wv, lv)
            self.fill(keep=self.selected())
            self.status.set("Scaled %d flame(s): width × %g, length × %g.  Save to game to keep it." % (len(idx), wv, lv))

        def reset_sel(self):
            idx = self.selected()
            if not idx:
                return
            self.push()
            for i in idx:
                self.wpk.reset_flame(i)
            self.fill(keep=idx)
            self.status.set("Reset %d flame(s) to the shape the file was opened with." % len(idx))

        def export_tex(self):
            ti = self.tex_index()
            t = self.wpk.tex[ti]
            f = filedialog.asksaveasfilename(title="Export texture", defaultextension=".png", initialfile=t["name"] + ".png", filetypes=[("PNG", "*.png")])
            if f:
                decode_tex(t).save(f)
                self.status.set("Wrote %s (%dx%d)." % (os.path.basename(f), t["w"], t["h"]))

        def replace_tex(self):
            ti = self.tex_index()
            t = self.wpk.tex[ti]
            f = filedialog.askopenfilename(title="PNG for %s (%dx%d)" % (t["name"], t["w"], t["h"]), filetypes=[("Pictures", "*.png *.tga *.bmp"), ("All files", "*.*")])
            if not f:
                return
            try:
                im = Image.open(f)
                im.load()
            except Exception as ex:
                messagebox.showerror("Backfire flame", "Cannot read the picture: %s" % ex)
                return
            if im.size != (t["w"], t["h"]) and not messagebox.askyesno("Backfire flame", "The picture is %dx%d, the texture is %dx%d.\nStretch it to fit?" % (im.size + (t["w"], t["h"]))):
                return
            self.push()
            self.wpk.replace_tex(ti, im)
            self.render()
            self.status.set("Texture %s replaced (kept at %dx%d, format %d)." % (t["name"], t["w"], t["h"], t["fmt"]))

        def edit_keys(self):
            sel = self.selected()
            if not sel or not self.mesh_of(sel[0]):
                return
            i = sel[0]
            mi = self.bfx.meshes.index(self.mesh_of(i))
            rec = self.bfx.meshes[mi]
            w = tk.Toplevel(root)
            w.title("Animation - flame %d  %s  %s" % (i + 1, rec["variant"], rec["path"]))
            ttk.Label(w, text="One 4x4 matrix per frame (17 frames), the 16 numbers as the file stores them (m0..m15; m0, m5, m10 = size, m12..m14 = offset).\n"
                              "Double-click a number to change it. The file size never changes. Save to game writes it.", padding=8, justify="left").pack(anchor="w")
            cols = ("f",) + tuple("m%d" % k for k in range(16))
            tr = ttk.Treeview(w, columns=cols, show="headings", height=17)
            tr.heading("f", text="Frame")
            tr.column("f", width=46, anchor="e", stretch=False)
            for k in range(16):
                tr.heading("m%d" % k, text="m%d" % k)
                tr.column("m%d" % k, width=62, anchor="e", stretch=False)
            for fr, row in enumerate(self.bfx.frames(mi)):
                tr.insert("", "end", iid=str(fr), values=(fr,) + tuple("%.4g" % x for x in row))
            tr.pack(fill="both", expand=True)
            pushed = []

            def edit(ev):
                row, col = tr.identify_row(ev.y), tr.identify_column(ev.x)
                if not row or col == "#1":
                    return
                k = int(col[1:]) - 2
                x, y, wd, ht = tr.bbox(row, col)
                en = ttk.Entry(tr)
                en.place(x=x, y=y, width=wd, height=ht)
                en.insert(0, "%.9g" % self.bfx.frames(mi)[int(row)][k])
                en.focus_set()
                en.select_range(0, "end")

                def done(_e=None):
                    try:
                        v = float(en.get())
                        _check(v)
                    except (ValueError, BackfireError):
                        en.destroy()
                        return
                    if not pushed:
                        self.push()
                        pushed.append(1)
                    self.bfx.set_frame_value(mi, int(row), k, v)
                    tr.set(row, "m%d" % k, "%.4g" % self.bfx.frames(mi)[int(row)][k])
                    en.destroy()
                    self.status.set("Animation of flame %d changed.  Save to game to keep it." % (i + 1))

                en.bind("<Return>", done)
                en.bind("<FocusOut>", done)
                en.bind("<Escape>", lambda _e: en.destroy())

            tr.bind("<Double-1>", edit)

        # ---- the outlets: where the flames sit on a car
        def edit_outlets(self):
            if not self.bfx:
                return
            keys = self.bfx.car_keys()
            names = sorted(keys, key=str.lower)
            st = dict(q=queue.Queue(), pts=None, car=None, ckey=None, tris=None, imgs={}, zoom=None, photos=[], dir=None, files=None, r3d=None, r3d_path=None, r3d_busy=None, r3d_err=None)
            mods = getattr(app, "models", None) if app else None
            car_path = getattr(mods, "path", None)
            w = tk.Toplevel(root)
            w.title("Exhaust outlets - where the flames sit on each car")
            w.geometry("%dx%d" % (min(1000, w.winfo_screenwidth() - 60), min(940, w.winfo_screenheight() - 110)))
            body = scrollable(w)
            top = ttk.Frame(body, padding=(8, 8, 8, 0))
            top.pack(fill="x")
            ttk.Label(top, text="The game draws a flame at each MUFFLER1 outlet (red). Outlets are stored per car body and are the STOCK car's, so move them to the new exhaust.\n"
                                "Rear / side view of the car body below (open the car's .wpk to see it). 10 units = 1 m. Right side of the car = negative x.",
                      justify="left").pack(anchor="w")
            main = ttk.Frame(body, padding=8)
            main.pack(fill="both", expand=True)
            left = ttk.Frame(main)
            left.pack(side="left", fill="y")
            fv = tk.StringVar(value="")
            ttk.Entry(left, textvariable=fv, width=30).pack(fill="x")
            lb = tk.Listbox(left, width=30, height=18, exportselection=False)
            lb.pack(fill="y", expand=True, pady=(4, 0))
            mid = ttk.Frame(main)
            mid.pack(side="left", fill="both", expand=True, padx=8)
            cols = ("muffler", "slot", "x", "y", "z")
            tr = ttk.Treeview(mid, columns=cols, show="headings", height=6, selectmode="browse")
            for c, t, wd in (("muffler", "Muffler", 80), ("slot", "Slot", 90), ("x", "x", 70), ("y", "y", 70), ("z", "z", 70)):
                tr.heading(c, text=t)
                tr.column(c, width=wd, anchor="e" if c in "xyz" else "w", stretch=False)
            tr.pack(fill="x")
            ttk.Label(mid, text="Double-click x, y or z to change it. MUFFLER2 rows are not drawn by the game.", foreground="#8f9eb2").pack(anchor="w")
            mv = ttk.Frame(mid)
            mv.pack(fill="x", pady=(8, 0))
            dv = [tk.StringVar(value="0"), tk.StringVar(value="0"), tk.StringVar(value="0")]
            ttk.Label(mv, text="Move all red by").pack(side="left")
            for lab, v in zip("xyz", dv):
                ttk.Label(mv, text=" " + lab).pack(side="left")
                ttk.Entry(mv, textvariable=v, width=6).pack(side="left")
            sf = ttk.LabelFrame(mid, text="Slide the selected outlet (click a row first)", padding=6)
            sf.pack(fill="x", pady=(8, 0))
            mir = tk.IntVar(value=1)
            sel_id, busy, sliders = [None], [False], []
            for axis, lab in enumerate("xyz"):
                row = ttk.Frame(sf)
                row.pack(fill="x")
                ttk.Label(row, text=lab, width=2).pack(side="left")
                sc = tk.Scale(row, from_=-1, to=1, resolution=0.01, orient="horizontal", length=330, showvalue=True,
                              command=lambda v, a=axis: slide(a, v))
                sc.pack(side="left", fill="x", expand=True)
                sc.bind("<ButtonPress-1>", lambda _e: self.push(), add="+")
                sc.bind("<KeyPress>", lambda _e: self.push(), add="+")
                sliders.append(sc)
            ttk.Checkbutton(sf, text="Keep left / right mirrored (moves the other side with it)", variable=mir).pack(anchor="w")
            cv = tk.StringVar(value="")
            ctl = ttk.Frame(mid)
            ctl.pack(fill="x", pady=(6, 0))
            zoom = tk.IntVar(value=1)
            ttk.Checkbutton(mid, text="Zoom on the exhaust (off = whole car)", variable=zoom, command=lambda: show()).pack(anchor="w", pady=(8, 0))
            cv2 = tk.Canvas(mid, width=560, height=300, background="#16192a", highlightthickness=0)
            cv3 = tk.Canvas(mid, width=560, height=300, background="#16192a", highlightthickness=0)
            cv2.pack(pady=(4, 0))
            cv3.pack(pady=(6, 0))
            msg = tk.StringVar(value="")
            ttk.Label(body, textvariable=msg, foreground="#8f9eb2", padding=(8, 0, 8, 8)).pack(anchor="w")

            def cur():
                s_ = lb.curselection()
                return lb.get(s_[0]) if s_ else None

            def fill_list(_e=None, select=None):
                lb.delete(0, "end")
                for n in names:
                    if fv.get().lower() in n.lower():
                        lb.insert("end", n)
                if select and select in lb.get(0, "end"):
                    k = lb.get(0, "end").index(select)
                    lb.selection_set(k)
                    lb.see(k)
                elif lb.size():
                    lb.selection_set(0)
                show()

            def load_car(path, select=True):
                try:
                    pk = E.Pack.load(path) if E else None
                except Exception as ex:
                    messagebox.showerror("Backfire flame", str(ex))
                    return
                if not pk:
                    return
                ch = pk.chunk(0)
                bs = [b for b in ch.batches if len(b.V) and len(b.I)] if ch else []
                P = np.concatenate([b.V["pos"] for b in bs]) if bs else np.zeros((0, 3), np.float32)
                st["tris"] = [(np.asarray(b.V["pos"], dtype=np.float64), np.asarray(b.I, dtype=np.int64).reshape(-1, 3)) for b in bs]
                st["imgs"] = {}
                st["pts"], st["car"] = P, path
                k = car_key_of(path)
                st["ckey"] = k
                st["r3d"], st["r3d_err"] = None, None
                start_3d(path, pk)
                if not select:
                    show()
                    return
                hit = next((n for n in names if n.split("|")[0] == k), None)
                if hit:
                    fv.set("")
                    fill_list(select=hit)
                else:
                    msg.set("%s has no outlets in this .bfx (key %s): pick another car in the list. New cars cannot be added; the file keeps its size." % (os.path.basename(path), k))
                    show()

            def start_3d(path, pk):
                """the Models tab's 3D render of the car, in a thread (a few seconds); the wireframe is shown until it is done"""
                if not E:
                    return
                try:
                    import wpk_view  # noqa: F401
                except ImportError:
                    return
                st["r3d_busy"], st["r3d_err"] = path, None
                show()

                def work():
                    try:
                        st["q"].put((path, render_car_views(path, pk), None))
                    except BaseException as ex:                        # no render: the wireframe stays, the reason is shown on the picture
                        import traceback
                        err = "%s: %s" % (type(ex).__name__, ex)
                        try:
                            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backfire_3d_error.txt"), "w") as fh:
                                fh.write(traceback.format_exc())
                        except OSError:
                            pass
                        st["q"].put((path, None, err))
                threading.Thread(target=work, daemon=True).start()
                msg.set("Rendering %s in 3D..." % os.path.basename(path))

            def poll():
                try:
                    while True:
                        path, res, err = st["q"].get_nowait()
                        if path == st["car"]:
                            st["r3d"], st["r3d_path"], st["r3d_err"], st["r3d_busy"] = res, path, err, None
                            st["imgs"] = {}
                            msg.set("3D render ready." if res else "No 3D render (%s): wireframe shown." % err)
                            show()
                except queue.Empty:
                    pass
                try:
                    w.after(250, poll)
                except tk.TclError:
                    pass

            def file_for_key(k):
                """cars/maz_FD3S -> maz_FD3S.wpk, cars/maz_FD3S:aero_b -> maz_FD3S_aeroB.wpk (any letter case) in the cars folder"""
                d = st["dir"]
                if not d or not os.path.isdir(d):
                    return None
                if st["files"] is None or st["files"][0] != d:
                    try:
                        st["files"] = (d, {f.lower(): f for f in os.listdir(d) if f.lower().endswith(".wpk")})
                    except OSError:
                        return None
                body, _, aero = k[5:].partition(":")
                want = (body + ("_aero" + aero[-1] if aero else "") + ".wpk").lower()
                f = st["files"][1].get(want)
                return os.path.join(d, f) if f else None

            def auto_car():
                k = (cur() or "").split("|")[0]
                if k and st["ckey"] != k:
                    f = file_for_key(k)
                    if f:
                        load_car(f, select=False)

            def pick_dir():
                d = filedialog.askdirectory(title="Folder with the car .wpk files")
                if d:
                    st["dir"], st["files"] = d, None
                    auto_car()
                    show()

            def open_car():
                f = filedialog.askopenfilename(title="Car .wpk (the body to line the outlets up with)", filetypes=[("Car pack", "*.wpk"), ("All files", "*.*")])
                if f:
                    st["dir"], st["files"] = os.path.dirname(f), None
                    load_car(f)

            def has_car():
                """the loaded body is the one of the selected key (a body of another car would mislead)"""
                return bool(st["tris"]) and st["ckey"] == (cur() or "").split("|")[0]

            def view_window(ax, ay):
                """the (ax, ay) window of a view: the whole car, or (Zoom) a patch around the exhaust outlets as they were when the car / key was picked"""
                P = st["pts"]
                lo, hi = P[:, [ax, ay]].min(0), P[:, [ax, ay]].max(0)
                if zoom.get() and st["zoom"] is not None:
                    c0, half = st["zoom"], (np.array([8.0, 5.0]) if ax == 0 else np.array([10.0, 5.0]))
                    c0 = np.array([c0[ax], c0[ay]])
                    lo, hi = np.maximum(c0 - half, lo - 0.5), np.minimum(c0 + half, hi + 0.5)
                    lo, hi = np.minimum(lo, hi - 2.0), hi
                return lo, hi

            def draw_view(c, ax, ay, flip_x, title):
                c.delete("all")
                pts = st["pts"]
                idx = keys.get(cur() or "", [])
                ns = [(self.bfx.nozzles[i], self.bfx.pos(i)) for i in idx]
                W, H = int(c.cget("width")), int(c.cget("height"))
                r3 = st["r3d"] if (st["r3d"] and st["r3d_path"] == st["car"]) else None
                view = r3["rear" if ax == 0 else "side"] if (r3 and has_car()) else None
                if view:                                                  # the 3D render, cropped (Zoom) or fitted (whole car)
                    big, proj, s_ = view["img"], view["proj"], view["s"]
                    if zoom.get() and st["zoom"] is not None:
                        cx0, cy0 = proj(st["zoom"])
                        bw = (16.0 if ax == 0 else 20.0) * s_
                        bh = bw * H / W
                        bx0, by0 = cx0 - bw / 2, cy0 - bh / 2
                    else:
                        bw = float(big.width)
                        bh = bw * H / W
                        if bh < big.height:
                            bh = float(big.height)
                            bw = bh * W / H
                        bx0, by0 = (big.width - bw) / 2, (big.height - bh) / 2
                    key = (ax, W, H, "3d", int(bx0), int(by0), int(bw))
                    if key not in st["imgs"]:
                        canvas_im = Image.new("RGB", (int(bw), int(bh)), (140, 158, 179))
                        canvas_im.paste(big, (-int(bx0), -int(by0)))
                        st["imgs"] = {k_: v_ for k_, v_ in st["imgs"].items() if k_[0] != ax}
                        st["imgs"][key] = ImageTk.PhotoImage(canvas_im.resize((W, H), Image.LANCZOS))
                    c.create_image(0, 0, anchor="nw", image=st["imgs"][key])

                    def pp(p):
                        X, Y = proj(p)
                        return (X - int(bx0)) * W / int(bw), (Y - int(by0)) * H / int(bh)
                else:
                    if has_car():
                        lo, hi = view_window(ax, ay)
                    else:
                        allp = np.array([[p[ax], p[ay]] for _n, p in ns]) if ns else None
                        if allp is None:
                            return
                        lo, hi = allp.min(0) - 5, allp.max(0) + 5
                    sc = 0.98 * min(W / max(hi[0] - lo[0], 1e-3), H / max(hi[1] - lo[1], 1e-3))
                    mx, my = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2

                    def pp(p):
                        return (W / 2 + ((mx - p[ax]) if flip_x else (p[ax] - mx)) * sc, H / 2 - (p[ay] - my) * sc)
                    if has_car():
                        if ax == 0:                                          # rear view: the rear 4 units of the car
                            dlo, dhi = float(pts[:, 2].min()), float(pts[:, 2].min()) + 4.0
                        else:                                                # side view: a 5 unit slice through the outlets
                            x0 = float(st["zoom"][0]) if st["zoom"] is not None else 0.0
                            dlo, dhi = x0 - 2.5, x0 + 2.5
                        key = (ax, W, H, bool(zoom.get()), tuple(np.round(lo, 2)), tuple(np.round(hi, 2)), round(dlo, 2))
                        if key not in st["imgs"]:
                            im = render_wire(st["tris"], ax, ay, flip_x, W, H, lo, hi, 2 if ax == 0 else 0, ax == 0, dlo, dhi)
                            st["imgs"] = {k_: v_ for k_, v_ in st["imgs"].items() if k_[0] != ax}     # one picture per view is kept
                            st["imgs"][key] = ImageTk.PhotoImage(im)
                        c.create_image(0, 0, anchor="nw", image=st["imgs"][key])
                for n, p in ns:
                    on = n["muffler"] == "MUFFLER1"
                    x, y = pp(p)
                    if ax == 2 and on:                                  # the flame points to the rear (-Z)
                        x2, y2 = pp((p[0], p[1], p[2] - 6))
                        c.create_line(x, y, x2, y2, fill="#7CFC00", width=2)
                    c.create_oval(x - 6, y - 6, x + 6, y + 6, outline="#00e5ff" if on else "#8a8f9c", width=2)
                    c.create_oval(x - 1, y - 1, x + 1, y + 1, outline="#00e5ff" if on else "#8a8f9c")
                    c.create_text(x + 9, y - 10, anchor="w", fill="#00e5ff" if on else "#8a8f9c", text=n["slot"][-2:] if on else "", font=("TkDefaultFont", 10, "bold"))
                c.create_text(6, 6, anchor="nw", fill="#ffffff" if view else "#9aa4b8", text=title)
                if st["r3d_busy"] and has_car() and not view:
                    c.create_text(W / 2, H - 14, fill="#d8b36a", text="Rendering the 3D view... (the wireframe is shown until it is ready)")
                elif st["r3d_err"] and has_car() and not view:
                    c.create_text(W / 2, H - 14, fill="#ff8a80", text="3D render failed: %s (see backfire_3d_error.txt) - wireframe shown" % st["r3d_err"][:90])
                if not has_car() and cur():
                    c.create_text(W / 2, H - 14, fill="#d8b36a", text="No car model for this key - pick the Cars folder or Open car .wpk")

            def show(_e=None):
                tr.delete(*tr.get_children())
                k = cur()
                if _e is not None:
                    sel_id[0] = None
                    auto_car()
                if _e is not None or st["zoom"] is None or st.get("zkey") != k:      # the zoom patch stays put while you slide
                    dr = [self.bfx.pos(i) for i in keys.get(k or "", []) if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"]
                    st["zoom"], st["zkey"] = (np.mean(np.array(dr), 0) if dr else None), k
                for i in keys.get(k or "", []):
                    n = self.bfx.nozzles[i]
                    x, y, z = self.bfx.pos(i)
                    tr.insert("", "end", iid=str(i), values=(n["muffler"], n["slot"], "%.4f" % x, "%.4f" % y, "%.4f" % z))
                if sel_id[0] is not None and tr.exists(str(sel_id[0])):
                    tr.selection_set(str(sel_id[0]))
                    sync_sliders()
                else:
                    sel_id[0] = None
                draw_view(cv2, 0, 1, True, "Rear view  (right side of the car on the right)")
                draw_view(cv3, 2, 1, False, "Side view  (front to the right, flames point left)")

            def counterpart(i):
                n = self.bfx.nozzles[i]
                sl = n["slot"]
                if len(sl) < 10 or sl[8] not in "LR":
                    return None
                want = sl[:8] + ("R" if sl[8] == "L" else "L") + sl[9:]
                return next((j for j in keys.get(n["car"], []) if self.bfx.nozzles[j]["muffler"] == n["muffler"] and self.bfx.nozzles[j]["slot"] == want), None)

            def sync_sliders():
                busy[0] = True
                try:
                    if sel_id[0] is None:
                        return
                    for a, v in enumerate(self.bfx.pos(sel_id[0])):
                        sliders[a].configure(from_=v - 6, to=v + 6)
                        sliders[a].set(v)
                finally:
                    busy[0] = False

            def slide(axis, val):
                if busy[0] or sel_id[0] is None:
                    return
                i = sel_id[0]
                p = list(self.bfx.pos(i))
                p[axis] = float(val)
                self.bfx.set_pos(i, *p)
                j = counterpart(i) if mir.get() else None
                if j is not None:
                    q = list(self.bfx.pos(j))
                    q[axis] = -p[axis] if axis == 0 else p[axis]
                    self.bfx.set_pos(j, *q)
                self.b_save.configure(state="normal")
                for k in (i, j):
                    if k is not None and tr.exists(str(k)):
                        x, y, z = self.bfx.pos(k)
                        tr.item(str(k), values=(self.bfx.nozzles[k]["muffler"], self.bfx.nozzles[k]["slot"], "%.4f" % x, "%.4f" % y, "%.4f" % z))
                draw_view(cv2, 0, 1, True, "Rear view  (right side of the car on the right)")
                draw_view(cv3, 2, 1, False, "Side view  (front to the right, flames point left)")
                msg.set("Sliding... Save to game (main window) writes it.")

            def picked(_e=None):
                s_ = tr.selection()
                sel_id[0] = int(s_[0]) if s_ else None
                sync_sliders()

            def after_change(txt):
                self.b_save.configure(state="normal")
                msg.set(txt + "  Save to game (main window) writes it.")
                show()

            def edit(ev):
                row, col = tr.identify_row(ev.y), tr.identify_column(ev.x)
                if not row or col not in ("#3", "#4", "#5"):
                    return
                k = int(col[1:]) - 3
                x, y, wd, ht = tr.bbox(row, col)
                en = ttk.Entry(tr)
                en.place(x=x, y=y, width=wd, height=ht)
                en.insert(0, tr.set(row, cols[k + 2]))
                en.focus_set()
                en.select_range(0, "end")

                def done(_e=None):
                    try:
                        v = float(en.get())
                        _check(v)
                    except (ValueError, BackfireError):
                        en.destroy()
                        return
                    i = int(row)
                    p = list(self.bfx.pos(i))
                    p[k] = v
                    self.push()
                    self.bfx.set_pos(i, *p)
                    en.destroy()
                    after_change("Outlet moved.")

                en.bind("<Return>", done)
                en.bind("<FocusOut>", done)
                en.bind("<Escape>", lambda _e: en.destroy())

            def move_all():
                try:
                    d = [float(v.get()) for v in dv]
                except ValueError:
                    return
                idx = [i for i in keys.get(cur() or "", []) if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"]
                if not idx:
                    return
                self.push()
                for i in idx:
                    p = self.bfx.pos(i)
                    self.bfx.set_pos(i, p[0] + d[0], p[1] + d[1], p[2] + d[2])
                after_change("Moved %d outlet(s) by %g %g %g." % (len(idx), *d))

            def mirror(src):
                """copy the outlets of one side to the other (x negated), slot for slot"""
                idx = {self.bfx.nozzles[i]["slot"]: i for i in keys.get(cur() or "", []) if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"}
                pairs = [(a, b) for a, b in (("MF_NULL_L1", "MF_NULL_R1"), ("MF_NULL_L2", "MF_NULL_R2")) if a in idx and b in idx]
                if not pairs:
                    msg.set("This car has no matching left / right MUFFLER1 outlets.")
                    return
                self.push()
                for l, r in pairs:
                    a, b = (l, r) if src == "L" else (r, l)
                    p = self.bfx.pos(idx[a])
                    self.bfx.set_pos(idx[b], -p[0], p[1], p[2])
                after_change("Mirrored %s -> %s." % (src, "R" if src == "L" else "L"))

            def copy_from():
                src = cv.get()
                if not src or not cur():
                    return
                a = {self.bfx.nozzles[i]["slot"]: i for i in keys[src] if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"}
                b = {self.bfx.nozzles[i]["slot"]: i for i in keys[cur()] if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"}
                both = [sl for sl in a if sl in b]
                if not both:
                    msg.set("No MUFFLER1 slot in common (%s has %s)." % (src, ", ".join(sorted(a)) or "none"))
                    return
                self.push()
                for sl in both:
                    self.bfx.set_pos(b[sl], *self.bfx.pos(a[sl]))
                after_change("Copied %d outlet(s) from %s." % (len(both), src.split("|")[0]))

            ttk.Button(ctl, text="Move", command=move_all).pack(side="left")
            ttk.Button(ctl, text="Mirror L → R", command=lambda: mirror("L")).pack(side="left", padx=4)
            ttk.Button(ctl, text="Mirror R → L", command=lambda: mirror("R")).pack(side="left")
            ttk.Button(ctl, text="Open car .wpk…", command=open_car).pack(side="right")
            ttk.Button(ctl, text="Cars folder…", command=pick_dir).pack(side="right", padx=4)
            ttk.Button(ctl, text="Render 3D again", command=lambda: (st["car"] and start_3d(st["car"], None)) or show()).pack(side="right")
            cb = ttk.Frame(mid)
            cb.pack(fill="x", pady=(6, 0))
            ttk.Label(cb, text="Copy from:").pack(side="left")
            cc2 = ttk.Combobox(cb, textvariable=cv, values=names, width=30, state="readonly")
            cc2.pack(side="left", padx=4)
            ttk.Button(cb, text="Copy", command=copy_from).pack(side="left")
            fv.trace_add("write", lambda *_a: fill_list())
            lb.bind("<<ListboxSelect>>", show)
            tr.bind("<Double-1>", edit)
            tr.bind("<<TreeviewSelect>>", picked)
            poll()
            st["dir"] = getattr(mods, "cars_dir", None) or (os.path.dirname(car_path) if car_path else None)
            fill_list()
            if car_path and os.path.isfile(car_path):
                load_car(car_path)
            else:
                msg.set("Pick the Cars folder (or Open car .wpk): the body of the selected car is drawn behind its outlets.")
                auto_car()

        # ---- saving
        def save(self):
            if not self.wpk:
                return
            try:
                a = self.wpk.save(self.wpk_path)
                bak = " (old file kept as .bak)" if a["bak"] else ""
                if self.bfx and self.bfx.changed():
                    self.bfx.save(self.bfx_path)
                    bak += " + .bfx"
            except (OSError, BackfireError) as ex:
                messagebox.showerror("Backfire flame", str(ex))
                return
            self.b_save.configure(state="disabled")
            self.status.set("Saved %s%s." % (os.path.basename(self.wpk_path), bak))

    return Tab()


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
