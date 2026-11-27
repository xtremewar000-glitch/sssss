"""
lamp_edit.py  -  edit the headlights and tail lights of a car .wpk: where they are, how big and what shape, how many.

A car pack keeps its lamps in a few small parts (chunks), each drawn only in one lighting situation:

    0x110  garage / menus (lamps off)        0x111  race, lamps on (bulbs, reflectors, front glow)
    0x112  race, tail glow (running lights)  0x113  race, braking (stop glow, usually bigger / brighter)
    0x114  "other" lamps (only some packs; the packs seen so far do not have it)
    0x141-0x17f  high-mount / wing brake lamps (race, braking only, and only for the wings whose bit is set in the number)

Every connected piece of a part is one "lamp" here (a bulb, the outline of a tail lamp, the high-mount stop lamp ...).
This module has no window code in its first half, so it can be tested and scripted:

    python lamp_edit.py list CAR.wpk                      the lamps of a car
    python lamp_edit.py selftest CAR.wpk                  edit a copy in memory and check the pack stays valid
    python lamp_edit.py apply SRC.wpk DST.wpk [DST2 ...]  replace the lamp parts of the DST packs by SRC's (old files kept as .bak)
    python lamp_edit.py kits SRC.wpk                      the same, to the car's other kits (_aeroA / B / C, the stock pack) beside it

The second half is the app's "Lamps" tab (build_tab).
"""
import copy
import os
import sys

import numpy as np

import wpk_edit as we

try:
    import wpk_view
except ImportError:
    wpk_view = None

LAMP_TAGS = (0x110, 0x111, 0x112, 0x113, 0x114)
HIGH_BRAKE = range(0x141, 0x180)                # the engine's high-mount / wing brake lamps: drawn only while braking, for the wings whose bit is set
LINKED = {0x112: (0x113,), 0x113: (0x112,), 0x110: (0x111,), 0x111: (0x110,)}   # a tail lamp is in two parts, a bulb in two
STATE_NAMES = {0: "Lens (clear glass over a lamp)", 0x110: "Garage / menus (0x110)", 0x111: "Race, lamps on (0x111)", 0x112: "Race, tail glow (0x112)",
               0x113: "Braking, stop glow (0x113)", 0x114: "Other lamps (0x114)"}
SHORT = {0: "lens", 0x110: "garage", 0x111: "lamps on", 0x112: "tail glow", 0x113: "stop glow", 0x114: "other"}
MAX_VERTS = 65535                      # a batch indexes its vertices with 16 bits

FACINGS = ("Front", "Rear", "Left side", "Right side")
SHAPES = ("Rectangle", "Round", "Ring")
TINTS = {"White": (255, 255, 255), "Warm white": (255, 238, 200), "Red": (255, 40, 30), "Amber": (255, 150, 20),
         "Blue-white": (200, 225, 255), "Green": (60, 255, 90)}
ROLES = {                                       # name -> (parts it goes into, facing, shape, tint, w, h)
    "Headlamp": ((0x111, 0x110), "Front", "Round", "Warm white", 1.2, 1.2),
    "Tail lamp": ((0x112, 0x113), "Rear", "Rectangle", "Red", 2.0, 0.5),
    "Brake lamp (braking only)": ((0x113,), "Rear", "Rectangle", "Red", 1.0, 0.4),
    "Indicator / side marker": ((0x111,), "Front", "Rectangle", "Amber", 0.8, 0.3),
    "Reverse lamp": ((0x112, 0x113), "Rear", "Rectangle", "White", 0.7, 0.3),
}


import vpk_ui as UI                       # shared confirmations / look (Milestone 6)
import vpk_layout as VL                   # Blender-style list | viewport | panels layout


def is_lamp_tag(t):
    return t in LAMP_TAGS or t in HIGH_BRAKE


def is_edit_tag(t):
    """the body (for the lens pieces) and every lamp part: what undo keeps"""
    return t == 0 or is_lamp_tag(t)


def state_name(t):
    if t in STATE_NAMES:
        return STATE_NAMES[t]
    if t in HIGH_BRAKE:
        return "High brake lamp, wings %s (%s)" % (_wings(t), hex(t))
    return hex(t)


def short_name(t):
    if t in SHORT:
        return SHORT[t]
    return "hi-brake" if t in HIGH_BRAKE else hex(t)


def _wings(t):
    w = [str(i) for i in range(5) if t >> i & 1]
    return ", ".join(w) if w else "none"


class LampError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ finding lamps
class Lamp:
    """one connected piece of a lamp part: its triangles (rows of the batch) and where it sits"""
    __slots__ = ("tag", "bi", "rows", "lo", "hi", "key")

    def __init__(self, tag, bi, rows, lo, hi, key):
        self.tag, self.bi, self.rows, self.lo, self.hi, self.key = tag, bi, rows, lo, hi, key

    @property
    def center(self):
        return (self.lo + self.hi) / 2

    @property
    def size(self):
        return self.hi - self.lo

    @property
    def side(self):
        x = float(self.center[0])
        return "C" if abs(x) < 0.35 else ("L" if x < 0 else "R")

    @property
    def ntri(self):
        return len(self.rows)


def _islands(b):
    """connected pieces of a batch, numbered by their first triangle (so a lamp keeps its number while it is moved)"""
    T = b.I.reshape(-1, 3).astype(np.int64)
    if not len(T):
        return []
    lab = we._weld_islands(b.V["pos"].astype(np.float64), T)
    order = {}
    for i, k in enumerate(lab):
        order.setdefault(int(k), len(order))
    lab = np.array([order[int(k)] for k in lab])
    return [np.nonzero(lab == k)[0] for k in range(len(order))]


def find_lens(pack, lamps):
    """the clear glass pieces of the body that sit over a lamp (not the windows): they are lamps too, for moving / resizing"""
    c = pack.chunk(0)
    out = []
    if not c or not lamps:
        return out
    for bi, b in enumerate(c.batches):
        if not len(b.I) or not (b.flags & 1) or b.prog != 2:
            continue
        T = b.I.reshape(-1, 3).astype(np.int64)
        for rows in _islands(b):
            if len(rows) > 1500:
                continue
            P = b.V["pos"][np.unique(T[rows])].astype(np.float64)
            lo, hi = P.min(0), P.max(0)
            if any(np.all(lo <= l.hi + 1.0) and np.all(hi >= l.lo - 1.0) for l in lamps):
                out.append(Lamp(0, bi, rows, lo, hi, (0, bi, int(rows[0]))))
    return out


def lamp_tags(pack):
    return sorted(c.tag for c in pack.chunks if is_lamp_tag(c.tag))


def find_lamps(pack):
    out = []
    for tag in lamp_tags(pack):
        c = pack.chunk(tag)
        if not c:
            continue
        for bi, b in enumerate(c.batches):
            T = b.I.reshape(-1, 3).astype(np.int64)
            for rows in _islands(b):
                P = b.V["pos"][np.unique(T[rows])].astype(np.float64)
                out.append(Lamp(tag, bi, rows, P.min(0), P.max(0), (tag, bi, int(rows[0]))))
    return out + find_lens(pack, out)


def describe(lamp, pack=None):
    c, s = lamp.center, lamp.size
    nm = ""
    if pack is not None:
        b = pack.chunk(lamp.tag).batches[lamp.bi]
        nm = pack.tex[b.tex]["name"] if b.tex < len(pack.tex) else "?"
    return "%-9s %s  x %+.2f  y %.2f  z %+.2f   %.2f x %.2f x %.2f   %d tris  %s" % (
        short_name(lamp.tag), lamp.side, c[0], c[1], c[2], s[0], s[1], s[2], lamp.ntri, nm)


def _verts_of(pack, lamp):
    b = pack.chunk(lamp.tag).batches[lamp.bi]
    return b, np.unique(b.I.reshape(-1, 3)[lamp.rows].astype(np.int64))


def _touch(pack, lamps):
    for t in {l.tag for l in lamps}:
        pack.chunk(t).update_bbox()


def _norm_to_i16(n):
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(ln > 1e-9, n / np.maximum(ln, 1e-9), n)
    return np.round(n * 32767).astype(np.int16)


def rot_matrix(rx=0.0, ry=0.0, rz=0.0):
    """degrees about x (pitch), y (yaw), z (roll), applied in that order"""
    a, b, c = np.radians([rx, ry, rz])
    X = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    Y = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    Z = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    return Z @ Y @ X


# ------------------------------------------------------------------------------------------------ editing
def transform(pack, lamps, move=(0, 0, 0), scale=(1, 1, 1), rot=(0, 0, 0), group=False):
    """move / scale / turn lamps.  Each lamp turns and scales about its own centre; group=True treats the selection as
    one piece (about the middle of all of it), so the gaps between the lamps scale too."""
    if not lamps:
        return
    move, scale = np.asarray(move, float), np.asarray(scale, float)
    R = rot_matrix(*rot)
    if group:
        lo = np.min([l.lo for l in lamps], 0)
        hi = np.max([l.hi for l in lamps], 0)
        gc = (lo + hi) / 2
    for l in lamps:
        b, vi = _verts_of(pack, l)
        P = b.V["pos"][vi].astype(np.float64)
        c = gc if group else l.center
        P = ((P - c) * scale) @ R.T + c + move
        V = b.V.copy()
        V["pos"][vi] = P.astype(np.float32)
        if not np.allclose(R, np.eye(3)):
            N = V["normal"][vi].astype(np.float64) @ R.T
            V["normal"][vi] = _norm_to_i16(N)
        b.V = V
    _touch(pack, lamps)


def flip(pack, lamps, axis):
    """turn lamps over about their own centre along an axis (0 = left / right, 1 = up / down, 2 = front / back)"""
    for l in lamps:
        b, vi = _verts_of(pack, l)
        c = l.center
        V = b.V.copy()
        P = V["pos"][vi].astype(np.float64)
        P[:, axis] = 2 * c[axis] - P[:, axis]
        V["pos"][vi] = P.astype(np.float32)
        N = V["normal"][vi].astype(np.int32)
        N[:, axis] = -N[:, axis]
        V["normal"][vi] = np.clip(N, -32767, 32767).astype(np.int16)
        b.V = V
        I = b.I.reshape(-1, 3).copy()
        I[l.rows] = I[l.rows][:, [0, 2, 1]]                     # keep the faces facing the same way round
        b.I = I.reshape(-1)
    _touch(pack, lamps)


def _append(b, V, I):
    if len(b.V) + len(V) > MAX_VERTS:
        raise LampError("This part would hold more than %d vertices (a batch cannot index more)." % MAX_VERTS)
    base = len(b.V)
    b.V = np.concatenate([b.V, V])
    b.I = np.concatenate([b.I.reshape(-1), (np.asarray(I).reshape(-1).astype(np.int64) + base)]).astype("<u2")


def copy_lamps(pack, lamps, offset=(0, 0, 0), mirror_x=False, into=None):
    """add copies of lamps (same part, or `into` = another part tag); mirror_x puts each copy on the other side of the car"""
    new = []
    for l in lamps:
        b, vi = _verts_of(pack, l)
        T = b.I.reshape(-1, 3).astype(np.int64)[l.rows]
        remap = {int(v): i for i, v in enumerate(vi)}
        loc = np.vectorize(remap.get)(T) if len(T) else T
        V = b.V[vi].copy()
        P = V["pos"].astype(np.float64)
        N = V["normal"].astype(np.int32)
        if mirror_x:
            P[:, 0] = -P[:, 0]
            N[:, 0] = -N[:, 0]
            loc = loc[:, [0, 2, 1]]
        P += np.asarray(offset, float)
        V["pos"] = P.astype(np.float32)
        V["normal"] = np.clip(N, -32767, 32767).astype(np.int16)
        dst = b
        if into is not None and into != l.tag:
            c = pack.chunk(into)
            if c is None:
                c = pack.set_chunk(into, [])
            dst = next((x for x in c.batches if x.material() == b.material()), None)
            if dst is None:
                dst = we.Batch(b.tex, b.flags, b.prog, b.pad, b.env, b.spec, b.power, b.V[:0].copy(), np.zeros(0, "<u2"))
                c.batches.append(dst)
        first = len(dst.I) // 3
        _append(dst, V, loc)
        tag = into if into is not None else l.tag
        pack.chunk(tag).update_bbox()
        new.append((tag, dst, first, len(loc)))
    return _relocate(pack, new)


def _relocate(pack, spans):
    """the Lamp objects that now cover the given (tag, batch, first row, count) triangle runs"""
    found = []
    for tag, dst, first, n in spans:
        bi = next(i for i, x in enumerate(pack.chunk(tag).batches) if x is dst)
        want = set(range(first, first + n))
        for l in find_lamps(pack):
            if l.tag == tag and l.bi == bi and want & set(int(r) for r in l.rows):
                if all(l.key != f.key for f in found):
                    found.append(l)
    return found


def delete_lamps(pack, lamps):
    by = {}
    for l in lamps:
        by.setdefault((l.tag, l.bi), []).append(l.rows)
    for (tag, bi), rows in sorted(by.items(), key=lambda kv: -kv[0][1]):
        c = pack.chunk(tag)
        b = c.batches[bi]
        T = b.I.reshape(-1, 3).astype(np.int64)
        keep = np.ones(len(T), bool)
        keep[np.concatenate(rows)] = False
        T = T[keep]
        if not len(T):
            del c.batches[bi]
        else:
            used, inv = np.unique(T, return_inverse=True)
            b.V = b.V[used].copy()
            b.I = inv.reshape(-1).astype("<u2")
        c.update_bbox()


def split_at(pack, lamp, axis, value, gap=0.12):
    """cut one lamp piece into two at a plane (e.g. a tail-lamp strip that spans both sides: axis 0, value 0).
    Triangles go by their centre; vertices on the cut are duplicated so the two halves can be moved separately, and the
    far half is moved `gap` away (about 1 cm) so the two read as separate lamps from then on."""
    b, vi = _verts_of(pack, lamp)
    T = b.I.reshape(-1, 3).astype(np.int64)
    cen = b.V["pos"].astype(np.float64)[T[lamp.rows]].mean(1)[:, axis]
    side_b = cen >= value
    if side_b.all() or (~side_b).all():
        raise LampError("Nothing to cut: all of this lamp is on one side of that line.")
    rows_b = lamp.rows[side_b]
    used_a = set(np.unique(T[lamp.rows[~side_b]]).tolist())
    shared = sorted(set(np.unique(T[rows_b]).tolist()) & used_a)
    if shared:
        add = b.V[shared].copy()
        base = len(b.V)
        if base + len(add) > MAX_VERTS:
            raise LampError("This part would hold more than %d vertices." % MAX_VERTS)
        b.V = np.concatenate([b.V, add])
        mp = {v: base + i for i, v in enumerate(shared)}
        for r in rows_b:
            T[r] = [mp.get(int(x), int(x)) for x in T[r]]
        b.I = T.reshape(-1).astype("<u2")
    vb = np.unique(b.I.reshape(-1, 3).astype(np.int64)[rows_b])
    V = b.V.copy()
    V["pos"][vb, axis] += np.float32(gap)
    b.V = V
    pack.chunk(lamp.tag).update_bbox()
    return [l for l in find_lamps(pack) if l.tag == lamp.tag and l.bi == lamp.bi and set(map(int, l.rows)) & set(map(int, lamp.rows))]


# ------------------------------------------------------------------------------------------------ new lamps
def glow_texture(pack, shape):
    """slot of a soft glow texture in the pack (made on first use): black edges, white middle, so it adds light"""
    from PIL import Image
    name = {"Rectangle": "lamp_glow_box", "Round": "lamp_glow_round", "Ring": "lamp_glow_ring"}[shape]
    i = pack.tex_index(name)
    if i is not None and pack.tex[i]["name"] == name:
        return i
    n = 64
    y, x = np.mgrid[0:n, 0:n]
    u, v = (x + 0.5) / n * 2 - 1, (y + 0.5) / n * 2 - 1
    if shape == "Rectangle":
        d = np.maximum(np.abs(u), np.abs(v))
        k = np.clip((1.0 - d) / 0.35, 0, 1)
    else:
        r = np.sqrt(u * u + v * v)
        if shape == "Round":
            k = np.clip((1.0 - r) / 0.8, 0, 1) ** 1.3
        else:
            k = np.clip(1 - np.abs(r - 0.78) / 0.2, 0, 1)
    k = k * k * (3 - 2 * k)
    g = (k * 255).astype(np.uint8)
    im = Image.fromarray(np.dstack([g, g, g, np.full_like(g, 255)]), "RGBA")
    return pack.add_texture(name, im, max_size=64)


def _mesh(shape, w, h, segments=24):
    """local (u right, v up) vertices, uv and triangles (counter-clockwise from the front) of a w x h lamp"""
    hw, hh = w / 2, h / 2
    if shape == "Rectangle":
        P = np.array([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]], float)
        UV = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], float)
        return P, UV, np.array([[0, 1, 2], [0, 2, 3]])
    a = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ring = np.c_[np.cos(a), np.sin(a)]
    uvr = np.c_[0.5 + 0.5 * ring[:, 0], 0.5 - 0.5 * ring[:, 1]]
    if shape == "Round":
        P = np.vstack([[0, 0], ring * [hw, hh]])
        UV = np.vstack([[0.5, 0.5], uvr])
        T = [[0, 1 + i, 1 + (i + 1) % segments] for i in range(segments)]
        return P, UV, np.array(T)
    inner = 0.55
    P = np.vstack([ring * [hw, hh], ring * [hw * inner, hh * inner]])
    UV = np.vstack([uvr, np.c_[0.5 + 0.5 * inner * ring[:, 0], 0.5 - 0.5 * inner * ring[:, 1]]])
    T = []
    for i in range(segments):
        j = (i + 1) % segments
        T += [[i, j, segments + i], [j, segments + j, segments + i]]
    return P, UV, np.array(T)


def make_lamp_mesh(shape, w, h, center, facing, tint, strength=1.0):
    """(V structured array, local triangles) of a flat lamp at `center`, looking out of the car towards `facing`"""
    P2, UV, T = _mesh(shape, w, h)
    cx, cy, cz = center
    u, v = P2[:, 0], P2[:, 1]
    if facing == "Front":
        pos = np.c_[cx + u, cy + v, np.full_like(u, cz)]
        n = (0, 0, 1)
    elif facing == "Rear":
        pos = np.c_[cx - u, cy + v, np.full_like(u, cz)]
        n = (0, 0, -1)
    elif facing == "Right side":
        pos = np.c_[np.full_like(u, cx), cy + v, cz - u]
        n = (1, 0, 0)
    else:
        pos = np.c_[np.full_like(u, cx), cy + v, cz + u]
        n = (-1, 0, 0)
    T = np.array(T, int)
    cr = np.cross(pos[T[:, 1]] - pos[T[:, 0]], pos[T[:, 2]] - pos[T[:, 0]])
    flip_ = (cr @ np.array(n, float)) < 0
    T[flip_] = T[flip_][:, [0, 2, 1]]
    V = np.zeros(len(pos), dtype=we.VERTEX_LIT if hasattr(we, "VERTEX_LIT") else None)
    V["pos"] = pos.astype(np.float32)
    V["uv"] = UV.astype(np.float32)
    s = float(np.clip(strength, 0.0, 1.0))
    col = np.array(TINTS.get(tint, tint), float) * s
    V["rgba"] = np.r_[np.round(col), 255].astype(np.uint8)
    V["normal"] = np.array(n, np.int16) * 32767
    return V, T


def add_lamp(pack, states, shape="Rectangle", w=1.0, h=0.4, center=(0, 3, -20), facing="Rear", tint="Red", strength=1.0,
             count=1, step=(0.0, 0.0, 0.0), mirror=False, snap=False, tilt=True):
    """add lamp(s) to the given parts (e.g. (0x112, 0x113)).  count > 1 = a row (LED bar) spaced by `step`;
    mirror = the same again on the other side of the car.
    snap = put each lamp just outside the car's skin at its X / Y (Front / Rear) or Z / Y (sides); tilt = also turn it to lie
    flat on the slope there (up to 70 degrees).  -> the new Lamp objects"""
    if not states:
        raise LampError("Pick at least one part (garage, lamps on, tail glow or stop glow) for the new lamp.")
    if w <= 0 or h <= 0:
        raise LampError("The lamp needs a width and a height above 0.")
    count = int(max(1, min(count, 200)))
    slot = glow_texture(pack, shape)
    spans = []
    for tag in states:
        c = pack.chunk(tag) or pack.set_chunk(tag, [])
        pre = pack.preset("add")
        dst = next((b for b in c.batches if b.tex == slot and b.flags == pre[0] and b.prog == pre[1]), None)
        if dst is None:
            dst = we.Batch(slot, pre[0], pre[1], 0, pre[2], pre[3], pre[4], np.zeros(0, we.VERTEX_LIT), np.zeros(0, "<u2"))
            c.batches.append(dst)
        for k in range(count):
            for flipx in ((False, True) if mirror else (False,)):
                cc = np.array(center, float) + np.asarray(step, float) * k
                if flipx:
                    cc[0] = -cc[0]
                fc = facing
                nt = None
                if snap:
                    fr = fc in ("Front", "Rear")
                    sf = fit_surface(pack, cc[0] if fr else cc[2], cc[1], w, h, fc)
                    if sf:
                        cc[2 if fr else 0] = sf[0]
                        nt = sf[1]
                V, T = make_lamp_mesh(shape, w, h, cc, fc, tint, strength)
                if nt is not None and tilt:
                    n0 = np.array(V["normal"][0], float) / 32767.0
                    R = _tilt_matrix(n0, nt)
                    if R is not None:
                        P = (V["pos"].astype(np.float64) - cc) @ R.T + cc + nt * 0.03
                        V["pos"] = P.astype(np.float32)
                        V["normal"] = np.round(nt * 32767).astype(np.int16)
                first = len(dst.I) // 3
                _append(dst, V, T)
                spans.append((tag, dst, first, len(T)))
        c.update_bbox()
    return _relocate(pack, spans)



# ------------------------------------------------------------------------------------------------ linked pieces
def carve(pack, tag, bi, rows):
    """the triangles `rows` of a batch as a piece of their own: vertices they share with the rest are duplicated, so
    moving the piece does not drag the rest along (a seam opens only where it is moved)"""
    b = pack.chunk(tag).batches[bi]
    T = b.I.reshape(-1, 3).astype(np.int64)
    rows = np.asarray(rows)
    rest = np.ones(len(T), bool)
    rest[rows] = False
    shared = sorted(set(np.unique(T[rows]).tolist()) & set(np.unique(T[rest]).tolist()))
    if shared:
        base = len(b.V)
        if base + len(shared) > MAX_VERTS:
            raise LampError("This part would hold more than %d vertices." % MAX_VERTS)
        b.V = np.concatenate([b.V, b.V[shared].copy()])
        mp = {v: base + i for i, v in enumerate(shared)}
        for r in rows:
            T[r] = [mp.get(int(x), int(x)) for x in T[r]]
        b.I = T.reshape(-1).astype("<u2")
    P = b.V["pos"][np.unique(T[rows])].astype(np.float64)
    return Lamp(tag, bi, rows, P.min(0), P.max(0), (tag, bi, int(rows[0])))


def linked_pieces(pack, lamp, tol=0.15):
    """the piece(s) of the linked part that sit where `lamp` sits: a tail lamp in tail glow (0x112) <-> the same lamp in
    stop glow (0x113), a bulb in lamps on (0x111) <-> garage (0x110).  When the other part holds one big piece (one strip for
    both tail lamps) only the triangles inside this lamp's box are taken (carved out).  Changes the pack: call inside an undo point."""
    out = []
    for tag in LINKED.get(lamp.tag, ()):
        c = pack.chunk(tag)
        if not c:
            continue
        for bi, b in enumerate(c.batches):
            T = b.I.reshape(-1, 3).astype(np.int64)
            if not len(T):
                continue
            P = b.V["pos"].astype(np.float64)
            cen = P[T].mean(1)
            m = np.all((cen >= lamp.lo - tol) & (cen <= lamp.hi + tol), 1)
            if not m.any():
                continue
            rows = np.nonzero(m)[0]
            sub = P[np.unique(T[rows])]
            ext, want = sub.max(0) - sub.min(0), lamp.hi - lamp.lo
            if np.any(ext[:2] < 0.4 * want[:2] - 1e-6):
                continue                                   # only a sliver of it is there: not the same lamp
            out.append(carve(pack, tag, bi, rows))
    return out


def distinct(pieces, others):
    """pieces that share no triangle with `others` (so nothing is edited twice)"""
    taken = {}
    for o in others:
        taken.setdefault((o.tag, o.bi), set()).update(int(r) for r in o.rows)
    out = []
    for p in pieces:
        t = taken.setdefault((p.tag, p.bi), set())
        rs = set(int(r) for r in p.rows)
        if not (rs & t):
            out.append(p)
            t.update(rs)
    return out


# ------------------------------------------------------------------------------------------------ colour and strength
def lamp_color(pack, lamps):
    """(r, g, b) 0-255 of the brightest vertex colour among the lamps, and the glow strength 0-1"""
    cols = []
    for l in lamps:
        b, vi = _verts_of(pack, l)
        cols.append(b.V["rgba"][vi][:, :3].astype(float))
    if not cols:
        return (255, 255, 255), 1.0
    c = np.concatenate(cols)
    mx = float(c.max()) if len(c) else 255.0
    return tuple(int(x) for x in (c.mean(0) * (255.0 / max(mx, 1.0)))), mx / 255.0


def recolor(pack, lamps, tint=None, strength=1.0):
    """set the vertex colour of lamps: tint = a TINTS name (None = keep the lamp's own colour), strength 0-1 = how bright.
    The colour multiplies the lamp's texture, so it tints and dims what the texture already shows; it cannot turn a red lamp
    texture blue (use flatten() for a plain colour)."""
    s = float(np.clip(strength, 0.0, 1.0))
    for l in lamps:
        b, vi = _verts_of(pack, l)
        V = b.V.copy()
        a = V["rgba"]
        if tint is None:
            rgb = a[vi][:, :3].astype(float)
            mx = rgb.max(1, keepdims=True)
            base = np.where(mx > 0, rgb / np.maximum(mx, 1) * 255.0, 255.0)
        else:
            base = np.array(TINTS[tint], float)
        a[vi, :3] = np.round(base * s).astype(np.uint8)
        b.V = V


def flat_texture(pack):
    from PIL import Image
    return pack.add_texture("lamp_glow_solid", Image.new("RGBA", (8, 8), (255, 255, 255, 255)), max_size=8) \
        if pack.tex_index("lamp_glow_solid") is None or pack.tex[pack.tex_index("lamp_glow_solid")]["name"] != "lamp_glow_solid" \
        else pack.tex_index("lamp_glow_solid")


def flatten(pack, lamps):
    """give lamps a plain white texture (every texel the same) so their colour comes only from recolor(): any colour is
    possible, the texture's detail is lost.  -> the lamps in their new place"""
    slot = flat_texture(pack)
    spans, old = [], []
    for l in lamps:
        if l.tag == 0:
            continue
        b, vi = _verts_of(pack, l)
        T = b.I.reshape(-1, 3).astype(np.int64)[l.rows]
        remap = {int(v): i for i, v in enumerate(vi)}
        loc = np.vectorize(remap.get)(T)
        V = b.V[vi].copy()
        V["uv"] = (0.5, 0.5)
        c = pack.chunk(l.tag)
        dst = next((x for x in c.batches if x.tex == slot and (x.flags, x.prog, x.env, x.spec, x.power) == (b.flags, b.prog, b.env, b.spec, b.power)), None)
        if dst is None:
            dst = we.Batch(slot, b.flags, b.prog, b.pad, b.env, b.spec, b.power, b.V[:0].copy(), np.zeros(0, "<u2"))
            c.batches.append(dst)
        first = len(dst.I) // 3
        _append(dst, V, loc)
        spans.append((l.tag, dst, first, len(loc)))
        old.append(l)
    delete_lamps(pack, old)
    return _relocate(pack, spans)


# ------------------------------------------------------------------------------------------------ other kits / cars
def sibling_kits(path):
    """the other packs of the same car beside this one: maz_FC3S.wpk <-> maz_FC3S_aeroA / B / C.wpk"""
    import re
    d, base = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    m = re.match(r"(.+?)(?:_aero[ABC])?\.wpk$", base, re.I)
    if not m:
        return []
    out = []
    for suf in ("", "_aeroA", "_aeroB", "_aeroC"):
        p = os.path.join(d, m.group(1) + suf + ".wpk")
        if os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(path):
            out.append(p)
    return out


def copy_lamp_parts(src, dst, src_stock=None):
    """replace the lamp parts (0x110-0x114) of `dst` by those of `src`, with the textures they use (added to dst when it
    lacks them).  Parts that `src` does not have are left as they are in dst.  -> [notes]"""
    notes = []

    def slot(i):
        if i >= len(src.tex):
            return 0xFFFF
        name = src.tex[i]["name"]
        for j, t in enumerate(dst.tex):
            if t["name"] == name:
                return j
        e = src.tex[i]
        if not e.get("raw") and src_stock is not None:
            k = src_stock.tex_index(name)
            if k is not None and src_stock.tex[k]["name"] == name:
                e = src_stock.tex[k]
        if not e.get("raw"):
            raise LampError("The texture %s is empty in the source and missing in the target." % name)
        dst.tex.append(dict(e))
        notes.append("texture %s copied" % name)
        return len(dst.tex) - 1

    for tag in sorted({c.tag for c in src.chunks if is_lamp_tag(c.tag)}):
        sc = src.chunk(tag)
        if not sc or not sc.batches:
            continue
        bs = []
        for b in sc.batches:
            nb = b.copy()
            nb.tex = slot(b.tex) if b.tex < len(src.tex) else b.tex
            nb.env = slot(b.env) if b.env < len(src.tex) else 0xFFFF
            nb.spec = slot(b.spec) if b.spec < len(src.tex) else 0xFFFF
            bs.append(nb)
        dst.set_chunk(tag, bs)
        notes.append("%s: %d batches" % (hex(tag), len(bs)))
    dst.check()
    return notes


# ------------------------------------------------------------------------------------------------ undo
def snapshot(pack):
    return dict(chunks=[(c.tag, c.lo, c.hi, [b.copy() for b in c.batches]) for c in pack.chunks if is_edit_tag(c.tag)],
                ntex=len(pack.tex), tex=[dict(t) for t in pack.tex])


def restore(pack, snap):
    keep = [c for c in pack.chunks if not is_edit_tag(c.tag)]
    for tag, lo, hi, bs in snap["chunks"]:
        keep.append(we.Chunk(tag, lo, hi, [b.copy() for b in bs]))
    keep.sort(key=lambda c: c.tag)
    pack.chunks = keep
    pack.tex = [dict(t) for t in snap["tex"]]


# ------------------------------------------------------------------------------------------------ cli
def cli(argv):
    cmds = ("list", "selftest", "apply", "kits")
    if len(argv) < 2 or argv[0] not in cmds:
        print(__doc__)
        return 2
    if argv[0] in ("apply", "kits"):
        src = we.Pack.load(argv[1])
        stock = we.stock_pack_of(argv[1])
        targets = argv[2:] if argv[0] == "apply" else sibling_kits(argv[1])
        if not targets:
            print("No target packs.")
            return 1
        for f in targets:
            try:
                d = we.Pack.load(f)
                notes = copy_lamp_parts(src, d, stock)
                d.save(f)
                print("%s: %s" % (os.path.basename(f), "; ".join(notes) or "nothing to copy"))
            except Exception as ex:
                print("%s: FAILED %s" % (os.path.basename(f), ex))
        return 0
    pack = we.Pack.load(argv[1])
    lamps = find_lamps(pack)
    if argv[0] == "list":
        for i, l in enumerate(lamps):
            print("%3d  %s" % (i, describe(l, pack)))
        print("%d lamps" % len(lamps))
        return 0
    return selftest(pack)


def selftest(pack):
    """edit a copy: every operation, then check()"""
    p = copy.deepcopy(pack)
    ls = find_lamps(p)
    if not ls:
        print("no lamps in this pack")
        return 1
    n0 = len(ls)
    transform(p, ls[:1], move=(0.1, 0.1, 0.0), scale=(1.1, 1, 1), rot=(0, 5, 0))
    flip(p, find_lamps(p)[:1], 0)
    new = copy_lamps(p, find_lamps(p)[:1], mirror_x=True)
    add_lamp(p, (0x112, 0x113), "Round", 0.5, 0.5, (2, 4, -20), "Rear", "Red", count=3, step=(0.6, 0, 0), mirror=True)
    delete_lamps(p, new)
    ls2 = find_lamps(p)
    one = next((l for l in ls2 if l.tag in LINKED), None)
    if one:
        lk = distinct(linked_pieces(p, one), [one])
        transform(p, [one] + lk, move=(0, 0.1, 0))
        recolor(p, [l for l in find_lamps(p) if is_lamp_tag(l.tag)][:2], "Amber", 0.7)
    add_lamp(p, (0x111,), "Ring", 0.6, 0.6, (3, 3, 10), "Front", "White", snap=True, tilt=True)
    p.check()
    for c in p.chunks:
        c.update_bbox()
    print("selftest ok: %d lamps -> %d" % (n0, len(find_lamps(p))))
    return 0



# ================================================================================================ body surface
def body_surface(pack, u, v, facing, outside=0.05):
    """where the car's skin is under a lamp at (u, v) = (x, y) for Front / Rear, (z, y) for the sides:
    (the coordinate along the facing axis just outside the body, the outward surface normal), or None when no body is there"""
    body = pack.chunk(0)
    if not body:
        return None
    tris = [b.V["pos"].astype(np.float64)[b.I.reshape(-1, 3)] for b in body.batches if len(b.I)]
    if not tris:
        return None
    T = np.concatenate(tris)
    fr = facing in ("Front", "Rear")
    a, b2, ax = (0, 1, 2) if fr else (2, 1, 0)                   # the two picture axes, the ray axis
    A, B, C = T[:, 0], T[:, 1], T[:, 2]
    d = (B[:, b2] - C[:, b2]) * (A[:, a] - C[:, a]) + (C[:, a] - B[:, a]) * (A[:, b2] - C[:, b2])
    ok = np.abs(d) > 1e-12
    d = np.where(ok, d, 1.0)
    l0 = ((B[:, b2] - C[:, b2]) * (u - C[:, a]) + (C[:, a] - B[:, a]) * (v - C[:, b2])) / d
    l1 = ((C[:, b2] - A[:, b2]) * (u - C[:, a]) + (A[:, a] - C[:, a]) * (v - C[:, b2])) / d
    l2 = 1 - l0 - l1
    hit = np.nonzero(ok & (l0 >= 0) & (l1 >= 0) & (l2 >= 0))[0]
    if not len(hit):
        return None
    z = l0[hit] * A[hit, ax] + l1[hit] * B[hit, ax] + l2[hit] * C[hit, ax]
    out = facing in ("Front", "Right side")
    k = hit[int(np.argmax(z) if out else np.argmin(z))]
    n = np.cross(B[k] - A[k], C[k] - A[k])
    want = np.zeros(3)
    want[ax] = 1.0 if out else -1.0
    if n @ want < 0:
        n = -n
    ln = np.linalg.norm(n)
    n = n / ln if ln > 1e-12 else want
    zc = float(z.max() + outside) if out else float(z.min() - outside)
    return zc, n


def fit_surface(pack, u, v, w, h, facing, outside=0.05):
    """like body_surface, but for a lamp that is w x h big: the skin is sampled over the whole footprint and a flat plane is
    fitted through it, then pushed out until no sample is above it.  -> (coordinate of the lamp's middle along the facing
    axis, outward normal of the plane): the lamp lies flat on the slope and does not sink in where the body steps or curves."""
    pts = []
    for du in (-0.45, 0.0, 0.45):
        for dv in (-0.45, 0.0, 0.45):
            r = body_surface(pack, u + du * w, v + dv * h, facing, 0.0)
            if r is not None:
                pts.append((du * w, dv * h, r[0]))
    if not pts:
        return None
    P = np.array(pts, float)
    sign = 1.0 if facing in ("Front", "Right side") else -1.0           # which way is outwards along the ray axis
    if len(P) >= 3 and np.ptp(P[:, 0]) + np.ptp(P[:, 1]) > 1e-6:
        A = np.c_[P[:, 0], P[:, 1], np.ones(len(P))]
        try:
            (a, b, c), *_ = np.linalg.lstsq(A, P[:, 2], rcond=None)
        except np.linalg.LinAlgError:
            a = b = 0.0
            c = float(P[:, 2].max() if sign > 0 else P[:, 2].min())
        resid = (P[:, 2] - (A @ np.array([a, b, c]))) * sign
        c += sign * (max(0.0, float(resid.max())) + outside)
    else:
        a = b = 0.0
        c = float(P[:, 2].max() if sign > 0 else P[:, 2].min()) + sign * outside
    n = np.array([-a, -b, 1.0]) if facing in ("Front", "Rear") else np.array([1.0, -b, -a])
    n = n / np.linalg.norm(n)
    want = np.zeros(3)
    want[2 if facing in ("Front", "Rear") else 0] = sign
    if n @ want < 0:
        n = -n
    return float(c), n


def _tilt_matrix(n0, n1):
    """rotation taking the unit vector n0 onto n1 (None when they are more than 70 degrees apart)"""
    n0, n1 = np.asarray(n0, float), np.asarray(n1, float)
    c = float(n0 @ n1)
    if c < 0.342:
        return None
    v = np.cross(n0, n1)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K / (1.0 + c)


# ================================================================================================ the tab
HELP = """Lamps  -  move, resize, reshape, add and remove a car's headlights and tail lights

1. Open car .wpk (or "Use the car from Parts & viewport").  The list is every lamp piece of the car, by part:
     garage 0x110     drawn in the garage / menus
     lamps on 0x111   drawn in a race: bulbs, reflectors, front glow
     tail glow 0x112  drawn in a race: the running lights.   stop glow 0x113: drawn while braking
   "Light" under the picture shows what the game draws in each situation.
2. Click a lamp (Ctrl / Shift for several; "Left" / "Right" / "Whole part" select faster).  It lights up blue in the
   picture.  "Zoom" > "Close on selection" looks at it from close by.
3. Move   type a new X / Y / Z for the middle of the selection and press Set position, or nudge with the buttons
          (Step = how far).  X is left (-) / right (+), Y up, Z front (+) / back (-).
   Size   type the new width / height / depth and press Set size.   Turn: degrees, then Turn.
   "Edit both sides" repeats every change mirrored on the lamp on the other side of the car.
   Mirror copy / Duplicate / Flip / Split at X / Delete work on the selection.  A lamp strip that spans both sides
   (some tail glows are one piece) can be cut with Split at X, then each half moved on its own.
4. Add a lamp   pick a kind (Headlamp, Tail lamp ...) or set the parts yourself, the shape, colour, size and place.
   Count > 1 makes a row (an LED bar) spaced by Step; "Both sides" adds the mirror image.  "Snap to body" puts the
   lamp just outside the car's skin at that X / Y, and "Tilt to the surface" lays it flat on the slope there.  New lamps use a soft glow texture that is added to the pack.
5. Linked parts   a tail lamp lives in tail glow (0x112) AND stop glow (0x113), a bulb in lamps on (0x111) AND garage (0x110).
   With "Also the matching piece in the linked part" on, every edit also goes to the piece in the other part that sits
   at the same place.  If that part holds one big strip for both sides, only the part inside the lamp's box is moved.
6. Colour   pick a colour and a glow %, Apply colour.  The colour multiplies the lamp's own texture, so it tints and dims what is
   there; tick "Flat colour" to give the lamp a plain texture (any colour, the texture's detail is lost).
7. Lens covers   some bodies have a solid dark piece over the headlights.  "Show what would turn to glass" paints it pink in the
   picture; if it also covers black trim, lower "Black below" or "Reach", then "Make it clear glass".  Clear lens pieces already
   in the body show up in the list as "lens" and can be moved, resized or deleted like any lamp.
8. Copy lamps to other kits   puts this car's lamp parts (and the textures they need) into the car's other packs
   (_aeroA / B / C, the stock pack) or into files you pick.  The old files are kept as .bak.
9. Save to game writes the .wpk (the old file is kept once as <file>.bak).  Undo goes back one step.

Notes
- A tail lamp needs to be in BOTH tail glow (0x112) and stop glow (0x113) to show when racing and when braking.
- A headlamp needs 0x111 (race) and, to show in the garage, 0x110.
- A part can hold at most 65535 vertices; the tab says so if you go past that.  The game's own limit on lamps is not known.
- The headlight housing and the painted lens area are part of the body model: only clear glass lens pieces can be edited here.
- Not tried in the game: this was checked in the tool's own preview only.
"""


def build_tab(tab_parent, root, app=None, host=None):
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from PIL import Image, ImageTk

    parent = tab_parent
    host = host or getattr(app, "models", None)             # the Models page: it owns the car and the one shared viewport
    try:
        import wpk_browser as WB
    except ImportError:
        WB = None
    try:
        import gl_viewport as GLV
        GLV_WHY = GLV.import_error()
    except Exception as ex:                                  # no moderngl / OpenGL here: the software picture stays
        GLV, GLV_WHY = None, "gl_viewport.py could not be loaded (%s)" % ex
    VIEWS = {"Auto": None, "Rear": (180, 0), "Rear 3/4": (145, 16), "Front": (0, 0), "Front 3/4": (-35, 14),
             "Left side": (270, 0), "Right side": (90, 0), "Top": (0, 89)}
    LIGHTS = we.LIGHT_MODES + ("All lamp parts",)
    SIDE_ORDER = {"L": 0, "C": 1, "R": 2}

    class Tab:
        """The Lamps module of the Vehicle Models page.  It edits the car that is open in Parts (same pack, same Undo, same Save), draws in
        the page's one viewport (Models owns it; this adds a blue / pink overlay and its own camera moves) and has its own list, panels
        and bar.  host = the Models tab (wpk_edit)."""
        pack = property(lambda s: s.host.pack)
        path = property(lambda s: s.host.path)
        stock = property(lambda s: s.host.stock)
        gl = property(lambda s: s.host.gl)
        cam = property(lambda s: s.host.cam)
        dirty = property(lambda s: s.host.dirty, lambda s, v: setattr(s.host, "dirty", v))

        def __init__(self):
            self.host = host
            self.lamps = []                 # in list order
            self.layer = None               # the overlay layer in the shared viewport
            self.active = False
            self._mine = False              # True while this module itself edits the car (it refreshes itself then)
            self._drag = None
            self._auto_side = None
            self.lens_dry = None
            sv = tk.StringVar
            self.status = UI.StatusLine("Open a car in Parts & viewport to edit its lamps.")
            self.v_filter = sv(value="All parts")
            self.v_view = sv(value="Auto")
            self.v_light = sv(value="All lamp parts")
            self.v_zoom = sv(value="Close on selection")
            self.v_night = tk.BooleanVar(value=True)
            self.v_both = tk.BooleanVar(value=True)
            self.px, self.py, self.pz = sv(value="0"), sv(value="0"), sv(value="0")
            self.sw, self.sh, self.sd = sv(value="0"), sv(value="0"), sv(value="0")
            self.ry, self.rx, self.rz = sv(value="0"), sv(value="0"), sv(value="0")
            self.step = sv(value="0.1")
            self.cutx = sv(value="0")
            # add panel
            self.a_role = sv(value="Tail lamp")
            self.a_parts = {t: tk.BooleanVar(value=False) for t in (0x110, 0x111, 0x112, 0x113)}
            self.a_facing, self.a_shape, self.a_tint = sv(value="Rear"), sv(value="Rectangle"), sv(value="Red")
            self.a_w, self.a_h = sv(value="2.0"), sv(value="0.5")
            self.a_x, self.a_y, self.a_z = sv(value="5"), sv(value="4"), sv(value="-20")
            self.a_count, self.a_sx, self.a_sy, self.a_sz = sv(value="1"), sv(value="0.6"), sv(value="0"), sv(value="0")
            self.a_mirror = tk.BooleanVar(value=True)
            self.a_snap = tk.BooleanVar(value=True)
            self.a_strength = sv(value="100")
            self.a_tilt = tk.BooleanVar(value=True)
            self.v_link = tk.BooleanVar(value=True)
            self.c_tint, self.c_strength = sv(value="Keep colour"), sv(value="100")
            self.v_flat = tk.BooleanVar(value=False)
            self.l_dark, self.l_reach = sv(value="50"), sv(value="1.0")
            self.lens_dry = None
            self.lens_msg = sv(value="")
            self.build()
            self.set_role()
            host.register_module("lamps", self)

        # ------------------------------------------------------------ layout
        def build(self):
            # panels only: the list on the left, a bar under the viewport, folding panels on the right; the viewport itself is the page's
            left = host.module_left("lamps")
            bar = host.module_bar("lamps")
            ws = self.ws = VL.Slot(left, host.module_right("lamps"), bar)

            # ---- left: toolbar and the lamp list
            tb = ttk.Frame(left)
            tb.pack(fill="x", pady=(0, 6))
            ttk.Label(tb, text="LAMPS", font=("TkDefaultFont", 10, "bold")).pack(side="left", padx=(0, 10))
            ttk.Button(tb, text="Undo", command=self.do_undo).pack(side="left")
            ttk.Button(tb, text="Copy to kits…", command=self.copy_kits).pack(side="left", padx=4)
            ttk.Button(tb, text="Help", command=lambda: messagebox.showinfo("Lamps", HELP, parent=root)).pack(side="left")
            fr = ttk.Frame(left)
            fr.pack(fill="x")
            ttk.Label(fr, text="Show:").pack(side="left")
            cb = ttk.Combobox(fr, textvariable=self.v_filter, state="readonly", width=20,
                              values=["All parts"] + [STATE_NAMES[t] for t in (0,) + LAMP_TAGS[:4]] + ["High brake lamps (0x141-0x17f)"])
            cb.pack(side="left", padx=4)
            cb.bind("<<ComboboxSelected>>", lambda _e: self.fill())
            sel = ttk.Frame(left)
            sel.pack(fill="x", side="bottom", pady=(4, 0))
            for t, f in (("Left", lambda: self.select_side("L")), ("Right", lambda: self.select_side("R")),
                         ("Whole part", self.select_part), ("None", lambda: self.tree.selection_set(()))):
                ttk.Button(sel, text=t, width=8, command=f).pack(side="left", padx=1)
            lf = ttk.Frame(left)
            lf.pack(fill="both", expand=True, pady=4)
            cols = ("part", "side", "x", "y", "z", "size", "tris")
            self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=14, selectmode="extended")
            for c, t, w, a in (("part", "Part", 62, "w"), ("side", "Side", 34, "center"), ("x", "X", 46, "e"), ("y", "Y", 42, "e"),
                               ("z", "Z", 46, "e"), ("size", "W x H x D", 104, "e"), ("tris", "Tris", 36, "e")):
                self.tree.heading(c, text=t)
                self.tree.column(c, width=w, anchor=a, stretch=(c == "size"))
            sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())

            # ---- the bar under the viewport: View / Zoom / Light / Dark scene, and this module's status line
            b1 = ttk.Frame(bar)
            b1.pack(fill="x")
            for lab, var, vals, w, row in (("Light", self.v_light, list(LIGHTS), 24, b1),):
                ttk.Label(row, text=lab + ":").pack(side="left", padx=(0, 2))
                c = ttk.Combobox(row, textvariable=var, values=vals, state="readonly", width=w)
                c.pack(side="left", padx=(0, 8))
                c.bind("<<ComboboxSelected>>", lambda _e, lab=lab: self.on_combo(lab))
            ttk.Checkbutton(b1, text="Dark scene", variable=self.v_night, command=self.render).pack(side="left")
            ws.flow(b1, bar)
            self.hint = ttk.Label(bar, text="Drag = orbit all the way round, right-drag or Shift-drag = move, wheel = zoom, double-click = fit, "
                                  "click a lamp = select it. The view buttons above are the page's.", foreground="#8f9eb2", wraplength=420, justify="left")
            self.hint.pack(fill="x", pady=(3, 0))
            self.status.bar(bar, ttk, tk, wraplength=420, fill="x", pady=(3, 0))

            # ---- right: property panels
            sec = ws.section("Selected lamps")
            ed = sec.body
            self.sel_lbl = ttk.Label(ed, text="Nothing selected.", foreground="#8f9eb2", wraplength=340, justify="left")
            self.sel_lbl.pack(fill="x")

            def entries(label, vars_, names, btn, cmd):
                row = ttk.Frame(ed)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=label, width=9).pack(side="left")
                for v, n in zip(vars_, names):
                    ttk.Label(row, text=n).pack(side="left", padx=(6, 1))
                    ttk.Entry(row, textvariable=v, width=7).pack(side="left")
                ttk.Button(row, text=btn, command=cmd).pack(side="left", padx=8)
                ws.flow(row)

            def line(parent_=None):
                f = ttk.Frame(parent_ or ed)
                f.pack(fill="x", pady=2)
                return f
            entries("Position", (self.px, self.py, self.pz), ("X", "Y", "Z"), "Set position", self.set_position)
            entries("Size", (self.sw, self.sh, self.sd), ("W", "H", "D"), "Set size", self.set_size)
            entries("Turn (deg)", (self.rx, self.ry, self.rz), ("Pitch", "Yaw", "Roll"), "Turn", self.do_turn)
            nud = line()
            ttk.Label(nud, text="Nudge  step").pack(side="left")
            ttk.Entry(nud, textvariable=self.step, width=6).pack(side="left", padx=4)
            ws.flow(nud)
            nud1 = line()
            for t, d in (("◄ X-", (-1, 0, 0)), ("X+ ►", (1, 0, 0)), ("▼ Y-", (0, -1, 0)), ("Y+ ▲", (0, 1, 0)),
                         ("Z- back", (0, 0, -1)), ("Z+ front", (0, 0, 1))):
                ttk.Button(nud1, text=t, width=8, command=lambda d=d: self.nudge(d)).pack(side="left", padx=1)
            ws.flow(nud1)
            nud2 = line()
            ttk.Checkbutton(nud2, text="Edit both sides", variable=self.v_both).pack(side="left", padx=(0, 4))
            ttk.Checkbutton(nud2, text="Also the linked part (tail <-> stop, garage <-> on)",
                            variable=self.v_link).pack(side="left", padx=4)
            ws.flow(nud2)
            ops = line()
            for t, f in (("Mirror copy", self.do_mirror), ("Duplicate", self.do_dup), ("Flip left/right", lambda: self.do_flip(0)),
                         ("Flip up/down", lambda: self.do_flip(1)), ("Delete", self.do_delete)):
                ttk.Button(ops, text=t, command=f).pack(side="left", padx=1)
            ttk.Label(ops, text="   Split at X").pack(side="left")
            ttk.Entry(ops, textvariable=self.cutx, width=6).pack(side="left", padx=3)
            ttk.Button(ops, text="Split", command=self.do_split).pack(side="left")
            ws.flow(ops)
            col = line()
            ttk.Label(col, text="Colour").pack(side="left")
            ttk.Combobox(col, textvariable=self.c_tint, values=["Keep colour"] + list(TINTS), state="readonly", width=12).pack(side="left", padx=4)
            ttk.Label(col, text="Glow %").pack(side="left", padx=(6, 0))
            ttk.Entry(col, textvariable=self.c_strength, width=5).pack(side="left", padx=4)
            ttk.Button(col, text="Apply colour", command=self.do_color).pack(side="left", padx=4)
            ttk.Checkbutton(col, text="Flat colour (plain, loses the texture's detail)", variable=self.v_flat).pack(side="left", padx=8)
            ws.flow(col)

            ad = ws.section("Add a lamp", opened=False).body
            r0 = line(ad)
            ttk.Label(r0, text="Kind").pack(side="left")
            c = ttk.Combobox(r0, textvariable=self.a_role, values=list(ROLES), state="readonly", width=24)
            c.pack(side="left", padx=4)
            c.bind("<<ComboboxSelected>>", lambda _e: self.set_role())
            ws.flow(r0)
            r0b = line(ad)
            ttk.Label(r0b, text="in parts:").pack(side="left")
            for t in (0x110, 0x111, 0x112, 0x113):
                ttk.Checkbutton(r0b, text="%s %s" % (hex(t), SHORT[t]), variable=self.a_parts[t]).pack(side="left", padx=3)
            ws.flow(r0b)
            r1 = line(ad)
            for lab, var, vals, w in (("Facing", self.a_facing, FACINGS, 10), ("Shape", self.a_shape, SHAPES, 10),
                                      ("Colour", self.a_tint, list(TINTS), 10)):
                ttk.Label(r1, text=lab).pack(side="left", padx=(0, 2))
                ttk.Combobox(r1, textvariable=var, values=list(vals), state="readonly", width=w).pack(side="left", padx=(0, 8))
            for lab, var in (("Width", self.a_w), ("Height", self.a_h), ("Glow %", self.a_strength)):
                ttk.Label(r1, text=lab).pack(side="left", padx=(0, 2))
                ttk.Entry(r1, textvariable=var, width=6).pack(side="left", padx=(0, 8))
            ws.flow(r1)
            r2 = line(ad)
            for lab, var in (("X", self.a_x), ("Y", self.a_y), ("Z", self.a_z)):
                ttk.Label(r2, text=lab).pack(side="left", padx=(0, 2))
                ttk.Entry(r2, textvariable=var, width=7).pack(side="left", padx=(0, 8))
            ttk.Checkbutton(r2, text="Snap to body", variable=self.a_snap).pack(side="left", padx=4)
            ttk.Checkbutton(r2, text="Tilt to the surface", variable=self.a_tilt).pack(side="left", padx=4)
            ttk.Button(r2, text="Use the selected lamp's spot", command=self.use_spot).pack(side="left", padx=8)
            ws.flow(r2)
            r3 = line(ad)
            ttk.Label(r3, text="Count").pack(side="left")
            ttk.Entry(r3, textvariable=self.a_count, width=5).pack(side="left", padx=(2, 8))
            ttk.Label(r3, text="Step X Y Z").pack(side="left")
            for v in (self.a_sx, self.a_sy, self.a_sz):
                ttk.Entry(r3, textvariable=v, width=6).pack(side="left", padx=2)
            ttk.Checkbutton(r3, text="Both sides (mirror image)", variable=self.a_mirror).pack(side="left", padx=12)
            ttk.Button(r3, text="Add lamp", command=self.do_add).pack(side="left", padx=8)
            ws.flow(r3)

            ln = ws.section("Lens covers (dark piece over the headlights to clear glass)", opened=False).body
            lr = line(ln)
            ttk.Label(lr, text="Black below").pack(side="left")
            ttk.Entry(lr, textvariable=self.l_dark, width=5).pack(side="left", padx=(2, 10))
            ttk.Label(lr, text="Reach x").pack(side="left")
            ttk.Entry(lr, textvariable=self.l_reach, width=5).pack(side="left", padx=(2, 10))
            ws.flow(lr)
            lr2 = line(ln)
            ttk.Button(lr2, text="Show what would turn to glass", command=self.lens_show).pack(side="left", padx=4)
            ttk.Button(lr2, text="Make it clear glass", command=self.lens_apply).pack(side="left", padx=4)
            ttk.Button(lr2, text="Hide preview", command=self.lens_hide).pack(side="left", padx=4)
            ws.flow(lr2)
            ttk.Label(ln, textvariable=self.lens_msg, foreground="#c9d4e3", wraplength=340, justify="left").pack(fill="x", pady=(4, 0))

        # ------------------------------------------------------------ list
        def refresh(self, keep=None):
            """re-read the lamps from the pack; `keep` = keys (or Lamp objects) to select afterwards"""
            self.lamps = sorted(find_lamps(self.pack), key=lambda l: (l.tag, SIDE_ORDER[l.side], float(l.center[0]), float(l.center[2]), float(l.center[1])))
            self.fill(keep)

        def fill(self, keep=None):
            if keep is None:
                keep = [l.key for l in self.selected()]
            keep = {k.key if isinstance(k, Lamp) else k for k in keep}
            want = self.v_filter.get()
            self.tree.delete(*self.tree.get_children())
            show = []
            for i, l in enumerate(self.lamps):
                if want != "All parts" and (want.startswith("High brake") != (l.tag in HIGH_BRAKE) or (l.tag not in HIGH_BRAKE and state_name(l.tag) != want)):
                    continue
                c, s = l.center, l.size
                self.tree.insert("", "end", iid=str(i), values=(short_name(l.tag), l.side, "%+.2f" % c[0], "%.2f" % c[1], "%+.2f" % c[2],
                                                                 "%.2f x %.2f x %.2f" % tuple(s), l.ntri))
                if l.key in keep:
                    show.append(str(i))
            if show:
                self.tree.selection_set(show)
            self.on_select()

        def selected(self):
            try:
                return [self.lamps[int(i)] for i in self.tree.selection()]
            except (ValueError, IndexError):
                return []

        def select_side(self, side):
            ids = [str(i) for i, l in enumerate(self.lamps) if l.side == side and self.tree.exists(str(i))]
            self.tree.selection_set(ids)

        def select_part(self):
            tags = {l.tag for l in self.selected()}
            self.tree.selection_set([str(i) for i, l in enumerate(self.lamps) if l.tag in tags and self.tree.exists(str(i))])

        def on_select(self):
            ls = self.selected()
            if not ls:
                self.sel_lbl.configure(text="Nothing selected.")
            else:
                lo, hi = np.min([l.lo for l in ls], 0), np.max([l.hi for l in ls], 0)
                c, s = (lo + hi) / 2, hi - lo
                for v, x in zip((self.px, self.py, self.pz), c):
                    v.set("%.3f" % x)
                for v, x in zip((self.sw, self.sh, self.sd), s):
                    v.set("%.3f" % x)
                nl = [l for l in ls if l.tag != 0]
                if nl:
                    self.c_strength.set(str(int(round(lamp_color(self.pack, nl)[1] * 100))))
                self.sel_lbl.configure(text="%d selected (%s), %d triangles. Position = middle of the selection, size = the box around it." % (
                    len(ls), ", ".join(sorted({short_name(l.tag) for l in ls})), sum(l.ntri for l in ls)))
            self.render()

        # ------------------------------------------------------------ editing
        def num(self, var, name, lo=None, hi=None):
            try:
                v = float(var.get().replace(",", "."))
            except ValueError:
                raise LampError("%s must be a number." % name)
            if lo is not None and v < lo or hi is not None and v > hi:
                raise LampError("%s must be between %s and %s." % (name, lo, hi))
            return v

        def partners(self, ls):
            """the lamp on the other side of the car for each selected one (same part, mirrored centre, same size)"""
            out = []
            ids = {l.key for l in ls}
            for l in ls:
                best = None
                for o in self.lamps:
                    if o.key in ids or o.tag != l.tag:
                        continue
                    if abs(o.center[0] + l.center[0]) < 0.3 and abs(o.center[1] - l.center[1]) < 0.3 and abs(o.center[2] - l.center[2]) < 0.3 \
                            and abs(o.ntri - l.ntri) <= 2 and abs(l.center[0]) > 0.35:
                        best = o
                        break
                if best and best.key not in ids and all(best.key != p.key for p in out):
                    out.append(best)
            return out

        def edit(self, fn, label, select_new=None):
            """run fn(pack, lamps, partners) on a copy-safe undo point; refresh"""
            if not self.pack:
                self.status.set("Open a car in Parts & viewport first.")
                return
            ls = self.selected()
            if not ls and select_new is None:
                self.status.set("Select a lamp first.")
                return
            snap = snapshot(self.pack)
            host.snapshot()                                     # the page's one Undo point (taken before anything changes)
            keys = [l.key for l in ls]
            pa0 = self.partners(ls) if self.v_both.get() else []
            try:
                lk, lkp = [], []
                if self.v_link.get() and ls:
                    lk = distinct([x for l in ls for x in linked_pieces(self.pack, l)], ls)
                    lkp = distinct([x for l in pa0 for x in linked_pieces(self.pack, l)], ls + pa0 + lk)
                res = fn(self.pack, ls + lk, pa0 + lkp)
            except (LampError, we.EditError) as ex:
                restore(self.pack, snap)
                if host.undo:
                    host.undo.pop()
                self.refresh(keys)
                messagebox.showwarning("Lamps", str(ex), parent=root)
                return
            self.committed("Lamps: " + label)
            self.refresh(res if res is not None else keys)
            self.status.set(label + (" (and %d linked piece(s) in the other part)" % (len(lk) + len(lkp)) if (lk or lkp) else ""))

        def set_position(self):
            try:
                t = np.array([self.num(self.px, "X"), self.num(self.py, "Y"), self.num(self.pz, "Z")])
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            ls = self.selected()
            if not ls:
                return
            c = (np.min([l.lo for l in ls], 0) + np.max([l.hi for l in ls], 0)) / 2
            d = t - c

            def fn(p, ls, pa):
                transform(p, ls, move=d)
                if pa:
                    transform(p, pa, move=(-d[0], d[1], d[2]))
                return None
            self.edit(fn, "Moved %d lamp(s) by %+.2f %+.2f %+.2f." % (len(ls), *d))

        def nudge(self, d):
            try:
                st = self.num(self.step, "Step", 0.0001)
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            m = np.array(d, float) * st

            def fn(p, ls, pa):
                transform(p, ls, move=m)
                if pa:
                    transform(p, pa, move=(-m[0], m[1], m[2]))
                return None
            self.edit(fn, "Moved %+.2f %+.2f %+.2f." % tuple(m))

        def set_size(self):
            try:
                t = np.array([self.num(self.sw, "W", 0), self.num(self.sh, "H", 0), self.num(self.sd, "D", 0)])
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            ls = self.selected()
            if not ls:
                return
            s = np.max([l.hi for l in ls], 0) - np.min([l.lo for l in ls], 0)
            f = np.where(s > 1e-6, t / np.maximum(s, 1e-6), 1.0)
            grp = len(ls) > 1

            def fn(p, ls, pa):
                transform(p, ls, scale=f, group=grp)
                if pa:
                    transform(p, pa, scale=f, group=grp)
                return None
            self.edit(fn, "Resized: x%.2f x%.2f x%.2f." % tuple(f))

        def do_turn(self):
            try:
                r = (self.num(self.rx, "Pitch"), self.num(self.ry, "Yaw"), self.num(self.rz, "Roll"))
            except LampError as ex:
                self.status.set(str(ex), "error")
                return

            def fn(p, ls, pa):
                transform(p, ls, rot=r, group=len(ls) > 1)
                if pa:
                    transform(p, pa, rot=(r[0], -r[1], -r[2]), group=len(ls) > 1)
                return None
            self.edit(fn, "Turned %.1f / %.1f / %.1f degrees." % r)

        def do_mirror(self):
            self.edit(lambda p, ls, pa: copy_lamps(p, ls, mirror_x=True), "Mirror copies added on the other side.")

        def do_dup(self):
            try:
                st = self.num(self.step, "Step", 0.0001)
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            self.edit(lambda p, ls, pa: copy_lamps(p, ls, offset=(st * 5, 0, 0)), "Duplicated (moved %.2f to the right; move the copies where you want them)." % (st * 5))

        def do_flip(self, axis):
            def fn(p, ls, pa):
                flip(p, ls, axis)
                return None
            self.edit(fn, "Flipped.")

        def do_delete(self):
            ls = self.selected()
            if ls and UI.confirm_destructive(messagebox, root, "Lamps", "Delete %d selected lamp piece%s?" % (len(ls), "" if len(ls) == 1 else "s"), undoable=True):
                def fn(p, ls, pa):
                    delete_lamps(p, ls + pa)
                    return []
                self.edit(fn, "Deleted.")

        def do_split(self):
            try:
                x = self.num(self.cutx, "Split X")
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            ls = self.selected()
            if len(ls) != 1:
                self.status.set("Select one lamp piece to split.")
                return

            def fn(p, ls, pa):
                return split_at(p, ls[0], 0, x)
            self.edit(fn, "Split at X = %.2f (the two halves are now separate lamps)." % x)

        def committed(self, msg):
            """the car was changed by this module: the page redraws, marks the car changed and refreshes Parts' own list"""
            self._mine = True
            try:
                host.edited(msg)
            finally:
                self._mine = False

        def do_undo(self):
            if not host.undo:
                self.status.set("Nothing to undo.")
                return
            host.do_undo()                                      # the page's Undo; it tells this module through pack_changed
            self.status.set("Undone.")

        # ------------------------------------------------------------ adding
        def set_role(self):
            parts, facing, shape, tint, w, h = ROLES[self.a_role.get()]
            for t, v in self.a_parts.items():
                v.set(t in parts)
            self.a_facing.set(facing)
            self.a_shape.set(shape)
            self.a_tint.set(tint)
            self.a_w.set(str(w))
            self.a_h.set(str(h))
            if self.pack:
                self.guess_spot(facing)

        def guess_spot(self, facing):
            body = self.pack.chunk(0)
            if not body:
                return
            lo, hi = np.array(body.lo), np.array(body.hi)
            x = round(float(hi[0]) * 0.45, 2)
            self.a_x.set(str(x))
            self.a_y.set(str(round(float(lo[1] + (hi[1] - lo[1]) * (0.35 if facing == "Front" else 0.5)), 2)))
            self.a_z.set(str(round(float(hi[2] if facing == "Front" else lo[2]), 2)))

        def use_spot(self):
            ls = self.selected()
            if not ls:
                self.status.set("Select a lamp first.")
                return
            c = np.mean([l.center for l in ls], 0)
            self.a_x.set("%.3f" % c[0])
            self.a_y.set("%.3f" % c[1])
            self.a_z.set("%.3f" % c[2])
            self.a_facing.set("Front" if c[2] > 0 else "Rear")

        def do_add(self):
            if not self.pack:
                self.status.set("Open a car first.")
                return
            try:
                states = tuple(t for t, v in self.a_parts.items() if v.get())
                w, h = self.num(self.a_w, "Width", 0.01), self.num(self.a_h, "Height", 0.01)
                x, y, z = self.num(self.a_x, "X"), self.num(self.a_y, "Y"), self.num(self.a_z, "Z")
                cnt = int(self.num(self.a_count, "Count", 1, 200))
                st = (self.num(self.a_sx, "Step X"), self.num(self.a_sy, "Step Y"), self.num(self.a_sz, "Step Z"))
                stre = self.num(self.a_strength, "Glow %", 0, 100) / 100.0
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            facing = self.a_facing.get()
            tint, shape, mirror = self.a_tint.get(), self.a_shape.get(), self.a_mirror.get()
            snap, tilt = self.a_snap.get(), self.a_tilt.get()
            made = []

            def fn(p, ls, pa):
                made.extend(add_lamp(p, states, shape, w, h, (x, y, z), facing, tint, stre, cnt, st, mirror, snap, tilt))
                return made
            self.edit(fn, "Added %d lamp piece(s)%s." % (cnt * (2 if mirror else 1) * len(states),
                                                         (" (snapped to the body%s)" % (" and tilted to it" if tilt else "")) if snap else ""), select_new=True)
            if made:
                self.v_light.set("All lamp parts")

        # ------------------------------------------------------------ colour, lens, other kits
        def do_color(self):
            try:
                s_ = self.num(self.c_strength, "Glow %", 0, 100) / 100.0
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            tint = self.c_tint.get()
            tint = None if tint.startswith("Keep") else tint
            flat = self.v_flat.get()

            def fn(p, ls, pa):
                tg = [l for l in ls + pa if l.tag != 0]
                if not tg:
                    raise LampError("Pick lamp parts (not lens glass) to colour.")
                if flat:
                    tg = flatten(p, tg)
                recolor(p, tg, tint, s_)
                return tg if flat else None
            self.edit(fn, "Colour set (%s, %d%%%s)." % (tint or "kept", round(s_ * 100), ", flat" if flat else ""))

        def lens_params(self):
            return self.num(self.l_dark, "Black below", 0, 255), self.num(self.l_reach, "Reach", 0.1, 3)

        def lens_show(self):
            if not self.pack:
                self.status.set("Open a car in Parts & viewport first.")
                return
            try:
                dark, reach = self.lens_params()
                rep = we.fix_headlights(self.pack, dry=True, dark=dark, reach=reach)
            except (LampError, we.EditError) as ex:
                self.status.set(str(ex), "error")
                return
            self.lens_dry = rep["cover"] or None
            self.lens_msg.set(" ".join(rep["notes"]) + ("  Pink in the picture = what would change. If it covers black trim, lower 'Black below' or 'Reach'." if rep["cover"] else ""))
            self.render()

        def lens_hide(self):
            self.lens_dry = None
            self.lens_msg.set("")
            self.render()

        def lens_apply(self):
            if not self.pack:
                self.status.set("Open a car in Parts & viewport first.")
                return
            try:
                dark, reach = self.lens_params()
            except LampError as ex:
                self.status.set(str(ex), "error")
                return
            snap = snapshot(self.pack)
            host.snapshot()
            try:
                rep = we.fix_headlights(self.pack, dark=dark, reach=reach)
            except (LampError, we.EditError) as ex:
                restore(self.pack, snap)
                if host.undo:
                    host.undo.pop()
                messagebox.showwarning("Lamps", str(ex), parent=root)
                return
            self.lens_dry = None
            self.committed("Lamps: lens covers made clear")
            self.lens_msg.set(" ".join(rep["notes"]))
            self.refresh([])
            self.status.set("Lens covers done: %d triangles became clear glass." % rep["covers"])

        def copy_kits(self):
            if not self.pack or not self.path:
                self.status.set("Open a car in Parts & viewport first.")
                return
            sib = sibling_kits(self.path)
            warn = ("Only the lamp parts (0x110-0x113) of the other pack are replaced by the ones in this window; the old files are kept as .bak.\n"
                    "A kit with different bumpers or tail lamps may need its lamps moved afterwards.")
            if sib:
                a = messagebox.askyesnocancel("Copy lamps", "Copy this car's lamps to:\n  %s\n\nYes = these, No = choose other files.\n\n%s" % (
                    "\n  ".join(os.path.basename(x) for x in sib), warn), parent=root)
                if a is None:
                    return
                targets = sib if a else []
            else:
                targets = []
            if not targets:
                targets = list(filedialog.askopenfilenames(parent=root, title="Copy the lamps to these packs", initialdir=os.path.dirname(self.path),
                                                           filetypes=[("Car pack", "*.wpk")]))
            targets = [t for t in targets if os.path.abspath(t) != os.path.abspath(self.path)]
            if not targets:
                return
            done, bad = [], []
            for f in targets:
                try:
                    d = we.Pack.load(f)
                    copy_lamp_parts(self.pack, d, self.stock)
                    d.save(f)
                    done.append(os.path.basename(f))
                except Exception as ex:
                    bad.append("%s: %s" % (os.path.basename(f), ex))
            self.status.set("Lamps copied to %d pack(s)%s." % (len(done), "; %d failed" % len(bad) if bad else ""))
            messagebox.showinfo("Copy lamps", ("Done: %s" % ", ".join(done) if done else "Nothing copied.") + ("\n\nFailed:\n" + "\n".join(bad) if bad else ""), parent=root)

        # ------------------------------------------------------------ the shared viewport
        def on_enter(self):
            """this module came to the front: attach the overlay, look at the lamps"""
            self.active = True
            gl = self.gl
            if gl is None:
                self.status.set("Lamps are drawn in the GPU viewport: switch it on in Parts & viewport (GPU viewport).", "warning")
            else:
                self.ensure_layer()
            self.refresh()
            self.render()

        def on_leave(self):
            self.active = False
            self.drop_layer()

        def pack_changed(self, kind):
            """the page opened a different car, or the car was edited / undone from somewhere else"""
            if self._mine:
                return
            if kind == "open":
                self._auto_side = None
                self.lens_dry = None
            if self.pack:
                self.refresh()
                self.status.set("%s: %d lamp pieces." % (os.path.basename(self.path or "car"), len(self.lamps))) if kind == "open" else None
            else:
                self.lamps = []
                self.fill()

        def ensure_layer(self):
            gl = self.gl
            if gl is None or GLV is None:
                return
            if self.layer is None:
                self.layer = GLV.TriLayer()
            if self.layer not in gl.extras:
                gl.extras.append(self.layer)

        def drop_layer(self):
            gl = self.gl
            if gl is not None and self.layer in gl.extras:
                gl.extras.remove(self.layer)
            if self.layer is not None:
                self.layer.clear()

        # what the page's viewport shows while this module is in front
        def shown_tags(self, base):
            return self.visible_tags()

        def selection_tags(self):
            return []                                           # the blue overlay marks the lamps; no whole-part outline

        def viewport_changed(self):
            """called by the page each time it has set the model / visibility: add this module's look, framing and overlay"""
            gl = self.gl
            if gl is None or not self.active or not self.pack:
                return
            self.ensure_layer()
            night = self.v_night.get()
            gl.set_look("Game: Tokyo, night" if night else GLV.DEFAULT_SHADING)
            gl.set_background(*((((0.05, 0.06, 0.09), (0.02, 0.025, 0.04))) if night else ((0.24, 0.29, 0.36), (0.09, 0.115, 0.15))))
            ls = self.selected()
            self.layer.set_triangles(self.overlay_triangles(ls))

        def render(self):
            if self.pack:
                host.render(reuse=True)

        def on_combo(self, which):
            """View / Light / Zoom changed: View turns the camera, the others only re-frame or re-light"""
            self.render()

        def fit_view(self):
            if self.cam is not None:
                self.cam.fit()
                self.render()

        def overlay_triangles(self, ls):
            """the selected lamps (blue) and the lens candidates (pink) as see-through triangles, x y z r g b a per vertex"""
            groups = []
            if ls:
                groups.append(((0.0, 0.59, 1.0, 0.62), [(l.tag, l.bi, np.asarray(l.rows)) for l in ls]))
            if self.lens_dry and self.pack.chunk(0):
                groups.append(((1.0, 0.0, 0.75, 0.62), [(0, bi, np.asarray(rows)) for bi, rows in self.lens_dry.items()
                                                          if bi < len(self.pack.chunk(0).batches)]))
            out = []
            for col, items in groups:
                for tag, bi, rows in items:
                    ch = self.pack.chunk(tag)
                    if ch is None or bi >= len(ch.batches) or not len(rows):
                        continue
                    b = ch.batches[bi]
                    T = b.I.reshape(-1, 3).astype(np.int64)[rows]
                    P = np.asarray(b.V["pos"], np.float32)[T.reshape(-1)]
                    out.append(np.hstack([P, np.tile(np.asarray(col, np.float32), (len(P), 1))]))
            return np.vstack(out) if out else np.zeros((0, 7), np.float32)

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
            d, self._drag = self._drag, None
            if d and not d["moved"] and d["btn"] == 1:
                self.pick_lamp(e.x, e.y)

        def vp_wheel(self, e):
            if self.gl is not None:
                up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
                self.cam.zoom_by(1.2 if up else 1 / 1.2)
                self.gl.request_draw()
            return "break"                                           # the page's scroll bar must not move with the wheel

        def pick_lamp(self, x, y):
            """a click on a lamp piece selects it: the part under the pointer, then the piece of it whose middle is nearest"""
            tag = self.gl.pick(x, y)
            if tag is None or not is_lamp_tag(tag) and tag not in HIGH_BRAKE and tag != 0:
                return
            cand = [(i, l) for i, l in enumerate(self.lamps) if l.tag == tag and self.tree.exists(str(i))]
            last = getattr(getattr(self.gl, "r", None), "last", None)
            if not cand:
                return
            best = cand[0][0]
            if last:
                view, bd = last[1], None
                for i, l in cand:
                    px, py = view.project(l.center)
                    dd = (px - x) ** 2 + (py - y) ** 2
                    if bd is None or dd < bd:
                        best, bd = i, dd
            self.tree.selection_set([str(best)])
            self.tree.see(str(best))

        # ------------------------------------------------------------ what is drawn
        def visible_tags(self):
            """parts of the car drawn in this module: everything Parts shows (hidden parts stay hidden), the lamp parts by the Light box"""
            hide = host.hidden
            mode = self.v_light.get()
            out = set()
            for c in self.pack.chunks:
                t = c.tag
                if t == we.WPK_SHADOW or t in hide and not is_lamp_tag(t):
                    continue
                if is_lamp_tag(t) and mode != "All lamp parts" and not we.light_shown(t, mode):
                    continue
                out.add(t)
            return out

    return Tab()


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
