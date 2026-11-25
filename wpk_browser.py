"""Asset-browser / viewport / inspector logic for the Models (WPK) tab.

Pure Python + numpy, no Tk: everything here can be unit-tested without a display.
It only READS a wpk_edit.Pack; nothing in this module changes WPK data or serialisation.
"""
import time

import numpy as np

SHADOW = 0xFFFF
CATEGORIES = ("Body", "Wheels", "Wing", "Bonnet", "Lights", "Mirrors", "Exhaust", "Other")
BLEND = ("opaque", "alpha", "additive", "blend 3")


# ------------------------------------------------------------------------------------------------
# categories and rows
# ------------------------------------------------------------------------------------------------
def category(tag):
    """browser category of a chunk tag (same tag ranges as wpk_edit.tag_name / wpk_view.PART_GROUPS)"""
    if tag == 0:
        return "Body"
    if 0x100 <= tag <= 0x103 or 0x12A <= tag <= 0x12D:
        return "Wheels"
    if 0x122 <= tag <= 0x125 or 0x190 <= tag <= 0x19E:
        return "Wing"
    if 0x126 <= tag <= 0x129 or 0x180 <= tag <= 0x188:
        return "Bonnet"
    if 0x110 <= tag <= 0x114 or 0x140 <= tag <= 0x17F:
        return "Lights"
    if 0x120 <= tag <= 0x121 or 0x1A0 <= tag <= 0x1A2:
        return "Mirrors"
    if 0x130 <= tag <= 0x131:
        return "Exhaust"
    return "Other"


def tex_label(pack, ti):
    if ti == 0xFFFF:
        return "(untextured)"
    if 0 <= ti < len(pack.tex):
        return pack.tex[ti]["name"]
    return "(missing #%d)" % ti


class Row:
    __slots__ = ("tag", "name", "cat", "draws", "verts", "tris", "visible", "engine", "meta")

    def __init__(self, tag, name, cat, draws, verts, tris, visible, engine, meta):
        self.tag, self.name, self.cat, self.draws, self.verts, self.tris = tag, name, cat, draws, verts, tris
        self.visible, self.engine, self.meta = visible, engine, meta

    @property
    def label(self):
        return "%s  (%d)%s" % (self.name, self.tag, "" if self.engine else " *")


def build_rows(pack, hidden, name_fn, engine_fn):
    rows = []
    for c in pack.chunks:
        nb, nv, nt = c.counts()
        tex = sorted({tex_label(pack, b.tex) for b in c.batches})
        rows.append(Row(c.tag, name_fn(c.tag), category(c.tag), nb, nv, nt, c.tag not in hidden, bool(engine_fn(c.tag)),
                        " ".join(tex).lower()))
    return rows


def filter_rows(rows, query="", cats=()):
    """rows whose category is in `cats` (empty = all) and that match every word of `query`.
    A word matches the part name, the category, the tag (decimal, 0x hex, or hex digits) or a texture name."""
    words = [w for w in query.lower().split() if w]
    cats = set(cats)
    out = []
    for r in rows:
        if cats and r.cat not in cats:
            continue
        hay = "%s %s %d 0x%x %x %s" % (r.name.lower(), r.cat.lower(), r.tag, r.tag, r.tag, r.meta)
        if all(w in hay for w in words):
            out.append(r)
    return out


def group_rows(rows):
    """[(category, [rows])] in CATEGORIES order, empty categories left out"""
    return [(c, [r for r in rows if r.cat == c]) for c in CATEGORIES if any(r.cat == c for r in rows)]


def valid_selection(sel, pack):
    """the selected tags that still exist in `pack` (after a kit switch, delete, undo ...)"""
    have = {c.tag for c in pack.chunks} if pack else set()
    return [t for t in sel if t in have]


# ------------------------------------------------------------------------------------------------
# camera: orbit / pan / zoom on top of wpk_view.rasterize(yaw, pitch, window=...)
# ------------------------------------------------------------------------------------------------
class Camera:
    MIN_ZOOM, MAX_ZOOM = 0.25, 40.0

    def __init__(self, yaw=-35.0, pitch=14.0):
        self.yaw, self.pitch = float(yaw), float(pitch)
        self.zoom = 1.0
        self.pan = [0.0, 0.0]          # picture-plane offset of the view centre from the fitted centre (model units)

    def set_angles(self, yaw, pitch):
        self.yaw, self.pitch = float(yaw), float(pitch)

    def fit(self):
        self.zoom = 1.0
        self.pan = [0.0, 0.0]

    def orbit(self, dx, dy, deg_per_px=0.4):
        """a full 360 degree turn on both axes: drag past the top and the car goes over and upside down, nothing stops you.
        Upside down (|pitch| > 90) a drag to the right still turns the car to the right, so the controls never feel mirrored."""
        flip = -1.0 if abs(self.pitch) > 90.0 else 1.0
        self.yaw = (self.yaw + flip * dx * deg_per_px + 180.0) % 360.0 - 180.0
        self.pitch = (self.pitch + dy * deg_per_px + 180.0) % 360.0 - 180.0

    def zoom_by(self, factor):
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))

    def pan_px(self, dx, dy, units_per_px):
        """drag the picture by (dx, dy) screen pixels; units_per_px = model units one pixel covers at the current zoom"""
        self.pan[0] -= dx * units_per_px
        self.pan[1] += dy * units_per_px

    def window(self, cx, cy, hw0, W, H, margin=24):
        """(window, model units per pixel) for wpk_view.rasterize(window=...).  cx, cy, hw0 = the fitted centre and
        half width (view_extent).  The window is ALWAYS given so that zoom 1 and zoom 2 frame the car the same way."""
        hw = hw0 / self.zoom
        hh = hw * H / max(W, 1)
        s = min((W - margin) / (2 * hw), (H - margin) / (2 * hh))      # rasterize's own scale
        return (cx + self.pan[0], cy + self.pan[1], hw), 1.0 / max(s, 1e-9)


def view_extent(chunks, yaw, pitch, view_point, aspect=1.0, fill=0.92):
    """fitted (cx, cy, half_width) for a picture of width / height = aspect.
    The fit is the BOUNDING SPHERE of the car (all parts, hidden ones too, no shadow), so the car's centre stays at the centre of the
    picture and its size stays the same whatever the angle: orbiting only turns the car.  (Fitting the projected box instead made
    the car drift and breathe while it was dragged.)  `fill` < 1 trims the sphere a little: a car is not a ball, its corners are empty."""
    lo = hi = None
    for c in chunks:
        if c[8] == SHADOW:
            continue
        a, b = np.array(c[0:3], np.float64), np.array(c[3:6], np.float64)
        lo = a if lo is None else np.minimum(lo, a)
        hi = b if hi is None else np.maximum(hi, b)
    if lo is None:
        return 0.0, 0.0, 1.0
    centre = (lo + hi) / 2
    radius = float(np.linalg.norm(hi - lo) / 2) * fill
    cx, cy = view_point(centre, yaw, pitch)
    return float(cx), float(cy), float(radius * max(1.0, aspect) + 1e-3)


# ------------------------------------------------------------------------------------------------
# picking and highlight, from the rasteriser's per-pixel batch map (Gbuf.M)
# ------------------------------------------------------------------------------------------------
def batch_tags(chunks):
    """{batch index: chunk tag} for chunks as in wpk_edit.view_model: (6 floats, first, n, tag)"""
    out = {}
    for c in chunks:
        for bi in range(c[6], c[6] + c[7]):
            out[bi] = c[8]
    return out


def tag_map(M, bmap):
    """per-pixel chunk tag (int32; -1 = nothing drawn) from the batch map M"""
    t = np.full(M.shape, -1, np.int32)
    if not len(bmap):
        return t
    lut = np.full(max(bmap) + 2, -1, np.int64)
    for bi, tg in bmap.items():
        lut[bi] = tg
    ok = M >= 0
    t[ok] = lut[np.minimum(M[ok], len(lut) - 1)]
    return t


def pick_tag(T, x, y, radius=3):
    """chunk tag under pixel (x, y) of tag map T; looks up to `radius` px around for thin parts. None if nothing."""
    h, w = T.shape
    x, y = int(x), int(y)
    if not (0 <= x < w and 0 <= y < h):
        return None
    if T[y, x] >= 0:
        return int(T[y, x])
    best, bd = None, None
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            xx, yy = x + dx, y + dy
            if 0 <= xx < w and 0 <= yy < h and T[yy, xx] >= 0:
                d = dx * dx + dy * dy
                if bd is None or d < bd:
                    best, bd = int(T[yy, xx]), d
    return best


def selection_overlay(T, tags, fill=(255, 140, 0, 70), edge=(255, 200, 0, 255)):
    """RGBA uint8 (H, W, 4): translucent fill + 2px outline on the pixels of the selected tags; None if none visible"""
    if not len(tags):
        return None
    m = np.isin(T, list(tags))
    if not m.any():
        return None
    inner = m.copy()
    for _ in range(2):                                   # erode twice -> outline of 2 px
        e = inner.copy()
        e[1:, :] &= inner[:-1, :]
        e[:-1, :] &= inner[1:, :]
        e[:, 1:] &= inner[:, :-1]
        e[:, :-1] &= inner[:, 1:]
        e[0, :] = e[-1, :] = False
        e[:, 0] = e[:, -1] = False
        inner = e
    out = np.zeros(T.shape + (4,), np.uint8)
    out[m] = fill
    out[m & ~inner] = edge
    return out


# ------------------------------------------------------------------------------------------------
# properties
# ------------------------------------------------------------------------------------------------
def part_info(pack, tags, hidden, name_fn, engine_fn, stock=None):
    """read-only facts about the selected chunks (a dict), straight from the Pack"""
    chunks = [c for c in (pack.chunk(t) for t in tags) if c is not None]
    info = dict(count=len(chunks), tags=[c.tag for c in chunks])
    if not chunks:
        return info
    draws = sum(len(c.batches) for c in chunks)
    verts = sum(len(b.V) for c in chunks for b in c.batches)
    tris = sum(len(b.I) // 3 for c in chunks for b in c.batches)
    info.update(draws=draws, verts=verts, tris=tris,
                visible=all(c.tag not in hidden for c in chunks),
                mixed_visibility=len({c.tag in hidden for c in chunks}) > 1)
    if len(chunks) == 1:
        c = chunks[0]
        info.update(name=name_fn(c.tag), tag=c.tag, category=category(c.tag), engine=bool(engine_fn(c.tag)))
    else:
        info.update(name="%d parts" % len(chunks), tag=None, category="", engine=all(engine_fn(c.tag) for c in chunks))
    pts = [b.V["pos"] for c in chunks for b in c.batches if len(b.V)]
    if pts:
        p = np.concatenate(pts)
        lo, hi = p.min(0), p.max(0)
        info["bbox"] = (tuple(float(x) for x in lo), tuple(float(x) for x in hi))
        info["size"] = tuple(float(x) for x in (hi - lo))
        info["centre"] = tuple(float(x) for x in (lo + hi) / 2)
    mats, texrefs = [], {}
    for c in chunks:
        for n, b in enumerate(c.batches):
            mats.append(dict(tag=c.tag, draw=n, tex=tex_label(pack, b.tex), blend=BLEND[b.flags & 3], flags=b.flags,
                             prog=b.prog, env=tex_label(pack, b.env) if b.env < len(pack.tex) else "-",
                             spec=tex_label(pack, b.spec) if b.spec < len(pack.tex) else "-",
                             power=b.power / 10000.0, verts=len(b.V), tris=len(b.I) // 3))
            for role, ti in (("diffuse", b.tex), ("env", b.env), ("spec", b.spec)):
                if role != "diffuse" and ti >= len(pack.tex):
                    continue                                          # no env / spec map: not a reference
                texrefs.setdefault(ti, set()).add(role)
    refs = []
    for ti, roles in sorted(texrefs.items()):
        if ti == 0xFFFF:
            refs.append(dict(name="(untextured)", status="none", w=0, h=0, fmt=0, roles=sorted(roles)))
        elif not (0 <= ti < len(pack.tex)):
            refs.append(dict(name="#%d" % ti, status="MISSING", w=0, h=0, fmt=0, roles=sorted(roles)))
        else:
            t = pack.tex[ti]
            if t["raw"]:
                st = "ok"
            elif stock is not None and stock.tex_index(t["name"]) is not None:
                st = "borrowed (stock pack)"
            else:
                st = "MISSING (borrowed, no stock pack)"
            refs.append(dict(name=t["name"], status=st, w=t["w"], h=t["h"], fmt=t["fmt"], roles=sorted(roles)))
    info.update(materials=mats, textures=refs)
    return info


# ------------------------------------------------------------------------------------------------
# operation log
# ------------------------------------------------------------------------------------------------
LEVELS = ("info", "success", "warning", "error")


class OpLog:
    def __init__(self, limit=500):
        self.entries = []
        self.limit = limit

    def add(self, level, msg, detail=""):
        if level not in LEVELS:
            level = "info"
        e = dict(t=time.time(), level=level, msg=str(msg), detail=str(detail or ""))
        self.entries.append(e)
        del self.entries[:-self.limit]
        return e

    def clear(self):
        self.entries = []

    def counts(self):
        return {lv: sum(1 for e in self.entries if e["level"] == lv) for lv in LEVELS}

    @staticmethod
    def format(e):
        s = "%s  %-7s  %s" % (time.strftime("%H:%M:%S", time.localtime(e["t"])), e["level"].upper(), e["msg"])
        if e["detail"]:
            s += "\n" + "\n".join("    " + ln for ln in e["detail"].splitlines())
        return s

    def text(self):
        return "\n".join(self.format(e) for e in self.entries)
