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
import vpk_layout as VL            # Blender-style list | viewport | panels layout
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

    def set_scale_abs(self, i, width=1.0, length=1.0):
        """the flame as it was OPENED, scaled: X / Y by width, Z by length (a slider sets this, so it never drifts the way repeated
        multiplying does).  The vertex array object stays the same, only its positions change."""
        V, O = self.flames[i]["V"], self.orig[i]
        if len(V) and len(V) == len(O):
            V["pos"] = (O["pos"] * np.array([width, width, length], np.float32)).astype(np.float32)

    def scale_factors(self, i):
        """(width, length) of flame i against the shape it was opened with (1, 1 when unchanged or flat)"""
        V, O = self.flames[i]["V"], self.orig[i]
        out = []
        for axes, col in (("w", (0, 1)), ("l", (2,))):
            a = max((float(np.abs(V["pos"][:, c]).max()) for c in col), default=0.0) if len(V) else 0.0
            b = max((float(np.abs(O["pos"][:, c]).max()) for c in col), default=0.0) if len(O) else 0.0
            out.append(a / b if b > 1e-9 else 1.0)
        return tuple(out)

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
        self.base = bytes(data)            # as opened (orig moves on every save): the animation sliders are relative to this
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

    def frame_matrix(self, i, f):
        """the 4x4 matrix of frame f of flame animation i, as a column-vector matrix (translation in the last column)"""
        return np.frombuffer(bytes(self.b[self.meshes[i]["off"] + 64 * f:self.meshes[i]["off"] + 64 * f + 64]), "<f4").reshape(4, 4).T.astype(np.float64)

    def nozzle_matrix(self, i):
        o = self.nozzles[i]["off"]
        return np.frombuffer(bytes(self.b[o:o + 64]), "<f4").reshape(4, 4).T.astype(np.float64)

    def _base_diag(self, i):
        o = self.meshes[i]["off"]
        return np.frombuffer(self.base, "<f4", count=FRAMES * 16, offset=o).reshape(FRAMES, 16)[:, (0, 5, 10)]

    def anim_factor(self, i):
        """how much the size numbers (m0, m5, m10 of every frame) were scaled against the file as opened; 1.0 when unchanged"""
        o = self.meshes[i]["off"]
        cur = np.frombuffer(bytes(self.b[o:o + FRAMES * 64]), "<f4").reshape(FRAMES, 16)[:, (0, 5, 10)]
        base = self._base_diag(i)
        k = int(np.abs(base).argmax())
        b = float(base.reshape(-1)[k])
        return float(cur.reshape(-1)[k]) / b if abs(b) > 1e-9 else 1.0

    def set_anim_factor(self, i, k):
        """the size numbers of flame animation i = the opened ones x k (the file keeps its size; only these numbers change)"""
        _check(k)
        o = self.meshes[i]["off"]
        base = self._base_diag(i)
        for f in range(FRAMES):
            for j, m in enumerate((0, 5, 10)):
                v = float(base[f, j]) * float(k)
                _check(v)
                struct.pack_into("<f", self.b, o + 64 * f + 4 * m, v)

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
def scrollable(parent, horizontal=True):
    """Put a scrollable area into `parent` and return the frame to build in. Bars show only when the content is bigger than the window;
    otherwise the frame is stretched to fill it, so wide screens look exactly as before. Mouse wheel = up / down, Shift + wheel = sideways.
    horizontal=False: no sideways bar at all, the frame is always exactly as wide as the window (what is too wide is clipped, so the page
    must be built to shrink: panes with dividers, wrapping rows)."""
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
        need_v, need_h = rh > ch, (rw > cw and horizontal)
        if need_v != bool(vs.winfo_manager()):
            vs.pack(side="right", fill="y", before=cv) if need_v else vs.pack_forget()
        if need_h != bool(hs.winfo_manager()):
            hs.pack(side="bottom", fill="x", before=cv) if need_h else hs.pack_forget()
        w, h = (max(rw, cw) if horizontal else cw), max(rh, ch)
        cv.itemconfigure(win, width=w, height=h)
        cv.configure(scrollregion=(0, 0, w, h))

    inner.bind("<Configure>", relayout)
    cv.bind("<Configure>", relayout)
    if horizontal:
        vpk_ui.FIT_PROBES.append(lambda: (inner.winfo_reqwidth(), cv.winfo_width(), cv.winfo_ismapped()))      # "fit to window" measures this

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
VIEW_BUTTONS = (("Rear", 180.0, 6.0), ("Rear 3/4", 150.0, 12.0), ("Side", 90.0, 0.0), ("Front 3/4", -35.0, 14.0), ("Top", 180.0, 89.0))
FPS = 60.0                                        # the game steps a flame one frame per tick (60 a second)
B_BODIES = (0x25, 0x26, 0x27, 0x28, 0x29, 0x2a, 0x2b, 0x2c, 0x2d, 0x32, 0x34, 0x5f, 0x60, 0x61, 0x71, 0x75, 0x76)     # src/backfire.c bfx_variant_b


def variant_for(car_id, size, override="Auto"):
    """the flame animation set the game draws on a car: 'Small' / 'Middle' pulse, and the B set on the cars of B_BODIES
    (src/app.c backfire_draw_car).  An explicit variant name wins."""
    if override and override != "Auto":
        return override
    b = car_id in B_BODIES
    if size == "Small":
        return "SMALL_B" if b else "SAMLL_A"            # (sic: the game's own spelling of the A set's small flame)
    return "MIDDLE_B" if b else "MIDDLE_A"


def instances_for(bfx, wpk, key, variant, frame, show_all=False):
    """[(flame index in the .wpk, 4x4 model matrix)] for the flames the game draws on car `key` at animation frame `frame` (None = flame off):
    for every MUFFLER1 outlet  car_space x outlet matrix x 0.5 x frame matrix  (src/app.c backfire_draw_car)."""
    if bfx is None or wpk is None or frame is None or not (0 <= frame < FRAMES):
        return []
    by_tag = {f["tag"]: i for i, f in enumerate(wpk.flames)}
    metas = [(mi, by_tag[m["group"]]) for mi, m in enumerate(bfx.meshes) if m["variant"] == variant and m["group"] in by_tag]
    S = np.diag([0.5, 0.5, 0.5, 1.0])
    out = []
    for ni in bfx.car_keys().get(key, []):
        n = bfx.nozzles[ni]
        if n["muffler"] != "MUFFLER1":
            continue
        base = bfx.nozzle_matrix(ni) @ S
        for mi, fi in metas:
            out.append((fi, base @ bfx.frame_matrix(mi, frame)))
    return out


def build_tab(tab_parent, root, app=None, host=None):
    import time
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from PIL import ImageTk

    try:
        import wpk_view
    except ImportError:
        wpk_view = None
    try:
        import wpk_browser as WB
    except ImportError:
        WB = None
    try:
        import gl_viewport as GLV
        GLV_WHY = GLV.import_error()
    except Exception as ex:                                  # no moderngl / OpenGL on this machine: the flat flame drawing stays
        GLV, GLV_WHY = None, "gl_viewport.py could not be loaded (%s)" % ex
    host = host or getattr(app, "models", None)       # the Models page: owns the car and the one shared viewport
    TEX_ = 190

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
            # car
            self.names = []                     # every car key of the .bfx ("cars/maz_FD3S|12")
            self.active = False
            self.sel_noz = None                 # index of the outlet being slid
            self.noz_rows = []
            # viewport + playback
            self.layer = None                   # the flame layer in the shared viewport
            self.frame = None                   # animation frame shown now (None = no flame)
            self.clock = 0.0
            self.last = time.perf_counter()
            self.once = False
            self._sync = False
            self._drag = None
            self._tick_err = False
            self.playing = tk.BooleanVar(value=True)
            self.speed = tk.DoubleVar(value=1.0)
            self.gap = tk.IntVar(value=24)
            self.size = tk.StringVar(value="Middle")
            self.variant = tk.StringVar(value="Auto")
            self.show_car = tk.BooleanVar(value=True)
            self.hide_wing = tk.BooleanVar(value=True)
            self.markers = tk.BooleanVar(value=True)
            self.autorot = tk.BooleanVar(value=False)
            self.rot_speed = tk.DoubleVar(value=24.0)
            self.gain = tk.DoubleVar(value=1.0)
            self.scope = tk.StringVar(value="sel")
            self.sw, self.sl, self.sa = tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)
            self.mir = tk.IntVar(value=1)
            self.scrub = tk.DoubleVar(value=0.0)
            self.build()
            host.register_module("backfire", self)
            self.autoload()
            root.after(300, self.tick)

        # the car and the viewport belong to the Models page
        car_path = property(lambda s: host.path)
        gl = property(lambda s: host.gl)
        cam = property(lambda s: host.cam)

        # ---- layout
        def build(self):
            # panels only: the viewport is the page's.  Left: the flames + outlets; bar under the viewport: views / play / options / save;
            # right: folding adjustment panels.
            left = host.module_left("backfire")
            bar_c = host.module_bar("backfire")
            ws = self.ws = VL.Slot(left, host.module_right("backfire"), bar_c)

            # ---------------- left: file, flames, outlets
            top = ttk.Frame(left)
            top.pack(fill="x")
            ttk.Label(top, text="BACKFIRE", font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=(0, 8))
            ttk.Button(top, text="Open backfire.wpk…", command=self.open_dialog).pack(side="left")
            ttk.Button(top, text="Help", command=self.help).pack(side="left", padx=4)
            ws.flow(top, left)
            self.name_lbl = ttk.Label(left, text="", foreground="#8f9eb2", wraplength=240, justify="left")
            self.name_lbl.pack(anchor="w", pady=(2, 6))
            ttk.Label(left, text="FLAMES", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
            cols = ("n", "name", "tris")
            self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended", height=6)
            for c, t, w, a in (("n", "#", 24, "e"), ("name", "Flame (from the .bfx)", 130, "w"), ("tris", "Tris", 36, "e")):
                self.tree.heading(c, text=t)
                self.tree.column(c, width=w, anchor=a, stretch=(c == "name"))
            self.tree.pack(fill="x")
            self.tree.bind("<<TreeviewSelect>>", lambda _e: self.flame_selected())
            ttk.Label(left, text="CAR", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(10, 2))
            self.car_lbl = ttk.Label(left, text="", foreground="#8f9eb2", wraplength=240, justify="left")
            self.car_lbl.pack(anchor="w")
            ttk.Label(left, text="OUTLETS  (click one, or its cross in the picture)", font=("TkDefaultFont", 9, "bold"),
                      wraplength=240, justify="left").pack(anchor="w", pady=(8, 2))
            self.otree = ttk.Treeview(left, columns=("slot", "x", "y", "z"), show="headings", selectmode="browse", height=4)
            for c, t, w in (("slot", "Slot", 54), ("x", "x", 56), ("y", "y", 56), ("z", "z", 56)):
                self.otree.heading(c, text=t)
                self.otree.column(c, width=w, anchor="w" if c == "slot" else "e", stretch=(c == "slot"))
            self.otree.pack(fill="x")
            self.otree.bind("<<TreeviewSelect>>", lambda _e: self.outlet_selected())

            # ---------------- under the viewport
            center = bar_c
            pb = ttk.Frame(center)
            pb.pack(fill="x")
            self.b_play = ttk.Button(pb, text="Pause", width=7, command=self.toggle_play)
            self.b_play.pack(side="left")
            ttk.Button(pb, text="Fire once", command=self.fire_once).pack(side="left", padx=4)
            self.frame_lbl = ttk.Label(pb, text="", width=12, foreground="#8f9eb2")
            self.frame_lbl.pack(side="right")
            sc = ttk.Scale(pb, from_=0, to=FRAMES - 1, variable=self.scrub, orient="horizontal", command=self.scrubbed)
            sc.pack(side="left", fill="x", expand=True, padx=6)
            sc.bind("<ButtonPress-1>", lambda _e: self.pause())
            ob = ttk.Frame(center)
            ob.pack(fill="x", pady=(6, 0))
            for txt, var, cmd in (("Show car", self.show_car, self.apply_visibility), ("Hide wing", self.hide_wing, self.apply_visibility),
                                  ("Outlet crosses", self.markers, self.refresh_instances), ("Auto-rotate", self.autorot, None)):
                ttk.Checkbutton(ob, text=txt, variable=var, command=cmd).pack(side="left", padx=(0, 10))
            ws.flow(ob, center)
            fb = ttk.Frame(center)
            fb.pack(fill="x", pady=(6, 0))
            self.b_undo = ttk.Button(fb, text="Undo flames", command=self.do_undo, state="disabled")
            self.b_undo.pack(side="left")
            self.b_save = ttk.Button(fb, text="Save backfire to game", command=self.save, state="disabled")
            self.b_save.pack(side="left", padx=4)
            ws.flow(fb, center)
            self.gl_info = ttk.Label(center, text="", foreground="#8f9eb2", wraplength=420, justify="left")
            self.gl_info.pack(fill="x", pady=(4, 0))
            self.status.bar(center, ttk, tk, wraplength=420, fill="x", pady=(3, 0))

            # ---------------- right: the adjustments
            right = ws.props.body
            ps = ws.section("Flame shape  (live)").body
            r0 = ttk.Frame(ps)
            r0.pack(fill="x")
            ttk.Radiobutton(r0, text="Selected flame(s)", variable=self.scope, value="sel", command=self.sync_shape).pack(side="left")
            ttk.Radiobutton(r0, text="All 17", variable=self.scope, value="all", command=self.sync_shape).pack(side="left", padx=(8, 0))
            ws.flow(r0)
            self.s_w = self.slider(ps, "Width ×", self.sw, 0.2, 3.0, self.shape_press, self.shape_move)
            self.s_l = self.slider(ps, "Length ×", self.sl, 0.2, 3.0, self.shape_press, self.shape_move)
            self.s_a = self.slider(ps, "Anim size ×", self.sa, 0.2, 3.0, self.shape_press, self.anim_move)
            rb = ttk.Frame(ps)
            rb.pack(fill="x", pady=(4, 0))
            self.b_rst = ttk.Button(rb, text="Reset to opened", command=self.reset_sel, state="disabled")
            self.b_rst.pack(side="left")
            self.b_keys = ttk.Button(rb, text="Edit animation…", command=self.edit_keys, state="disabled")
            self.b_keys.pack(side="left", padx=4)
            ws.flow(rb)
            po = ws.section("Outlet position  (live)").body
            self.o_sl = []
            for axis, lab in enumerate("xyz"):
                var = tk.DoubleVar(value=0.0)
                row = self.slider(po, lab + "  (units)", var, -1.0, 1.0, self.outlet_press, lambda v, a=axis: self.outlet_move(a, v), fmt="%.2f")
                self.o_sl.append((var, row))
            ttk.Checkbutton(po, text="Mirror left / right", variable=self.mir).pack(anchor="w")
            mb = ttk.Frame(po)
            mb.pack(fill="x", pady=(4, 0))
            ttk.Button(mb, text="L → R", width=7, command=lambda: self.mirror("L")).pack(side="left")
            ttk.Button(mb, text="R → L", width=7, command=lambda: self.mirror("R")).pack(side="left", padx=4)
            ttk.Button(mb, text="Copy from…", command=self.copy_from).pack(side="left")
            ws.flow(mb)
            pp = ws.section("Playback").body
            self.slider(pp, "Speed ×", self.speed, 0.05, 2.0, None, None)
            self.slider(pp, "Pause (frames)", self.gap, 0, 120, None, None, fmt="%.0f")
            self.slider(pp, "Brightness", self.gain, 0.2, 2.5, None, lambda v: self.gl and self.gl.request_draw())
            self.slider(pp, "Spin °/s", self.rot_speed, -90, 90, None, None, fmt="%.0f")
            rs = ttk.Frame(pp)
            rs.pack(fill="x", pady=(4, 0))
            ttk.Radiobutton(rs, text="Small", variable=self.size, value="Small", command=self.size_chosen).pack(side="left")
            ttk.Radiobutton(rs, text="Middle", variable=self.size, value="Middle", command=self.size_chosen).pack(side="left", padx=6)
            ttk.Label(pp, text="Flame set").pack(anchor="w", pady=(4, 0))
            self.var_box = ttk.Combobox(pp, textvariable=self.variant, values=["Auto"], state="readonly", width=16)
            self.var_box.pack(fill="x")
            self.var_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_instances())
            pt = ws.section("Texture").body
            self.tex_box = ttk.Combobox(pt, textvariable=self.texvar, state="readonly", width=20)
            self.tex_box.pack(fill="x")
            self.tex_box.bind("<<ComboboxSelected>>", lambda _e: self.render())
            self.tcanvas = tk.Canvas(pt, width=TEX_, height=TEX_, background="#1d2230", highlightthickness=0)
            self.tcanvas.pack(pady=(4, 0))
            tb = ttk.Frame(pt)
            tb.pack(fill="x", pady=(4, 0))
            self.b_texout = ttk.Button(tb, text="Export PNG…", command=self.export_tex, state="disabled")
            self.b_texout.pack(side="left", fill="x", expand=True)
            self.b_texin = ttk.Button(tb, text="Replace…", command=self.replace_tex, state="disabled")
            self.b_texin.pack(side="left", fill="x", expand=True, padx=(4, 0))
            self.info = ttk.Label(right, text="", foreground="#8f9eb2", wraplength=330, justify="left")
            self.info.pack(anchor="w", pady=(6, 0))

        def slider(self, parent, text, var, lo, hi, press, move, fmt="%.2f"):
            """label + live ttk.Scale + value; press() runs once when a drag starts (Undo point), move(value) on every change"""
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=1)
            row.columnconfigure(1, weight=1)
            ttk.Label(row, text=text, width=12).grid(row=0, column=0, sticky="w")
            sc = ttk.Scale(row, from_=lo, to=hi, variable=var, orient="horizontal",
                           command=(lambda v: None if self._sync or move is None else move(float(v))))
            sc.grid(row=0, column=1, sticky="ew", padx=4)
            val = ttk.Label(row, width=5, anchor="e")
            val.grid(row=0, column=2)

            def shown(*_a):
                try:
                    val.configure(text=fmt % float(var.get()))
                except (tk.TclError, ValueError):
                    pass
            var.trace_add("write", shown)
            shown()
            if press:
                sc.bind("<ButtonPress-1>", press, add="+")
                sc.bind("<KeyPress>", press, add="+")
            row.scale = sc
            return row

        def set_slider(self, row, var, value, lo=None, hi=None):
            self._sync = True
            try:
                if lo is not None:
                    row.scale.configure(from_=lo, to=hi)
                var.set(value)
            finally:
                self._sync = False

        def help(self):
            w = tk.Toplevel(root)
            w.title("Backfire flame - how it works")
            t = tk.Text(w, wrap="word", width=92, height=26, padx=10, pady=8)
            t.insert("1.0", __doc__ + HELP_3D)
            t.configure(state="disabled")
            t.pack(fill="both", expand=True)

        # ---- the shared viewport (the Models page calls these)
        def on_enter(self):
            self.active = True
            self.last = time.perf_counter()
            if self.gl is None:
                self.gl_info.configure(text="Flames and outlet crosses are drawn in the GPU viewport: switch it on in Parts & viewport (GPU viewport).")
                return
            self.gl_info.configure(text="")
            if self.layer is None:
                self.layer = GLV.FlameLayer()
            if self.layer not in self.gl.extras:
                self.gl.extras.append(self.layer)
            self.refresh_assets()
            self.after_car_change()
            self.gl.set_focus(None)                             # the whole car, framed exactly as in Parts; "Zoom to exhaust" goes close

        def on_leave(self):
            self.active = False
            gl = self.gl
            if gl is not None and self.layer in gl.extras:
                gl.extras.remove(self.layer)

        def pack_changed(self, kind):
            """a different car (or the same one edited) in Parts: the outlets follow it"""
            if kind == "open":
                self.after_car_change()
                if self.active and self.gl is not None:
                    self.gl.set_focus(None)

        def shown_tags(self, base):
            if not self.show_car.get():
                return set()
            wing = {c.tag for c in host.pack.chunks if WB and WB.category(c.tag) == "Wing"} if host.pack else set()
            return {t for t in base if not (self.hide_wing.get() and t in wing)}

        def selection_tags(self):
            return []

        def viewport_changed(self):
            self.refresh_instances()

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
                self.cam.orbit(e.x - d["x"], e.y - d["y"])           # a full turn on both axes: nothing stops the orbit
            else:
                d["moved"] = True
                self.cam.pan_px(e.x - d["x"], e.y - d["y"], self.gl.upp)
            d["x"], d["y"] = e.x, e.y
            self.gl.request_draw()

        def vp_release(self, e):
            d, self._drag = self._drag, None
            if d and not d["moved"] and d["btn"] == 1:
                self.pick_outlet(e.x, e.y)

        def vp_wheel(self, e):
            if self.gl is not None:
                up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
                self.cam.zoom_by(1.2 if up else 1 / 1.2)
                self.gl.request_draw()
            return "break"

        def set_view(self, yaw, pitch):
            if self.cam is None:
                return
            self.cam.set_angles(yaw, pitch)
            self.cam.fit()
            if self.gl is not None:
                self.gl.request_draw()

        def fit_view(self):
            if self.cam is None:
                return
            self.cam.fit()
            if self.gl is not None:
                self.gl.set_focus(None)
                self.gl.request_draw()

        def outlet_points(self):
            return [np.array(self.bfx.pos(i), np.float64) for i in self.key_nozzles() if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"] if self.bfx else []

        def pick_outlet(self, x, y):
            """a click on an outlet's cross selects it"""
            r = getattr(self.gl, "r", None)
            last = getattr(r, "last", None)
            if not last or not self.bfx:
                return
            view, best, bd = last[1], None, 22.0 ** 2
            for i in self.key_nozzles():
                if self.bfx.nozzles[i]["muffler"] != "MUFFLER1":
                    continue
                px, py = view.project(self.bfx.pos(i))
                dd = (px - x) ** 2 + (py - y) ** 2
                if dd < bd:
                    best, bd = i, dd
            if best is not None and self.otree.exists(str(best)):
                self.otree.selection_set(str(best))

        # ---- loading
        def pref(self, k):
            try:
                return wpk_view.load_prefs().get(k) if wpk_view else None
            except Exception:
                return None

        def setpref(self, **kv):
            try:
                if wpk_view:
                    wpk_view.save_prefs(**kv)
            except Exception:
                pass

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
            self.setpref(backfire_last=path)
            self.name_lbl.configure(text=os.path.basename(path) + (" + " + os.path.basename(other) if other else "  (no .bfx beside it: animation and outlets off)"))
            self.tex_box.configure(values=["%d  %s  %dx%d" % (i, t["name"], t["w"], t["h"]) for i, t in enumerate(w.tex)])
            self.texvar.set(self.tex_box.cget("values")[0])
            for s in (self.b_texout, self.b_texin, self.b_rst):
                s.configure(state="normal")
            self.b_keys.configure(state="normal" if b else "disabled")
            self.names = sorted(b.car_keys(), key=str.lower) if b else []
            self.var_box.configure(values=["Auto"] + sorted({m["variant"] for m in b.meshes}) if b else ["Auto"])
            self.variant.set("Auto")
            self.fill()
            self.refresh_assets()
            self.after_car_change()
            self.status.set("Opened %s." % os.path.basename(path))

        def fill(self, keep=None):
            sel = keep if keep is not None else [int(i) for i in self.tree.selection()]
            self.tree.delete(*self.tree.get_children())
            for i, f in enumerate(self.wpk.flames):
                m = self.mesh_of(i)
                nm = "%s  %s" % (m["variant"], m["path"]) if m else "tag %d" % f["tag"]
                self.tree.insert("", "end", iid=str(i), values=(i + 1, nm, len(f["I"]) // 3))
            ids = [str(i) for i in sel if 0 <= i < len(self.wpk.flames)] or ["0"]
            self.tree.selection_set(ids)
            self.render()
            self.sync_shape()

        def selected(self):
            return [int(i) for i in self.tree.selection()] or []

        def mesh_of(self, i):
            """the .bfx flame animation that belongs to the i-th flame of the .wpk (same group number)"""
            if not self.bfx:
                return None
            tag = self.wpk.flames[i]["tag"]
            return next((m for m in self.bfx.meshes if m["group"] == tag), None)

        def size_chosen(self):
            self.variant.set("Auto")                                  # Small / Middle choose the set again (the game's own A / B rule)
            self.refresh_instances()

        def flame_selected(self):
            """picking a flame shows its own animation set, so what you slide is what you see"""
            sel = self.selected()
            m = self.mesh_of(sel[0]) if sel else None
            if m and self.bfx and m["variant"] != self.current_variant():
                self.variant.set(m["variant"])
            self.render()
            self.sync_shape()
            self.refresh_instances()

        # ---- the car
        def fill_cars(self):
            pass

        def car_key(self):
            """the .bfx car key (with |id) of the car open in Parts"""
            if self.car_path:
                body = car_key_of(self.car_path)
                return next((k for k in self.names if k.split("|")[0] == body), None)
            return None

        def key_nozzles(self):
            return self.bfx.car_keys().get(self.car_key() or "", []) if self.bfx else []

        def apply_visibility(self):
            if host.pack:
                host.render(reuse=True)

        def after_car_change(self):
            k = self.car_key()
            if self.car_path:
                self.car_lbl.configure(text="%s (the car open in Parts)%s" % (os.path.basename(self.car_path), "" if k or not self.bfx else
                                       "\nThis backfire.bfx has no outlets for this car: the flames show nothing here."))
            else:
                self.car_lbl.configure(text="Open a car in Parts & viewport: the outlets are lined up on it.")
            self.fill_outlets()
            self.refresh_instances()

        # ---- the outlets
        def fill_outlets(self):
            keep = self.sel_noz
            self.otree.delete(*self.otree.get_children())
            for i in self.key_nozzles():
                n = self.bfx.nozzles[i]
                x, y, z = self.bfx.pos(i)
                self.otree.insert("", "end", iid=str(i), values=(n["slot"][-2:] + ("" if n["muffler"] == "MUFFLER1" else " (off)"), "%.3f" % x, "%.3f" % y, "%.3f" % z))
            if keep is not None and self.otree.exists(str(keep)):
                self.otree.selection_set(str(keep))
            else:
                self.sel_noz = None
                kids = self.otree.get_children()
                if kids:
                    self.otree.selection_set(kids[0])
            self.sync_outlet()

        def outlet_selected(self):
            s_ = self.otree.selection()
            self.sel_noz = int(s_[0]) if s_ else None
            self.sync_outlet()
            self.refresh_instances()

        def sync_outlet(self):
            for a, (var, row) in enumerate(self.o_sl):
                if self.sel_noz is None or not self.bfx:
                    self.set_slider(row, var, 0.0)
                else:
                    v = self.bfx.pos(self.sel_noz)[a]
                    self.set_slider(row, var, v, v - 6, v + 6)

        def outlet_press(self, _e=None):
            if self.sel_noz is not None:
                self.push()

        def counterpart(self, i):
            n = self.bfx.nozzles[i]
            sl = n["slot"]
            if len(sl) < 10 or sl[8] not in "LR":
                return None
            want = sl[:8] + ("R" if sl[8] == "L" else "L") + sl[9:]
            return next((j for j in self.bfx.car_keys().get(n["car"], []) if self.bfx.nozzles[j]["muffler"] == n["muffler"] and self.bfx.nozzles[j]["slot"] == want), None)

        def outlet_move(self, axis, val):
            i = self.sel_noz
            if i is None or not self.bfx:
                return
            p = list(self.bfx.pos(i))
            p[axis] = float(val)
            self.bfx.set_pos(i, *p)
            j = self.counterpart(i) if self.mir.get() else None
            if j is not None:
                q = list(self.bfx.pos(j))
                q[axis] = -p[axis] if axis == 0 else p[axis]
                self.bfx.set_pos(j, *q)
            self.b_save.configure(state="normal")
            for k in (i, j):
                if k is not None and self.otree.exists(str(k)):
                    x, y, z = self.bfx.pos(k)
                    n = self.bfx.nozzles[k]
                    self.otree.item(str(k), values=(n["slot"][-2:] + ("" if n["muffler"] == "MUFFLER1" else " (off)"), "%.3f" % x, "%.3f" % y, "%.3f" % z))
            self.refresh_instances()
            self.status.set("Sliding an outlet.  Save to game writes it.")

        def mirror(self, src):
            if not self.bfx:
                return
            idx = {self.bfx.nozzles[i]["slot"]: i for i in self.key_nozzles() if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"}
            pairs = [(a, b) for a, b in (("MF_NULL_L1", "MF_NULL_R1"), ("MF_NULL_L2", "MF_NULL_R2")) if a in idx and b in idx]
            if not pairs:
                self.status.set("This car has no matching left / right outlets.")
                return
            self.push()
            for l, r in pairs:
                a, b = (l, r) if src == "L" else (r, l)
                p = self.bfx.pos(idx[a])
                self.bfx.set_pos(idx[b], -p[0], p[1], p[2])
            self.fill_outlets()
            self.refresh_instances()
            self.status.set("Mirrored %s to %s.  Save to game writes it." % (src, "R" if src == "L" else "L"))

        def copy_from(self):
            if not self.bfx or not self.car_key():
                return
            w = tk.Toplevel(root)
            w.title("Copy the outlets of another car")
            ttk.Label(w, text="Copy every MUFFLER1 outlet from this car to the selected one:", padding=8).pack(anchor="w")
            cv = tk.StringVar()
            cbx = ttk.Combobox(w, textvariable=cv, values=self.names, width=34, state="readonly")
            cbx.pack(padx=8)

            def go():
                src, dst = cv.get(), self.car_key()
                if not src or not dst:
                    return
                keys = self.bfx.car_keys()
                a = {self.bfx.nozzles[i]["slot"]: i for i in keys[src] if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"}
                b = {self.bfx.nozzles[i]["slot"]: i for i in keys[dst] if self.bfx.nozzles[i]["muffler"] == "MUFFLER1"}
                both = [sl for sl in a if sl in b]
                if not both:
                    self.status.set("No outlet slot in common with %s." % src.split("|")[0])
                    w.destroy()
                    return
                self.push()
                for sl in both:
                    self.bfx.set_pos(b[sl], *self.bfx.pos(a[sl]))
                w.destroy()
                self.fill_outlets()
                self.refresh_instances()
                self.status.set("Copied %d outlet(s) from %s.  Save to game writes it." % (len(both), src.split("|")[0]))
            ttk.Button(w, text="Copy", command=go).pack(pady=8)

        # ---- what the viewport draws
        def refresh_assets(self):
            """the flame meshes and textures (after opening, Undo, a texture change)"""
            if self.layer is None or not self.wpk:
                return
            meshes = {i: dict(V=f["V"], I=f["I"], tex=f["tex"], blend=f["flags"] & 3) for i, f in enumerate(self.wpk.flames)}
            self.layer.set_assets(meshes, [np.asarray(decode_tex(t)) for t in self.wpk.tex])
            self.refresh_instances()

        def current_variant(self):
            k = self.car_key()
            try:
                cid = int(k.rpartition("|")[2]) if k else -1
            except ValueError:
                cid = -1
            return variant_for(cid, self.size.get(), self.variant.get())

        def refresh_instances(self, _e=None):
            if self.layer is None or not self.wpk:
                return
            k = self.car_key()
            self.layer.gain = float(self.gain.get())
            self.layer.instances = instances_for(self.bfx, self.wpk, k, self.current_variant(), self.frame) if k else []
            marks = []
            if self.markers.get() and self.bfx and k:
                for i in self.key_nozzles():
                    if self.bfx.nozzles[i]["muffler"] == "MUFFLER1":
                        marks.append((self.bfx.pos(i), 0.9 if i == self.sel_noz else 0.6, (1.0, 0.9, 0.1) if i == self.sel_noz else (0.0, 0.9, 1.0)))
            self.layer.markers = marks
            if self.gl is not None:
                self.gl.request_draw()

        # ---- playing
        def toggle_play(self):
            if self.playing.get():
                self.pause()
            else:
                self.playing.set(True)
                self.once = False
                self.b_play.configure(text="Pause")

        def pause(self):
            self.playing.set(False)
            self.b_play.configure(text="Play")

        def fire_once(self):
            self.once = True
            self.clock = 0.0
            self.playing.set(True)
            self.b_play.configure(text="Pause")
            self.set_frame(0)

        def scrubbed(self, v):
            if self._sync or self.playing.get():
                return
            self.set_frame(int(round(float(v))))

        def set_frame(self, f):
            if f != self.frame:
                self.frame = f
                self.frame_lbl.configure(text="frame %d / %d" % (f, FRAMES - 1) if f is not None else "flame off")
                self.refresh_instances()

        def tick(self):
            delay = 100
            try:
                shown = self.active and self.gl is not None and self.wpk is not None
                now = time.perf_counter()
                dt = min(now - self.last, 0.1)
                self.last = now
                if shown:
                    if self.autorot.get() and self.cam is not None:
                        self.cam.yaw = (self.cam.yaw + dt * float(self.rot_speed.get()) + 180.0) % 360.0 - 180.0
                        self.gl.request_draw()
                        delay = 16
                    if self.playing.get():
                        self.clock += dt * FPS * float(self.speed.get())
                        cycle = FRAMES + int(self.gap.get())
                        pos = self.clock % cycle
                        if self.once and self.clock >= FRAMES:
                            self.once = False
                            self.pause()
                            self.set_frame(None)
                        else:
                            f = int(pos) if pos < FRAMES else None
                            self.set_frame(f)
                            if f is not None:
                                self._sync = True
                                self.scrub.set(f)
                                self._sync = False
                        delay = 16
            except Exception:
                if not self._tick_err:                             # say it once; keep the timer alive
                    self._tick_err = True
                    import traceback
                    self.status.set("Cannot play the flame: %s" % traceback.format_exc().strip().splitlines()[-1])
            try:
                root.after(delay, self.tick)
            except tk.TclError:
                pass

        # ---- drawing (the flat fallback, and the texture picture)
        def render(self):
            if not self.wpk:
                return
            sel = self.selected()
            self.draw_tex(sel)
            if sel and self.mesh_of(sel[0]):
                r = self.mesh_of(sel[0])
                lo, hi = self.wpk.bbox(self.wpk.flames[sel[0]])
                self.info.configure(text="%s  %s\nlength %.1f  width %.2f" % (r["variant"], r["path"], hi[2] - lo[2], hi[0] - lo[0]))

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
            sc = TEX_ / max(t["w"], t["h"])
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
            self.refresh_assets()
            self.fill_outlets()
            self.status.set("Undone.")

        def targets(self):
            return list(range(len(self.wpk.flames))) if self.scope.get() == "all" else self.selected()

        def sync_shape(self):
            """the shape sliders show the first target flame's size against the shape the file was opened with"""
            if not self.wpk:
                return
            idx = self.targets()
            w, l = self.wpk.scale_factors(idx[0]) if idx else (1.0, 1.0)
            self.set_slider(self.s_w, self.sw, w)
            self.set_slider(self.s_l, self.sl, l)
            mi = None
            if idx and self.bfx and self.mesh_of(idx[0]):
                mi = self.bfx.meshes.index(self.mesh_of(idx[0]))
            self.set_slider(self.s_a, self.sa, self.bfx.anim_factor(mi) if mi is not None else 1.0)

        def shape_press(self, _e=None):
            if self.wpk:
                self.push()

        def shape_move(self, _v=None):
            if not self.wpk:
                return
            w, l = float(self.sw.get()), float(self.sl.get())
            for i in self.targets():
                self.wpk.set_scale_abs(i, w, l)
                if self.layer is not None:
                    self.layer.update_mesh(i, self.wpk.flames[i]["V"])
            self.b_save.configure(state="normal")
            if self.gl is not None:
                self.gl.request_draw()
            else:
                self.render()
            self.status.set("Flame width × %.2f, length × %.2f.  Save to game writes it." % (w, l))

        def anim_move(self, _v=None):
            if not self.bfx:
                return
            k = float(self.sa.get())
            for i in self.targets():
                m = self.mesh_of(i)
                if m:
                    self.bfx.set_anim_factor(self.bfx.meshes.index(m), k)
            self.b_save.configure(state="normal")
            self.refresh_instances()
            self.status.set("Animation size × %.2f.  Save to game writes it." % k)

        def reset_sel(self):
            idx = self.targets()
            if not idx:
                return
            self.push()
            for i in idx:
                self.wpk.reset_flame(i)
                if self.bfx and self.mesh_of(i):
                    self.bfx.set_anim_factor(self.bfx.meshes.index(self.mesh_of(i)), 1.0)
            self.fill(keep=self.selected())
            self.refresh_assets()
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
            self.refresh_assets()
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
                              "Double-click a number to change it (the viewport plays it at once). The file size never changes. Save to game writes it.", padding=8, justify="left").pack(anchor="w")
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
                    self.refresh_instances()
                    self.status.set("Animation of flame %d changed.  Save to game to keep it." % (i + 1))

                en.bind("<Return>", done)
                en.bind("<FocusOut>", done)
                en.bind("<Escape>", lambda _e: en.destroy())

            tr.bind("<Double-1>", edit)

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


HELP_3D = """

3D view and live play (this tab)
  The car you pick in the CAR list is drawn with the same GPU viewport as the Vehicle Models page (open its .wpk, or point at the Cars folder once).
  Flames are drawn the way the game does: for every MUFFLER1 outlet, car space x outlet matrix x 0.5 x the animation frame matrix.
  The game steps a flame one frame per tick (60 a second) from frame 0 to 16 and then it is gone until the next pop: Play loops that, with a
  pause between pops; Fire once plays a single pop; the frame slider scrubs it by hand (it pauses playing).
  Small / Middle and the A / B set follow the game (B-set cars use SMALL_B / MIDDLE_B); "Flame set" lets you look at any of them.
  Drag = orbit a full 360 degrees on both axes, right-drag / Shift-drag = pan, wheel = zoom, double-click = fit.  Auto-rotate turns the view by itself.
  Sliders change the picture while you drag, and the numbers the same way as before: Width / Length scale the selected flame (or all 17) against
  the shape it was opened with; Anim size scales the size numbers of its animation; the outlet sliders move the selected outlet (and its mirror).
  Colours: cyan cross = an outlet the game draws, yellow = the selected one.  Click a cross to select it.
  The flames' blend mode is read from the file (additive or alpha); how the game's own backfire shader mixes them was not checked on a device.
"""


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
