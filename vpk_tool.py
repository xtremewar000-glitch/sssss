#!/usr/bin/env python3
"""VPK tool 2: edit the stickers (vinyls) of a Re3DX+ car ``.vpk`` file.

    python vpk_tool.py                         opens the app (open a car's .vpk, click a sticker, replace, save)
    python vpk_tool.py info   CAR.vpk          lists what is inside
    python vpk_tool.py replace CAR.vpk NUMBER IMAGE [OUT.vpk] [--size 512|1024|2048]   swap one sticker without the app
    python vpk_tool.py uv CAR.wpk OUT.png [SIZE] [--plain]            the car's exact UV layout, to paint on (default 2048)
    python vpk_tool.py uvall CARS_DIR OUT_DIR [SIZE] [--plain]   the UV template of EVERY car (+ aero kits) in a folder
    python vpk_tool.py uvcheck CAR.wpk STICKER.png OUT.png  draw the UV borders over a sticker to check it fits
    python vpk_tool.py textures CAR.wpk        the car pack's textures (glass, lamps, plate ...): size, format, use
    python vpk_tool.py texexport CAR.wpk NAME OUT.png [SIZE]   one of them as an upright PNG to edit (SIZE: scaled up)
    python vpk_tool.py texguide  CAR.wpk NAME OUT.png [SIZE]  it with the UV borders over it
    python vpk_tool.py texprobe  CAR.wpk NAME OUT.png        where does it show? the car with every spot that uses it in pink
    python vpk_tool.py teximport CAR.wpk NAME IMAGE OUT [--size N] [--dither] [--car]   put an edited PNG back (a decal)
    python vpk_tool.py plc CAR.wpk PLC_FOLDER KIT [OUT.wpk]   arcade car's aero kit -> the body of a .wpk (see plc_import.py)
    python vpk_tool.py wpk info|export|replace|addpart|windows ... edit the 3D parts of a car .wpk (see wpk_edit.py; the app's 2nd tab)
    python vpk_tool.py engine list|copy|put|restore ...   the engine sounds (see engine_tab.py; the app's 5th tab)
    python vpk_tool.py wheels list|swap ...   the wheel packs (see wheels_tab.py; the app's 4th tab)
    python vpk_tool.py meters info|sheet|tile|selftest FILE.bin ...      the meters tab (plain WMS2 files; see meters_edit.py)
    python vpk_tool.py lamps list|selftest CAR.wpk                        the headlight / tail light editor (see lamp_edit.py; the Lamps tab)
    python vpk_tool.py backfire info|selftest backfire.wpk [backfire.bfx]   the exhaust flame files (see backfire_edit.py; the app's 3rd tab)
    python vpk_tool.py newvpk CAR.wpk OUT.vpk [STICKER_FOLDER]   a starter .vpk for a car that has none (names read from the .wpk; 12.png = sticker 12)
    python vpk_tool.py unpack CAR.vpk DIR      PNGs + project.json (older, scripting way)
    python vpk_tool.py pack PROJECT.json OUT.vpk

File format (Re3DX+ tools/stage/pack_vinyl.py, read by src/app.c vinyl_tex):
    "VPK1" or "VPK2", u32 n_entries, u32 n_planes, char page1[4][48]  (name of the car pack's texture the sticker
    is blended onto, by body: normal / aero A / B / C), u8 works[32] (by paint), then n_entries of
    {u8 body, u8 vinyl, u8 works, u8 cb, u8 r, g, b, kind, u32 plane}, then n_planes of
        VPK1: {u32 off, u32 size}                  every plane 512 x 512
        VPK2: {u32 off, u32 size, u16 w, u16 h}    a plane of its own size, up to 2048 x 2048
    (zlib; w x h x 4 premultiplied RGBA for kind 0, w x h x 1 alpha for kind 1 = one colour r g b).
    The tool writes a VPK1 while every picture is 512 x 512 (an old build reads it) and a VPK2 as soon as one is
    bigger; the other pictures of a VPK2 may stay 512.  The game blends onto the car's page, which it grows to the
    sticker's size by itself (render.c wpk_texture_over), so the car's .wpk needs no change.
    The app blends: rgb = paint * (1 - a) + sticker * a, rows bottom-up like IGB images.
"""
import hashlib
import json
import math
import os
import re
import struct
import sys
import threading
import zlib
from collections import defaultdict

import numpy as np
from PIL import Image

try:
    import wpk_view                    # the live car preview (View > Show on the car)
except ImportError:                    # the tool still works without it
    wpk_view = None
try:
    import uv_fit                      # fit a picture to the UV layout with pins
except ImportError:
    uv_fit = None
try:
    import wpk_tex                     # edit the textures inside a car .wpk (the glass, lamps, plate ...)
except ImportError:
    wpk_tex = None
try:
    import wpk_edit                    # the 2nd tab: swap a car's body / wings ... for your own model
except ImportError:
    wpk_edit = None
try:
    import wheels_tab                  # the 4th tab: wheel packs
except ImportError:
    wheels_tab = None
try:
    import engine_tab                  # the 5th tab: engine / exhaust sounds
except ImportError:
    engine_tab = None
try:
    import meters_edit                 # the meters tab: gauge pictures of a plain WMS2 meters file
except ImportError:
    meters_edit = None
try:
    import lamp_edit                   # the lamps tab: headlight / tail light positions, shapes, extra lamps
except ImportError:
    lamp_edit = None
try:
    import backfire_edit               # the 3rd tab: the exhaust flame (backfire.wpk + backfire.bfx)
except ImportError:
    backfire_edit = None
import vpk_ui                          # shared look, confirmations, keyboard shortcuts, saved layout (Milestone 6)

APP = "VPK tool"
VERSION = "2.4"
SIZE = 512                       # the stock sticker size (VPK1: every picture)
SIZES = (512, 1024, 2048)        # what the tool makes a picture at
DEFAULT_SIZE = 512               # new pictures (Edit > Size of new stickers)
MAX_SIDE = 2048                  # the game's limit for a picture (app.c vinyl_tex)
BODIES = ["Normal", "Aero A", "Aero B", "Aero C"]
BODY_TAG = ["normal", "aeroA", "aeroB", "aeroC"]
HEAD = 12 + 4 * 48 + 32          # what app.c reads before the entry table
UNPACKED = re.compile(r"b(\d)_v(\d+)_w(\d+)_cb(\d)", re.I)
IMAGE_EXT = (".png", ".tga", ".bmp", ".jpg", ".jpeg", ".webp")

# The in-game sticker menu ("places"). A sticker's number = its place's first number + its colour (0..9).
# Order taken from Re3DX+ dressdb.h (what each dress-up level unlocks) and menu.c (vinyl_colours).
GROUPS = [(1, 10, "Line 1"), (11, 20, "Line 2"), (21, 30, "Fire 1"), (31, 40, "Fire 2"), (41, 41, "Namco"),
          (42, 42, "Pac-Man"), (43, 47, "Works colours"), (48, 57, "Line 3"), (58, 67, "Fire 3"), (68, 77, "Tribal")]


def group_of(n):
    for a, b, name in GROUPS:
        if a <= n <= b:
            return name
    return "Other"


class VpkError(Exception):
    pass


# ------------------------------------------------------------------------------------------------
# pixels
# ------------------------------------------------------------------------------------------------
def check_size(size):
    if size not in SIZES:
        raise VpkError("A sticker can be 512, 1024 or 2048 pixels square, not %s." % size)
    return int(size)


def plane_side(kind, nbytes):
    """the side S of a plane of nbytes (S x S x 4 for a full-colour picture, S x S for one colour)"""
    per = 1 if kind else 4
    s = math.isqrt(nbytes // per)
    if s * s * per != nbytes:
        raise VpkError("A picture has a size that is not a square.")
    return s


def prepare(im, flip=True, fit="stretch", size=SIZE):
    """PIL image -> premultiplied float32 RGBA, size x size, rows bottom-up if flip."""
    size = check_size(size)
    im = im.convert("RGBA")
    if fit == "fit" and im.width != im.height:
        s = max(im.size)
        canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        canvas.paste(im, ((s - im.width) // 2, (s - im.height) // 2))
        im = canvas
    if flip:
        im = im.transpose(Image.FLIP_TOP_BOTTOM)
    a = np.asarray(im, dtype=np.float32)
    p = np.concatenate([a[..., :3] * (a[..., 3:4] / 255.0), a[..., 3:4]], axis=2)
    h, w = p.shape[:2]
    if (w, h) != (size, size):
        res = Image.BOX if (w >= size and h >= size) else Image.BICUBIC
        p = np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(p[..., c]), "F").resize((size, size), res))
                      for c in range(4)], axis=2)
        # resampling can overshoot: keep it a valid premultiplied picture (0 <= rgb <= alpha <= 255). Where alpha is
        # clipped the colour is scaled with it, so the straight colour stays what the art had (a one-colour design
        # scaled up must still look one-colour, not drift at its edges)
        al = p[..., 3:4].copy()
        p[..., :3] *= np.where(al > 255, 255.0 / np.maximum(al, 1e-6), 1.0)
        p[..., 3] = al[..., 0].clip(0, 255)
        p[..., :3] = np.minimum(p[..., :3].clip(0, None), p[..., 3:4])
    return p


def encode(p):
    """premultiplied RGBA float -> (kind, (r, g, b), plane bytes): a one-colour design keeps its alpha alone."""
    al = p[..., 3]
    seen = al > 8
    if seen.any():
        col = p[..., :3][seen] / al[seen][:, None] * 255.0
        lo, hi = col.min(axis=0), col.max(axis=0)
        if (hi - lo).max() <= 6:
            c = tuple(int(round(v)) for v in (lo + hi) / 2)
            return 1, c, al.round().clip(0, 255).astype(np.uint8).tobytes()
    return 0, (0, 0, 0), p.round().clip(0, 255).astype(np.uint8).tobytes()


def decode(kind, colour, data, flip=True):
    """a plane back to a straight-alpha RGBA PIL image (top-down when flip); its size is read from its length."""
    n = plane_side(kind, len(data))
    if kind == 1:
        al = np.frombuffer(data, np.uint8).reshape(n, n)
        rgba = np.zeros((n, n, 4), np.uint8)
        rgba[..., :3] = colour
        rgba[..., 3] = al
    else:
        p = np.frombuffer(data, np.uint8).reshape(n, n, 4).astype(np.float32)
        al = p[..., 3:4]
        rgb = np.where(al > 0, p[..., :3] * 255.0 / np.maximum(al, 1.0), 0.0)
        rgba = np.concatenate([rgb.clip(0, 255), al], axis=2).round().astype(np.uint8)
    im = Image.fromarray(rgba, "RGBA")
    return im.transpose(Image.FLIP_TOP_BOTTOM) if flip else im


# ------------------------------------------------------------------------------------------------
# the file
# ------------------------------------------------------------------------------------------------
def serialize(page1, works, entries, zplanes, sides=None):
    """entries: (body, vinyl, works, cb, r, g, b, kind, plane) tuples; zplanes: zlib-compressed planes; sides: the
    side of each plane (default 512). All 512 -> a VPK1, else a VPK2 with {off, size, w, h} per plane."""
    sides = list(sides) if sides is not None else [SIZE] * len(zplanes)
    v2 = any(sd != SIZE for sd in sides)
    head = struct.pack("<4sII", b"VPK2" if v2 else b"VPK1", len(entries), len(zplanes))
    head += b"".join(n.encode("latin1", "replace").ljust(48, b"\0")[:48] for n in page1)
    head += bytes((list(works) + [1] * 32)[:32])
    head += b"".join(struct.pack("<8BI", *e) for e in entries)
    off = len(head) + (12 if v2 else 8) * len(zplanes)
    tbl = b""
    for z, sd in zip(zplanes, sides):
        tbl += struct.pack("<IIHH", off, len(z), sd, sd) if v2 else struct.pack("<II", off, len(z))
        off += len(z)
    return head + tbl + b"".join(zplanes)


def read_vpk(path):
    with open(path, "rb") as fh:
        b = fh.read()
    if len(b) < HEAD or b[:4] not in (b"VPK1", b"VPK2"):
        raise VpkError("%s is not a VPK1 / VPK2 sticker file." % os.path.basename(path))
    version = 2 if b[:4] == b"VPK2" else 1
    stride = 12 if version == 2 else 8
    n, npl = struct.unpack_from("<II", b, 4)
    page1 = [b[12 + 48 * i:12 + 48 * (i + 1)].split(b"\0")[0].decode("latin1") for i in range(4)]
    works = list(b[12 + 192:HEAD])
    if HEAD + 12 * n + stride * npl > len(b):
        raise VpkError("The file is truncated.")
    ents = [struct.unpack_from("<8BI", b, HEAD + 12 * i) for i in range(n)]
    planes, zs, dims = [], [], []
    for i in range(npl):
        off, size = struct.unpack_from("<II", b, HEAD + 12 * n + stride * i)
        dims.append(struct.unpack_from("<HH", b, HEAD + 12 * n + stride * i + 8) if version == 2 else (SIZE, SIZE))
        z = b[off:off + size]
        try:
            planes.append(zlib.decompress(z))
        except zlib.error:
            raise VpkError("Picture %d does not inflate: the file is damaged." % i)
        zs.append(z)
    return dict(version=version, page1=page1, works=works, entries=ents, planes=planes, zs=zs, dims=dims)


def info_text(path):
    v = read_vpk(path)
    lines = ["%s: VPK%d, %d stickers, %d pictures" % (os.path.basename(path), v["version"], len(v["entries"]), len(v["planes"])),
             "page-1 textures: " + ", ".join("%s=%s" % (BODIES[i], v["page1"][i] or "-") for i in range(4)),
             "works by paint: " + ",".join(str(x) for x in v["works"][:32])]
    for body, vinyl, wk, cb, r, g, bl, kb, pl in v["entries"]:
        lines.append("  body %d  sticker %3d  works %d  carbon %d  %s  %d px  picture %d" % (
            body, vinyl, wk, cb, "one colour (%d,%d,%d)" % (r, g, bl) if kb & 1 else "full colour",
            v["dims"][pl][0] if pl < len(v["dims"]) else 0, pl))
    return "\n".join(lines)


class Pack:
    """A car's .vpk opened for editing. Entries are dicts: body, vinyl, works, cb, kind, col, plane
    (plus orig = what the file had, to show changes and undo them)."""

    def __init__(self, page1=None, works=None):
        self.page1 = list(page1 or [""] * 4)
        self.works = (list(works or []) + [1] * 32)[:32]
        self.entries = []
        self.planes = []          # dict(data=raw bytes, z=compressed bytes or None, kind=0/1, size=side in pixels)
        self.path = None
        self.version = 1
        self._idx = {}
        self.orig_head = (list(self.page1), list(self.works))

    # ---- load / save
    @classmethod
    def load(cls, path):
        v = read_vpk(path)
        p = cls(v["page1"], v["works"])
        p.path = path
        p.planes = [dict(data=d, z=z, kind=None, size=None) for d, z in zip(v["planes"], v["zs"])]
        for body, vinyl, wk, cb, r, g, b, kb, pl in v["entries"]:
            if pl >= len(p.planes):
                raise VpkError("A sticker points at a missing picture: the file is damaged.")
            kind = kb & 1
            w, h = v["dims"][pl]
            if v["version"] == 1 and kb >> 1:
                # VPK1 files of tool 2.2 kept a picture's size in the kind byte (bits 1-2); read them, the next save
                # writes them as a VPK2
                w = h = SIZE << (kb >> 1)
            elif kb >> 1:
                raise VpkError("Sticker %d uses a format this tool does not know (kind byte %d)." % (vinyl, kb))
            if w != h or not 1 <= w <= MAX_SIDE:
                raise VpkError("Picture %d is %d x %d: this tool edits square pictures up to %d." % (pl, w, h, MAX_SIDE))
            size = w
            pln = p.planes[pl]
            if len(pln["data"]) != size * size * (1 if kind else 4):
                raise VpkError("Picture %d has the wrong size: the file is damaged." % pl)
            if pln["kind"] is not None and (pln["kind"], pln["size"]) != (kind, size):
                raise VpkError("Picture %d is used with two different sizes: the file is damaged." % pl)
            pln["kind"], pln["size"] = kind, size
            e = dict(body=body, vinyl=vinyl, works=wk, cb=cb, kind=kind, col=(r, g, b), plane=pl)
            e["orig"] = (kind, (r, g, b), pl)
            p.entries.append(e)
        p.version = v["version"]
        p._reindex()
        return p

    def _reindex(self):
        self._idx = {hashlib.md5(bytes([pl["kind"] or 0]) + pl["data"]).digest(): i
                     for i, pl in enumerate(self.planes) if pl.get("kind") is not None}

    def plane_for(self, kind, data):
        h = hashlib.md5(bytes([kind]) + data).digest()
        i = self._idx.get(h)
        if i is None:
            i = len(self.planes)
            self.planes.append(dict(data=data, z=None, kind=kind, size=plane_side(kind, len(data))))
            self._idx[h] = i
        return i

    def size_of(self, i):
        """the side in pixels of the picture of entry i"""
        return self.planes[self.entries[i]["plane"]]["size"]

    def check(self):
        names = [n.strip() for n in self.page1]
        if not names[0]:
            raise VpkError("The normal-body texture name is empty (Edit > Car settings).")
        for n in names:
            if len(n.encode("latin1", "replace")) > 47:
                raise VpkError("A texture name is longer than 47 characters: " + n)
        if len(self.works) > 32 or any(not 0 <= int(x) <= 255 for x in self.works):
            raise VpkError("The works table takes at most 32 numbers, each 0..255.")
        for pl in self.planes:
            if pl.get("size") is not None and not 1 <= pl["size"] <= MAX_SIDE:
                raise VpkError("A picture is %d pixels square: the game takes at most %d." % (pl["size"], MAX_SIDE))
        seen = set()
        for e in self.entries:
            key = (e["body"], e["vinyl"], e["works"], e["cb"])
            if key in seen:
                raise VpkError("Sticker %d is in the file twice for the same body/works/carbon." % e["vinyl"])
            seen.add(key)

    def to_bytes(self):
        self.check()
        order = sorted({e["plane"] for e in self.entries})
        remap = {old: i for i, old in enumerate(order)}
        zs = []
        for old in order:
            pl = self.planes[old]
            if pl["z"] is None:
                pl["z"] = zlib.compress(pl["data"], 9)
            zs.append(pl["z"])
        ents = [(e["body"], e["vinyl"], e["works"], e["cb"], *e["col"], e["kind"], remap[e["plane"]])
                for e in self.entries]
        return serialize([n.strip() or self.page1[0].strip() for n in self.page1], self.works, ents, zs,
                         [self.planes[old]["size"] for old in order])

    def save(self, path):
        """write the file; an existing file is kept once as .bak.  -> dict(bak=bool, bytes=int)"""
        blob = self.to_bytes()
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        bak = False
        if os.path.exists(path) and not os.path.exists(path + ".bak"):
            os.replace(path, path + ".bak")
            bak = True
        with open(path, "wb") as f:
            f.write(blob)
        self.version = 2 if blob[:4] == b"VPK2" else 1
        return dict(bak=bak, bytes=len(blob), version=self.version)

    # ---- looking
    def by_number(self):
        d = defaultdict(list)
        for i, e in enumerate(self.entries):
            d[e["vinyl"]].append(i)
        return d

    def image(self, i):
        e = self.entries[i]
        return decode(e["kind"], e["col"], self.planes[e["plane"]]["data"], True)

    def thumb(self, i, side):
        """entry i's picture shrunk to side x side (straight-alpha RGBA, top-down): quick, even for a 2048 picture"""
        e = self.entries[i]
        pl = self.planes[e["plane"]]
        n = pl["size"]
        if e["kind"]:
            al = Image.frombuffer("L", (n, n), pl["data"], "raw", "L", 0, 1).resize((side, side), Image.LANCZOS,
                                                                                     reducing_gap=2.0)
            im = Image.new("RGBA", (side, side), tuple(e["col"]) + (0,))
            im.putalpha(al)
        else:
            im = Image.frombuffer("RGBa", (n, n), pl["data"], "raw", "RGBa", 0, 1)
            im = im.resize((side, side), Image.LANCZOS, reducing_gap=2.0).convert("RGBA")
        return im.transpose(Image.FLIP_TOP_BOTTOM)

    def sticker_image(self, i, original=False, flip=False):
        """straight-alpha RGBA picture of entry i, rows as stored in the file unless flip (for the car preview);
        original=True gives what the file had (None for a sticker added in this session)"""
        e = self.entries[i]
        if original:
            if e.get("orig") is None:
                return None
            kind, col, pl = e["orig"]
        else:
            kind, col, pl = e["kind"], e["col"], e["plane"]
        return decode(kind, col, self.planes[pl]["data"], flip)

    def key(self, i):
        e = self.entries[i]
        return (e["kind"], e["col"], e["plane"])

    def changed(self, i):
        e = self.entries[i]
        return e.get("orig") != self.key(i)

    def head_changed(self):
        return self.orig_head != (list(self.page1), list(self.works))

    def changed_numbers(self):
        return sorted({e["vinyl"] for i, e in enumerate(self.entries) if self.changed(i)})

    # ---- editing
    def set_image(self, idxs, im, fit="stretch", size=SIZE):
        kind, col, data = encode(prepare(im, True, fit, size))
        pl = self.plane_for(kind, data)
        for i in idxs:
            self.entries[i].update(kind=kind, col=col, plane=pl)

    def set_colour(self, idxs, col):
        for i in idxs:
            if self.entries[i]["kind"] == 1:
                self.entries[i]["col"] = tuple(int(c) for c in col)

    def revert(self, idxs):
        """undo changes; stickers added in this session are removed. Returns True if entries were removed."""
        drop = []
        for i in idxs:
            e = self.entries[i]
            if e.get("orig") is None:
                drop.append(i)
            else:
                e["kind"], e["col"], e["plane"] = e["orig"]
        for i in sorted(drop, reverse=True):
            del self.entries[i]
        return bool(drop)

    def add_sticker(self, n, im, fit="stretch", bodies=(0, 1, 2, 3), size=SIZE):
        if any(e["vinyl"] == n for e in self.entries):
            raise VpkError("Sticker %d already exists: select it and use Replace." % n)
        kind, col, data = encode(prepare(im, True, fit, size))
        pl = self.plane_for(kind, data)
        for b in bodies:
            self.entries.append(dict(body=b, vinyl=n, works=0, cb=0, kind=kind, col=col, plane=pl, orig=None))

    def same_as_current(self, i, kind, col, data):
        """whether a freshly prepared picture is the one already there (within rounding)"""
        e = self.entries[i]
        if kind != e["kind"]:
            return False
        a = np.frombuffer(self.planes[e["plane"]]["data"], np.uint8).astype(np.int16)
        b = np.frombuffer(data, np.uint8).astype(np.int16)
        if a.shape != b.shape or int(np.abs(a - b).max()) > 1:
            return False
        return kind == 0 or max(abs(x - y) for x, y in zip(col, e["col"])) <= 2


# ------------------------------------------------------------------------------------------------
# PNG folders (the app's Export / Import)
#   021_Fire-1.png               one picture for every body
#   042_Pac-Man_aeroB.png        a body of its own (normal, aeroA, aeroB, aeroC)
#   043_Works-colours_works2_carbon.png      a paint (works index) and/or carbon-bonnet version
# ------------------------------------------------------------------------------------------------
def export_pngs(pack, out_dir, log=print):
    os.makedirs(out_dir, exist_ok=True)
    groups = defaultdict(list)
    for i, e in enumerate(pack.entries):
        groups[(e["vinyl"], e["works"], e["cb"])].append(i)
    count = 0
    for (n, w, cb), idxs in sorted(groups.items()):
        base = "%02d_%s" % (n, group_of(n).replace(" ", "-")) + ("_works%d" % w if w else "") + ("_carbon" if cb else "")
        if len({pack.key(i) for i in idxs}) == 1:
            pack.image(idxs[0]).save(os.path.join(out_dir, base + ".png"))
            count += 1
        else:
            for i in idxs:
                pack.image(i).save(os.path.join(out_dir, "%s_%s.png" % (base, BODY_TAG[pack.entries[i]["body"]])))
                count += 1
    log("wrote %d PNG files to %s" % (count, out_dir))
    return count


NAME_NUM = re.compile(r"^(\d{1,3})(?:[_\- ].*)?$")


def import_pngs(pack, folder, fit="stretch", log=print, size=SIZE):
    """read the PNGs of a folder back into the pack; a picture that is unchanged is left alone.
    -> (changed numbers, skipped notes)"""
    changed, notes = set(), []
    for fn in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(fn)
        if ext.lower() not in IMAGE_EXT:
            continue
        m = NAME_NUM.match(stem)
        if not m:
            continue
        n = int(m.group(1))
        mb = re.search(r"(?:^|_)(normal|aeroA|aeroB|aeroC)$", stem)
        body = BODY_TAG.index(mb.group(1)) if mb else None
        mw = re.search(r"_works(\d+)", stem)
        w, cb = (int(mw.group(1)) if mw else None), 1 if re.search(r"_carbon(?:_|$)", stem) else 0
        idxs = [i for i, e in enumerate(pack.entries) if e["vinyl"] == n and (body is None or e["body"] == body)
                and e["cb"] == cb and (e["works"] == w if w is not None else e["works"] == 0)]
        if not idxs and w is None and not cb:        # a number that only has works versions
            idxs = [i for i, e in enumerate(pack.entries) if e["vinyl"] == n and (body is None or e["body"] == body)]
        if not idxs:
            notes.append("%s: no such sticker in this car, skipped" % fn)
            continue
        try:
            im = Image.open(os.path.join(folder, fn))
            im.load()
        except (OSError, ValueError) as ex:
            notes.append("%s: cannot read (%s)" % (fn, ex))
            continue
        todo, cache = [], {}
        for i in idxs:                         # unchanged = the same picture at the size it has now
            side = pack.size_of(i)
            if side not in cache:
                cache[side] = encode(prepare(im, True, fit, side))
            kind, col, data = cache[side]
            if not pack.same_as_current(i, kind, col, data):
                todo.append(i)
        if todo:
            pack.set_image(todo, im, fit, size)
            changed.add(n)
    log("%d sticker(s) changed from %s" % (len(changed), folder))
    return sorted(changed), notes


# ------------------------------------------------------------------------------------------------
# older project.json way (scripting)  {"page1": [4 names], "works": [ints], "flip": bool, "fit": "stretch"|"fit",
#                                      "entries": [{"file", "body", "vinyl", "works", "cb"}]}
# ------------------------------------------------------------------------------------------------
def new_project():
    return dict(page1=["", "", "", ""], works=[1], flip=True, fit="stretch", entries=[])


def vpk_names_from_wpk(path):
    """the four page-1 texture names of a car's .vpk, read from its .wpk: the body picture the stickers are blended onto, for the normal body
    and for the aero kits A / B / C (the <car>_aeroA / B / C.wpk next to it; a kit that is not there gets the normal body's name, as File > New
    empty car file does).  The body picture is the one most of the body's triangles are drawn with.  -> (names, notes)"""
    import wpk_edit as WE
    notes = []

    def body_tex(fp):
        pk = WE.Pack.load(fp)
        c = pk.chunk(0)
        use = {}
        for b in (c.batches if c else []):
            if b.tex != WE.NO_TEX and b.tex < len(pk.tex) and not b.flags & 3 and pk.tex[b.tex]["raw"] and not re.match(r"p\d:", pk.tex[b.tex]["name"]):
                use[b.tex] = use.get(b.tex, 0) + len(b.I) // 3
        if not use:
            raise VpkError("%s has no body texture." % os.path.basename(fp))
        i = max(use, key=use.get)
        return os.path.splitext(pk.tex[i]["name"])[0] if pk.tex[i]["name"].lower().endswith((".dds", ".tga", ".png")) else pk.tex[i]["name"], pk
    name, pk = body_tex(path)
    if pk.tex_index("p1:" + name) is None:
        notes.append("note: the car has no p1:%s paint copy (a stock car keeps one per paint); the stickers still blend onto %s." % (name, name))
    names = [name]
    stem = os.path.splitext(path)[0]
    for k in "ABC":
        kit = "%s_aero%s.wpk" % (stem, k)
        if os.path.exists(kit):
            nm, _ = body_tex(kit)
            names.append(nm)
            notes.append("aero %s: %s (from %s)" % (k, nm, os.path.basename(kit)))
        else:
            names.append(name)
    return names, notes


def load_project(path):
    with open(path) as fh:
        prj = json.load(fh)
    base = dict(new_project())
    base.update(prj)
    return base


def pack_project(prj, out_path, base_dir="."):
    p = Pack(prj.get("page1"), prj.get("works", [1]))
    p.page1 = (list(p.page1) + [""] * 4)[:4]
    flip, fit = bool(prj.get("flip", True)), prj.get("fit", "stretch")
    if not p.page1[0].strip():
        raise VpkError("Give the page-1 texture name for the normal body.")
    if not prj.get("entries"):
        raise VpkError("Add at least one image.")
    for e in prj["entries"]:
        path = e["file"] if os.path.isabs(e["file"]) else os.path.join(base_dir, e["file"])
        try:
            im = Image.open(path)
            im.load()
        except (OSError, ValueError) as ex:
            raise VpkError("Cannot read %s: %s" % (path, ex))
        kind, col, data = encode(prepare(im, flip, fit, int(prj.get("size", SIZE))))
        p.entries.append(dict(body=int(e["body"]), vinyl=int(e["vinyl"]), works=int(e["works"]), cb=int(e["cb"]),
                              kind=kind, col=col, plane=p.plane_for(kind, data), orig=None))
        if not 0 <= p.entries[-1]["body"] <= 3 or not 1 <= p.entries[-1]["vinyl"] <= 255:
            raise VpkError("%s: body must be 0..3 and sticker number 1..255" % os.path.basename(path))
    return p.save(out_path), len(p.entries)


def unpack_project(path, out_dir, log=print):
    p = Pack.load(path)
    os.makedirs(out_dir, exist_ok=True)
    ents = []
    for i, e in enumerate(p.entries):
        name = "b%d_v%d_w%d_cb%d.png" % (e["body"], e["vinyl"], e["works"], e["cb"])
        p.image(i).save(os.path.join(out_dir, name))
        ents.append(dict(file=name, body=e["body"], vinyl=e["vinyl"], works=e["works"], cb=e["cb"]))
    with open(os.path.join(out_dir, "project.json"), "w") as f:
        json.dump(dict(page1=p.page1, works=p.works, flip=True, fit="stretch", entries=ents), f, indent=1)
    log("wrote %d PNGs and project.json to %s" % (len(ents), out_dir))


# ------------------------------------------------------------------------------------------------
# command line
# ------------------------------------------------------------------------------------------------
def uv_model(path):
    if not wpk_view:
        raise VpkError("wpk_view.py is missing next to vpk_tool.py.")
    sp = wpk_view.stock_of(path)
    return wpk_view.read_wpk3(path, wpk_view.read_wpk3(sp)[0] if sp else None)


def texture_rows(path):
    """the textures of a car pack with what they are used for -> [dict(index, name, w, h, fmt_name, size, borrowed,
    editable, tris, blended)]"""
    if not wpk_tex:
        raise VpkError("wpk_tex.py is missing next to vpk_tool.py.")
    with open(path, "rb") as fh:
        rows = wpk_tex.list_textures(fh.read(), os.path.basename(path))
    use = {}
    if wpk_view:
        try:
            use = wpk_view.texture_usage(wpk_view.read_wpk3(path))
        except (wpk_view.WpkError, OSError, ValueError, KeyError, struct.error):
            use = {}
    for r in rows:
        u = use.get(r["index"], {})
        r["tris"], r["blended"] = u.get("tris", 0), u.get("blended", False)
    return rows


def cli(argv):
    argv = list(argv)
    raw = list(argv)                               # plc / wpk have flags of their own: they get the arguments untouched
    plain = "--plain" in argv                      # uv / uvall: colours only, not the car's own lamp / plate pictures
    dither = "--dither" in argv                    # teximport: ordered dither for 4 / 5 bit gradients
    whole_car = "--car" in argv                    # teximport: the car's aero kits too, OUT is a folder
    argv = [a for a in argv if a not in ("--plain", "--dither", "--car")]
    size = None                                    # --size N: replace = the new picture's side (default 512);
                                                   # teximport = the texture's longest side (default: as it is)
    if "--size" in argv:
        k = argv.index("--size")
        try:
            size = int(argv[k + 1])
        except (IndexError, ValueError):
            print("error: --size needs a number (512, 1024 or 2048)")
            return 1
        del argv[k:k + 2]
    try:
        if len(argv) == 2 and argv[0] == "info":
            print(info_text(argv[1]))
        elif len(argv) == 3 and argv[0] == "unpack":
            unpack_project(argv[1], argv[2])
        elif len(argv) in (3, 4) and argv[0] == "newvpk":
            # newvpk CAR.wpk OUT.vpk [STICKER_FOLDER]: a starter .vpk for a car that has none (an add-on / imported car)
            names, notes = vpk_names_from_wpk(argv[1])
            p = Pack(names)
            for n_ in notes:
                print(n_)
            print("page-1 textures: " + ", ".join("%s=%s" % (BODIES[i], names[i]) for i in range(4)))
            if len(argv) == 4:
                got = 0
                for fn in sorted(os.listdir(argv[3])):
                    stem, ext = os.path.splitext(fn)
                    m_ = NAME_NUM.match(stem)
                    if ext.lower() not in IMAGE_EXT or not m_:
                        continue
                    n_ = int(m_.group(1))
                    if not 1 <= n_ <= 255:
                        print("skipped %s: the sticker number must be 1..255" % fn)
                        continue
                    im = Image.open(os.path.join(argv[3], fn))
                    im.load()
                    p.add_sticker(n_, im, "stretch", size=size or SIZE)
                    got += 1
                print("%d sticker(s) added from %s" % (got, argv[3]))
            if not p.entries:
                print("note: no stickers yet - open the file in the app and use Edit > Add a sticker number...")
            st = p.save(argv[2])
            print("wrote %s (VPK%d, %d stickers%s)" % (argv[2], st["version"], len({e["vinyl"] for e in p.entries}), ", old file kept as .bak" if st["bak"] else ""))
        elif len(argv) == 3 and argv[0] == "pack":
            st, n = pack_project(load_project(argv[1]), argv[2], os.path.dirname(os.path.abspath(argv[1])))
            print("wrote %s: %d stickers, %.2f MB" % (argv[2], n, st["bytes"] / 1e6))
        elif len(argv) in (4, 5) and argv[0] == "replace":
            p = Pack.load(argv[1])
            n = int(argv[2])
            idxs = [i for i, e in enumerate(p.entries) if e["vinyl"] == n]
            if not idxs:
                raise VpkError("sticker %d is not in %s" % (n, argv[1]))
            im = Image.open(argv[3])
            im.load()
            p.set_image(idxs, im, "stretch", size or DEFAULT_SIZE)
            out = argv[4] if len(argv) == 5 else argv[1]
            st = p.save(out)
            print("sticker %d replaced on %d entries (%d px) -> %s, VPK%d%s%s" % (
                n, len(idxs), p.size_of(idxs[0]), out, st["version"], " (needs the game build that reads VPK2)"
                if st["version"] == 2 else "", " (old file kept as .bak)" if st["bak"] else ""))
        elif argv[0] == "uv" and len(argv) in (3, 4, 5):       # uv CAR.wpk OUT.png [SIZE] [TEXTURE]
            m = uv_model(argv[1])
            stem = argv[4] if len(argv) == 5 else wpk_view.body_stem(m)
            im, n = wpk_view.uv_template(m, stem, int(argv[3]) if len(argv) >= 4 else 2048, wpk_view.default_chunks(m),
                                         real=not plain)
            im.save(argv[2])
            print("wrote %s: %d islands of '%s'" % (argv[2], n, stem))
        elif argv[0] == "uvall" and len(argv) in (3, 4):       # uvall CARS_DIR OUT_DIR [SIZE]
            if not wpk_view:
                raise VpkError("wpk_view.py is missing next to vpk_tool.py.")
            done, failed = wpk_view.export_all_uv(
                argv[1], argv[2], int(argv[3]) if len(argv) == 4 else 2048, real=not plain,
                progress=lambda n, tot, name: print("\r%d/%d %s" % (n, tot, name), end="", flush=True))
            print("\nwrote %d templates to %s%s" % (done, argv[2], ("; %d not made (see index.txt)" % len(failed)) if failed else ""))
        elif argv[0] == "textures" and len(argv) == 2:         # textures CAR.wpk
            print("%3s  %-30s %-9s %-9s %8s  %s" % ("#", "texture", "size", "format", "KB", "used by"))
            for r in texture_rows(argv[1]):
                use = ("%d triangles%s" % (r["tris"], ", see-through (glass / lamp)" if r["blended"] else "")
                       if r["tris"] else "-")
                note = ("  [borrowed from the stock pack]" if r["borrowed"] else
                        "" if r["editable"] else "  [compressed: cannot be edited]")
                print("%3d  %-30s %-9s %-9s %8.1f  %s%s" % (r["index"], r["name"], "%dx%d" % (r["w"], r["h"]),
                                                          r["fmt_name"], r["size"] / 1024, use, note))
        elif argv[0] in ("texexport", "texguide", "texprobe") and len(argv) in (4, 5):   # CAR.wpk NAME OUT.png [SIZE]
            if not (wpk_tex and wpk_view):
                raise VpkError("wpk_tex.py / wpk_view.py are missing next to vpk_tool.py.")
            with open(argv[1], "rb") as fh:
                blob = fh.read()
            idx = wpk_tex.find_texture(wpk_tex.list_textures(blob), argv[2])
            if argv[0] == "texexport":
                im = wpk_tex.export_image(blob, idx, os.path.basename(argv[1]), int(argv[4]) if len(argv) == 5 else None)
                im.save(argv[3])
                print("wrote %s (%d x %d)" % (argv[3], im.width, im.height))
            else:
                m = uv_model(argv[1])
                if argv[0] == "texguide":
                    im, drawn = wpk_view.texture_guide(m, idx, int(argv[4]) if len(argv) == 5 else None)
                    im.save(argv[3])
                    print("wrote %s%s" % (argv[3], "" if drawn else " (no triangle of this car uses it: no UV borders)"))
                else:
                    wpk_view.texture_probe(m, idx).save(argv[3])
                    print("wrote %s: bottom row, pink = everywhere this texture shows; top row, a position pattern "
                          "(red across, green up, blue checks): a pane it varies over can carry a decal, one flat colour "
                          "cannot." % argv[3])
        elif argv[0] == "teximport" and len(argv) == 5:        # teximport CAR.wpk NAME IMAGE OUT
            if not wpk_tex:
                raise VpkError("wpk_tex.py is missing next to vpk_tool.py.")
            im = Image.open(argv[3])
            im.load()
            if whole_car:
                r = wpk_tex.import_to_car(argv[1], argv[2], im, argv[4], size, dither)
                for n, what in r["files"]:
                    print("  %-28s %s" % (n, what))
                print("wrote the car to %s%s" % (argv[4], "; the old files are kept as .bak" if r["bak"] else ""))
            else:
                with open(argv[1], "rb") as fh:
                    blob = fh.read()
                idx = wpk_tex.find_texture(wpk_tex.list_textures(blob), argv[2])
                new, r = wpk_tex.import_image(blob, idx, im, size, dither, os.path.basename(argv[1]))
                bak = wpk_tex.write_with_backup(argv[4], new)
                print("wrote %s (%d -> %d bytes)%s" % (argv[4], len(blob), len(new), "; the old file is kept as .bak" if bak else ""))
                for n in r:
                    print("  note:", n)
            if whole_car and r["notes"]:
                for n in r["notes"]:
                    print("  note:", n)
        elif argv[0] == "plc":
            import plc_import
            return plc_import.cli(raw[1:])
        elif argv[0] == "wheels":
            if not wheels_tab:
                raise VpkError("wheels_tab.py is missing next to vpk_tool.py.")
            return wheels_tab.cli(raw[1:])
        elif argv[0] == "engine":
            if not engine_tab:
                raise VpkError("engine_tab.py is missing next to vpk_tool.py.")
            try:
                return engine_tab.cli(raw[1:])
            except engine_tab.SoundError as ex:
                raise VpkError(str(ex))
        elif argv[0] == "meters":
            if not meters_edit:
                raise VpkError("meters_edit.py is missing next to vpk_tool.py.")
            return meters_edit.cli(raw[1:])
        elif argv[0] == "lamps":
            if not lamp_edit:
                raise VpkError("lamp_edit.py is missing next to vpk_tool.py.")
            return lamp_edit.cli(raw[1:])
        elif argv[0] == "backfire":
            if not backfire_edit:
                raise VpkError("backfire_edit.py is missing next to vpk_tool.py.")
            return backfire_edit.cli(raw[1:])
        elif argv[0] == "wpk":
            if not wpk_edit:
                raise VpkError("wpk_edit.py is missing next to vpk_tool.py.")
            return wpk_edit.cli(raw[1:])
        elif argv[0] == "uvcheck" and len(argv) in (4, 5):     # uvcheck CAR.wpk STICKER.png OUT.png [TEXTURE]
            m = uv_model(argv[1])
            stem = argv[4] if len(argv) == 5 else wpk_view.body_stem(m)
            tpl, _n = wpk_view.uv_template(m, stem, 1024, wpk_view.default_chunks(m), labels=False, real=False)
            pic, inside, outside = wpk_view.uv_check(tpl, Image.open(argv[2]))
            pic.save(argv[3])
            print("wrote %s: the picture covers %.0f%% of the car's UV islands. Red lines = the real panel borders; "
                  "art that does not sit inside them is misplaced." % (argv[3], 100 * inside))
        else:
            print(__doc__)
            return 2
    except (wpk_view.WpkError if wpk_view else VpkError, wpk_tex.TexError if wpk_tex else VpkError,
            VpkError, OSError, ValueError, KeyError) as ex:
        print("error:", ex)
        return 1
    return 0


# ------------------------------------------------------------------------------------------------
# the app
# ------------------------------------------------------------------------------------------------
HELP = """How it works

1.  Open a car's .vpk from the game's vinyl folder (for example maz_FD3S.vpk).
     The file already knows its car texture and has every sticker, so there is nothing to type.
2.  Click the sticker you want to change. The grid is the in-game sticker menu:
     each row of ten is one design in its ten colours (Line 1, Fire 1, Tribal ...).
3.  Click Replace image and pick a PNG or TGA with transparency.
     It is made 512 x 512 (or 1024 / 2048, see below) and put on every body of the car for you.
4.  Click Save to game. The old file is kept once as <car>.vpk.bak.

Good to know
- Orange dot = a sticker you changed. "Undo changes" puts that sticker back.
- A sticker is drawn for ONE car's body, so use the art made for that car (its sticker TGAs).
- A picture of one colour (lines, tribals) is stored as colour + shape, so "Change colour" is instant.
- Export all PNGs / Import PNGs lets you edit many stickers in an image editor and bring them back;
  pictures you did not touch are skipped.
- Edit > Car settings shows the texture name and works table taken from the file; you rarely need it.

Decals on the glass, lamps and plate (File > Car textures)
- The window glass, the lamps and the plate are not on the page the stickers go on: each is its own texture inside the
  car's .wpk (the glass is <car>_body02_cola01). File > Car textures lists them: pick one, Export PNG, paint on it in an
  image editor (keep it upright, keep its size), Import edited PNG. It is on the car for everybody, not a selectable sticker.
- The glass panes use only a small part of that picture (a windscreen is about 9 x 20 texels at the original 256 x 256),
  so export at 1024 or 2048: the texture is made that size when you import it (the file grows about 2 / 8 MB).
  "UV guide PNG" draws the UV borders over the texture so you can see where each pane is; "Where does it show?" draws
  the car with every spot that uses the texture in pink (bottom row) and a position pattern in its place (top row): a
  pane of one flat colour cannot carry a decal.
- Every other byte of the .wpk stays as it was (checked before anything is written). Aero kits borrow the stock car's
  textures, so one edit covers them; a kit with its own copy gets the same picture.

Sharper stickers (higher resolution)
- Edit > Size of new stickers: 512 x 512 (the default, the arcade's own size) or 1024 / 2048 for stickers that keep more detail in the game. The game grows the car's texture to the sticker's size by itself, so the car's .wpk
  needs no change. A file that holds a bigger picture is saved as a VPK2, which only the 10.7+ game build reads
  (an all-512 file stays a VPK1 that every build reads). A bigger sticker costs a little more time and memory the
  first time the car is drawn with it.
"""


def run_gui(_hook=None, start=None):
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

    from PIL import ImageTk

    ACCENT, ORANGE, GREY = "#1f6feb", "#e8710a", "#777777"
    TMIN, TMAX, PAD = 66, 100, 10

    def checker(size, cell=8):
        y, x = np.mgrid[0:size, 0:size]
        a = np.where(((x // cell) + (y // cell)) % 2, 150, 190).astype(np.uint8)
        return Image.fromarray(np.stack([a, a, a], -1), "RGB")

    class App:
        def __init__(self, root):
            self.root = root
            root.title(APP + "  " + VERSION)
            root.minsize(980, 620)
            root.geometry("%dx%d" % (min(1360, root.winfo_screenwidth() - 40), min(860, root.winfo_screenheight() - 90)))
            try:                                           # the size, place and maximised state it had last time
                saved = (wpk_view.load_prefs() if wpk_view else {}).get("main", {})
                geo = vpk_ui.fit_geometry(saved, root.winfo_screenwidth(), root.winfo_screenheight())
                if geo:
                    root.geometry(geo)
                if isinstance(saved, dict) and saved.get("maximized"):
                    self.maximize_later = True
            except (TypeError, ValueError, tk.TclError):
                pass
            vpk_ui.apply_theme(root, ttk, tk)               # the dark workshop theme (one place, every tab and dialog)
            vpk_ui.install_dialog_minsize(root, tk)
            self.pack = None
            self.by_num = {}
            self.sel = None            # sticker number
            self.ver = (0, 0)          # (works, cb) version being looked at
            self.body = 0
            self.tiles = {}
            self.imgs = []
            self.thumbs = {}
            self.photo = None
            self.cols = 1
            self.grid_w = 0
            self._redraw_job = None
            self.fit = tk.StringVar(value="stretch")
            self.size_var = tk.StringVar(value=str(DEFAULT_SIZE))   # the side new pictures are made at
            self.v_ver = tk.StringVar()
            self.v_body = tk.StringVar()
            self.v_all = tk.BooleanVar(value=False)
            self.v_uv = tk.BooleanVar(value=False)       # draw the car's UV panel borders over the sticker
            self.v_uvop = tk.DoubleVar(value=0.9)         # ... how strongly
            self.v_uvw = tk.StringVar(value="Normal")     # ... and how thick: Thin / Normal / Thick
            self.uv_models, self.bigwin, self.fitwin = {}, None, None
            self.status = vpk_ui.StatusLine()
            self.carvar = tk.StringVar()
            self.bold = tkfont.nametofont("TkDefaultFont").copy()
            self.bold.configure(weight="bold")
            self.big = tkfont.nametofont("TkDefaultFont").copy()
            self.big.configure(size=13, weight="bold")
            self.bg_prev = checker(256)
            self.tile = 76
            self.bg_thumbs = {}
            self.preview = None        # wpk_view.PreviewWindow while open
            self.cars_dir = None       # folder the car's .wpk files were found in
            self.nb, self.models = None, None      # page tracker and the "Models (WPK)" workspace
            self.pages, self.nav_buttons, self.current_page = [], [], 0
            self.page_names = []
            self.page_objs = []
            self.maximize_later = getattr(self, "maximize_later", False)
            self.build_ui()
            self.refresh_all()
            root.protocol("WM_DELETE_WINDOW", self.quit)
            self.install_keys()
            if self.maximize_later:
                vpk_ui.set_maximised(root, True, tk.TclError)
            for key, d in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, -1)), ("Down", (0, 1))):
                root.bind("<%s>" % key, lambda _e, d=d: self.move(*d) if self.on_stickers() else None)
            root.bind("<Return>", lambda _e: self.replace_image() if self.on_stickers() and self.sel is not None else None)

        # ------------------------------------------------------------ layout
        def build_ui(self):
            r = self.root
            menu_style = dict(bg="#111925", fg="#e6edf6", activebackground="#24577b",
                              activeforeground="#ffffff", bd=0, relief="flat")
            m = tk.Menu(r, tearoff=0, **menu_style)
            fm = tk.Menu(m, tearoff=0, **menu_style)
            fm.add_command(label="Open .vpk…    Ctrl+O", command=self.open_dialog)
            fm.add_command(label="Save to game    Ctrl+S", command=self.save)
            fm.add_command(label="Save as…", command=self.save_as)
            fm.add_separator()
            fm.add_command(label="Export all PNGs…", command=self.export_all)
            fm.add_command(label="Import PNGs from a folder…", command=self.import_folder)
            fm.add_separator()
            fm.add_command(label="New empty car file…", command=self.new_empty)
            fm.add_command(label="New car file from a car .wpk…", command=self.new_from_wpk)
            fm.add_separator()
            fm.add_command(label="Car textures: glass, lamps, plate…", command=self.textures_window)
            fm.add_separator()
            fm.add_command(label="Quit", command=self.quit)
            m.add_cascade(label="File", menu=fm)
            em = tk.Menu(m, tearoff=0, **menu_style)
            em.add_command(label="Add a sticker number…", command=self.add_sticker)
            em.add_command(label="Car settings…", command=self.car_settings)
            em.add_command(label="Undo all changes", command=self.undo_all)
            em.add_separator()
            om = tk.Menu(em, tearoff=0, **menu_style)
            om.add_radiobutton(label="Stretch to a square", variable=self.fit, value="stretch")
            om.add_radiobutton(label="Pad with transparency", variable=self.fit, value="fit")
            em.add_cascade(label="Pictures that are not square", menu=om)
            zm = tk.Menu(em, tearoff=0, **menu_style)
            for sz in SIZES:
                zm.add_radiobutton(label="%d × %d%s" % (sz, sz, "   (the arcade's own, an older build reads it)" if sz == SIZE else ""),
                                   variable=self.size_var, value=str(sz), command=self.size_changed)
            em.add_cascade(label="Size of new stickers", menu=zm)
            m.add_cascade(label="Edit", menu=em)
            vm = tk.Menu(m, tearoff=0, **menu_style)
            vm.add_command(label="Show on the car…", command=self.open_preview)
            vm.add_command(label="Fit a picture to the UV…", command=self.open_fit)
            vm.add_separator()
            vm.add_command(label="Export UV template…", command=self.save_uv_template)
            vm.add_command(label="Export UV template from a car .wpk…", command=self.export_uv_from_wpk)
            vm.add_command(label="Export car textures…", command=self.export_car_textures)
            vm.add_command(label="Export UV of ALL cars…", command=self.export_all_uv)
            vm.add_command(label="Export textures of ALL cars…", command=self.export_all_textures)
            m.add_cascade(label="View", menu=vm)
            if wpk_edit:
                mm = tk.Menu(m, tearoff=0, **menu_style)
                for sid, text, meth in (("open", "Open a car .wpk…", "open_dialog"), ("save", "Save to game", "save"),
                                        ("save_as", "Save as…", "save_as"), (None, None, None),
                                        ("undo", "Undo", "do_undo"), ("delete", "Delete selected part(s)", "delete"), (None, None, None),
                                        ("search", "Search the asset browser", "@search"), ("fit", "Fit / reset camera", "fit_view"),
                                        ("textures", "Textures and materials…", "textures"), ("export_obj", "Export selected parts as OBJ…", "export"),
                                        ("toggle_log", "Show / hide operation log", "_toggle_log")):
                    if sid is None:
                        mm.add_separator()
                    else:
                        mm.add_command(command=lambda m_=meth: self.models_cmd(m_), **vpk_ui.menu_label(sid, text))
                m.add_cascade(label="Models", menu=mm)
                self.go_menu = tk.Menu(m, tearoff=0, **menu_style)      # filled once the pages exist
                m.add_cascade(label="Go", menu=self.go_menu)
            hm = tk.Menu(m, tearoff=0, **menu_style)
            hm.add_command(command=self.show_shortcuts, **vpk_ui.menu_label("shortcuts", "Keyboard shortcuts"))
            hm.add_command(label="How it works", command=self.show_help)
            m.add_cascade(label="Help", menu=hm)
            r.configure(menu=m)

            if wpk_edit:
                # Navigation rail + stacked pages. Each page still uses its original editor.
                header = ttk.Frame(r, padding=(20, 13))
                header.pack(fill="x")
                tk.Label(header, text="V", bg="#205b89", fg="#ffffff",
                         font=("TkDefaultFont", 15, "bold"), width=2, pady=4).pack(side="left")
                brand = ttk.Frame(header)
                brand.pack(side="left", padx=(10, 0))
                tk.Label(brand, text="VPK TOOL", bg="#0c1017", fg="#e6edf6",
                         font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
                tk.Label(brand, text="RE3DX+ MODDING WORKSPACE", bg="#0c1017", fg="#8293a8",
                         font=("TkDefaultFont", 8)).pack(anchor="w", pady=(2, 0))
                tk.Label(header, text="WORKSPACE  /  LOCAL PROJECT", bg="#0c1017", fg="#8293a8",
                         font=("TkDefaultFont", 8, "bold")).pack(side="right", padx=8)
                ttk.Separator(r).pack(fill="x")

                shell = ttk.Frame(r)
                shell.pack(fill="both", expand=True)
                shell.columnconfigure(1, weight=1)
                shell.rowconfigure(0, weight=1)
                rail = tk.Frame(shell, bg="#0f151f", width=194, highlightthickness=0)
                rail.grid(row=0, column=0, sticky="ns")
                rail.grid_propagate(False)
                tk.Label(rail, text="WORKSPACE", bg="#0f151f", fg="#71839a",
                         font=("TkDefaultFont", 8, "bold")).pack(anchor="w", padx=17, pady=(20, 9))
                host = ttk.Frame(shell, padding=0)
                host.grid(row=0, column=1, sticky="nsew")
                host.rowconfigure(0, weight=1)
                host.columnconfigure(0, weight=1)

                class _PageTracker:
                    def __init__(tracker, app): tracker.app = app
                    def index(tracker, which): return tracker.app.current_page
                self.nb = _PageTracker(self)

                def add_page(label, icon, group=None):
                    if group:
                        tk.Label(rail, text=group.upper(), bg="#0f151f", fg="#71839a",
                                 font=("TkDefaultFont", 8, "bold")).pack(anchor="w", padx=17, pady=(15, 6))
                    frame = ttk.Frame(host)
                    frame.grid(row=0, column=0, sticky="nsew")
                    idx = len(self.pages)
                    btn = ttk.Button(rail, text="  %s   %s" % (icon, label), style="VPK.Nav.TButton",
                                     command=lambda i=idx: self.show_page(i))
                    btn.pack(fill="x", padx=9, pady=2)
                    self.pages.append(frame)
                    self.nav_buttons.append(btn)
                    self.page_names.append(label)
                    return frame

                tab1 = add_page("Livery & Stickers", "◈", "Design")
                tab2 = add_page("Vehicle Models", "▰", "Vehicle")
                if backfire_edit:
                    tab3 = add_page("Backfire", "≋", "Audio")
                if wheels_tab:
                    tab4 = add_page("Wheels", "◉", "Components")
                if lamp_edit:
                    tab6 = add_page("Lamps", "✦")
                if meters_edit:
                    tab7 = add_page("Meters", "▥")
                if engine_tab:
                    tab5 = add_page("Engine Sound", "♫", "Audio")
                tk.Label(rail, text="VPK TOOL  ·  %s" % VERSION, bg="#0f151f", fg="#53647a",
                         font=("TkDefaultFont", 7)).pack(side="bottom", anchor="w", padx=16, pady=15)

                _scroll = backfire_edit.scrollable if backfire_edit else (lambda f: f)
                self.models = wpk_edit.build_tab(_scroll(tab2), r, self)
                if backfire_edit:
                    self.backfire = backfire_edit.build_tab(tab3, r, self)
                if wheels_tab:
                    self.wheels = wheels_tab.build_tab(tab4, r, self)
                if lamp_edit:
                    self.lamps = lamp_edit.build_tab(tab6, r, self)
                if meters_edit:
                    self.meters = meters_edit.build_tab(tab7, r, self)
                if engine_tab:
                    self.engine = engine_tab.build_tab(tab5, r, self)
                self.page_objs = [self, self.models] + [getattr(self, n) for n, mod in (
                    ("backfire", backfire_edit), ("wheels", wheels_tab), ("lamps", lamp_edit), ("meters", meters_edit),
                    ("engine", engine_tab)) if mod]
                for i, name in enumerate(self.page_names[:9]):
                    self.go_menu.add_command(label=name, accelerator="Ctrl+%d" % (i + 1), command=lambda i=i: self.show_page(i))
                r = tab1                                   # everything below is the sticker page

            top = ttk.Frame(r, padding=(12, 10, 12, 4))
            top.pack(fill="x")
            ttk.Button(top, text="Open car .vpk…", command=self.open_dialog).pack(side="left")
            ttk.Label(top, text="  Car:").pack(side="left")
            self.car_combo = ttk.Combobox(top, textvariable=self.carvar, state="disabled", width=22)
            self.car_combo.pack(side="left", padx=(4, 0))
            self.car_combo.bind("<<ComboboxSelected>>", lambda _e: self.switch_car())
            self.steps = []
            sf = ttk.Frame(top)
            sf.pack(side="right")
            for i, t in enumerate(("1  Open a car file", "2  Click a sticker", "3  Replace image", "4  Save to game")):
                if i:
                    ttk.Label(sf, text="›", foreground=GREY).pack(side="left", padx=4)
                lab = ttk.Label(sf, text=t)
                lab.pack(side="left")
                self.steps.append(lab)

            ttk.Separator(r).pack(fill="x", pady=(6, 0))
            body = ttk.Frame(r)
            body.pack(fill="both", expand=True)
            body.columnconfigure(0, weight=1)
            body.rowconfigure(0, weight=1)

            gf = ttk.Frame(body)
            gf.grid(row=0, column=0, sticky="nsew")
            self.canvas = tk.Canvas(gf, highlightthickness=0, background="#f4f4f4")
            sb = ttk.Scrollbar(gf, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)
            self.canvas.bind("<Configure>", self.on_resize)
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.canvas.bind(seq, self.wheel)

            ttk.Separator(body, orient="vertical").grid(row=0, column=1, sticky="ns")
            pcol = ttk.Frame(body, width=360)                      # right side: scrolls when the window is short
            pcol.grid(row=0, column=2, sticky="ns")
            pcol.pack_propagate(False)
            self.pcv = tk.Canvas(pcol, highlightthickness=0, width=345)
            psb = ttk.Scrollbar(pcol, orient="vertical", command=self.pcv.yview)
            self.pcv.configure(yscrollcommand=psb.set)
            psb.pack(side="right", fill="y")
            self.pcv.pack(side="left", fill="both", expand=True)
            self.panel = ttk.Frame(self.pcv, padding=12)
            pwin = self.pcv.create_window(0, 0, anchor="nw", window=self.panel)
            self.panel.bind("<Configure>", lambda _e: self.pcv.configure(scrollregion=self.pcv.bbox("all")))
            self.pcv.bind("<Configure>", lambda e: self.pcv.itemconfigure(pwin, width=e.width))

            def panel_wheel(e):
                w = self.root.winfo_containing(e.x_root, e.y_root)
                if w is None or not str(w).startswith(str(pcol)):
                    return
                if self.pcv.bbox("all") and self.pcv.bbox("all")[3] > self.pcv.winfo_height():
                    self.pcv.yview_scroll(-3 if (e.num == 4 or getattr(e, "delta", 0) > 0) else 3, "units")
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.root.bind_all(seq, panel_wheel, add="+")
            self.build_panel()

            ttk.Separator(r).pack(fill="x")
            bar = ttk.Frame(r, padding=(12, 8))
            bar.pack(fill="x")
            self.status.bar(bar, ttk, tk, side="left", fill="x", expand=True)
            self.btn_save = ttk.Button(bar, text="Save to game", command=self.save, state="disabled")
            self.btn_save.pack(side="right")
            self.btn_saveas = ttk.Button(bar, text="Save as…", command=self.save_as, state="disabled")
            self.btn_saveas.pack(side="right", padx=(0, 8))

        def build_panel(self):
            p = self.panel
            p.columnconfigure(0, weight=1)
            self.title = ttk.Label(p, text="No sticker selected", font=self.big)
            self.title.grid(row=0, column=0, sticky="w")
            self.sub = ttk.Label(p, text="", foreground=GREY)
            self.sub.grid(row=1, column=0, sticky="w", pady=(0, 8))
            pf = ttk.Frame(p)
            pf.grid(row=2, column=0)
            self.prev = tk.Canvas(pf, width=256, height=256, highlightthickness=1, highlightbackground="#999")
            self.prev.pack()
            self.prev.bind("<Double-Button-1>", lambda _e: self.open_big())
            uvrow = ttk.Frame(pf)
            uvrow.pack(fill="x", pady=(4, 0))
            self.chk_uv = ttk.Checkbutton(uvrow, text="Show UV layout", variable=self.v_uv, command=self.toggle_uv,
                                          state="normal" if wpk_view else "disabled")
            self.chk_uv.pack(side="left")
            ttk.Button(uvrow, text="Big view…", command=self.open_big).pack(side="right")
            uvrow2 = ttk.Frame(pf)
            uvrow2.pack(fill="x", pady=(2, 0))
            ttk.Label(uvrow2, text="Strength").pack(side="left")
            so = ttk.Scale(uvrow2, from_=0.1, to=1.0, variable=self.v_uvop, length=90)
            so.pack(side="left", padx=(4, 8))
            so.bind("<ButtonRelease-1>", lambda _e: self.show_detail())
            ttk.Label(uvrow2, text="Lines").pack(side="left")
            cw = ttk.Combobox(uvrow2, textvariable=self.v_uvw, state="readonly", width=7, values=["Thin", "Normal", "Thick"])
            cw.pack(side="left", padx=(4, 0))
            cw.bind("<<ComboboxSelected>>", lambda _e: self.show_detail())
            self.info = ttk.Label(p, text="", foreground=GREY, wraplength=310, justify="left")
            self.info.grid(row=3, column=0, sticky="w", pady=(6, 6))
            self.opts = ttk.Frame(p)
            self.opts.grid(row=4, column=0, sticky="ew")
            self.opts.columnconfigure(1, weight=1)
            self.lab_ver = ttk.Label(self.opts, text="Version")
            self.lab_ver.grid(row=0, column=0, sticky="w")
            self.cb_ver = ttk.Combobox(self.opts, textvariable=self.v_ver, state="readonly", width=24)
            self.cb_ver.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=2)
            self.cb_ver.bind("<<ComboboxSelected>>", lambda _e: self.pick_version())
            self.lab_body = ttk.Label(self.opts, text="Body")
            self.lab_body.grid(row=1, column=0, sticky="w")
            self.cb_body = ttk.Combobox(self.opts, textvariable=self.v_body, state="readonly", width=24)
            self.cb_body.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=2)
            self.cb_body.bind("<<ComboboxSelected>>", lambda _e: self.pick_body())
            self.chk_all = ttk.Checkbutton(self.opts, text="Change all bodies together", variable=self.v_all)
            self.chk_all.grid(row=2, column=0, columnspan=2, sticky="w")
            ok_car = "normal" if wpk_view else "disabled"

            def group(row, title):
                f = ttk.LabelFrame(p, text=title, padding=(8, 2, 8, 8))
                f.grid(row=row, column=0, sticky="ew", pady=(10, 0))
                f.columnconfigure(0, weight=1)
                f.columnconfigure(1, weight=1)
                return f

            def button(f, row, text, cmd, state, col=0, span=2):
                b = ttk.Button(f, text=text, command=cmd, state=state)
                b.grid(row=row, column=col, columnspan=span, sticky="ew", pady=(0 if row == 0 else 4, 0),
                       padx=(0 if col == 0 else 2, 0 if span == 2 or col == 1 else 2), ipady=2)
                return b

            g1 = group(5, "Sticker")
            self.btn_rep = button(g1, 0, "Replace image…", self.replace_image, "disabled")
            self.btn_col = button(g1, 1, "Colour…", self.change_colour, "disabled", col=0, span=1)
            self.btn_undo = button(g1, 1, "Undo", self.undo, "disabled", col=1, span=1)
            g2 = group(6, "On the car")
            self.btn_car = button(g2, 0, "Show on the car…", self.open_preview, ok_car)
            self.btn_fit = button(g2, 1, "Fit a picture to the UV…", self.open_fit,
                                  "normal" if (wpk_view and uv_fit) else "disabled")
            g3 = group(7, "Export")
            self.btn_exp = button(g3, 0, "Export PNG", self.export_one, "disabled")
            self.btn_tex = button(g3, 1, "Export car textures…", self.export_car_textures, ok_car)
            self.btn_uv = button(g3, 2, "Export UV template…", self.save_uv_template, ok_car)
            g4 = group(8, "All cars")
            self.btn_uvall = button(g4, 0, "Export UV of ALL cars…", self.export_all_uv, ok_car)
            self.btn_texall = button(g4, 1, "Export textures of ALL cars…", self.export_all_textures, ok_car)
            self.hint = ttk.Label(p, text="", foreground=GREY, wraplength=310, justify="left")
            self.hint.grid(row=9, column=0, sticky="w", pady=(10, 0))

        # ------------------------------------------------------------ opening / saving
        def ask_discard(self):
            if self.pack and (self.pack.changed_numbers() or self.pack.head_changed()):
                a = messagebox.askyesnocancel(APP, "Save your changes to %s first?" % (
                    os.path.basename(self.pack.path) if self.pack.path else "this car"))
                if a is None:
                    return False
                if a:
                    return self.save()
            return True

        def open_dialog(self):
            if not self.ask_discard():
                return
            f = filedialog.askopenfilename(title="Open a car's .vpk (the game's vinyl folder)",
                                           filetypes=[("Sticker file", "*.vpk"), ("All", "*")],
                                           initialdir=os.path.dirname(self.pack.path) if self.pack and self.pack.path else None)
            if f:
                self.open_path(f)

        def open_path(self, path, keep=None):
            try:
                pack = Pack.load(path)
            except (VpkError, OSError) as ex:
                messagebox.showerror(APP, str(ex))
                return False
            self.pack = pack
            self.sel = None
            self.thumbs.clear()
            d = os.path.dirname(os.path.abspath(path))
            cars = sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.lower().endswith(".vpk"))
            self.car_combo.configure(values=cars, state="readonly")
            self.carvar.set(os.path.splitext(os.path.basename(path))[0])
            self.by_num = pack.by_number()
            if keep in self.by_num:
                self.sel = keep
                self.reset_version()
            self.refresh_all()
            if self.preview and self.preview.alive() and pack.path:
                p = self.car_wpk(False)                 # a different car: its own .wpk, if it can be found
                if p:
                    self.preview.set_car(p)
                else:
                    self.preview.msg.set("No %s.wpk found next to it or in a cars folder: use Choose car .wpk…" %
                                         os.path.splitext(os.path.basename(pack.path))[0])
            return True

        def switch_car(self):
            if not self.pack or not self.pack.path:
                return
            path = os.path.join(os.path.dirname(self.pack.path), self.carvar.get() + ".vpk")
            if os.path.abspath(path) == os.path.abspath(self.pack.path):
                return
            if not self.ask_discard():
                self.carvar.set(os.path.splitext(os.path.basename(self.pack.path))[0])
                return
            self.open_path(path)

        def save(self):
            if not self.pack:
                return False
            if not self.pack.path:
                return self.save_as()
            return self.do_save(self.pack.path)

        def save_as(self):
            if not self.pack:
                return False
            f = filedialog.asksaveasfilename(title="Save the car file", defaultextension=".vpk",
                                             filetypes=[("Sticker file", "*.vpk")],
                                             initialdir=os.path.dirname(self.pack.path) if self.pack.path else None,
                                             initialfile=os.path.basename(self.pack.path) if self.pack.path else "")
            return self.do_save(f) if f else False

        def do_save(self, path):
            n = len(self.pack.changed_numbers())
            try:
                st = self.pack.save(path)
            except (VpkError, OSError) as ex:
                messagebox.showerror(APP, str(ex))
                return False
            keep = self.sel
            if not self.open_path(path, keep):
                return False
            self.status.set("Saved %s  (%d sticker%s changed)%s" % (os.path.basename(path), n, "" if n == 1 else "s",
                            ("  ·  VPK2: needs the game build that reads it" if st["version"] == 2 else "")
                            + ("  ·  old file kept as .bak" if st["bak"] else "")))
            return True

        def show_page(self, index):
            """Raise a workspace page and update the active navigation item."""
            if not self.pages or not (0 <= index < len(self.pages)):
                return
            self.current_page = index
            self.pages[index].tkraise()
            for i, button in enumerate(self.nav_buttons):
                button.configure(style="VPK.NavActive.TButton" if i == index else "VPK.Nav.TButton")

        def on_stickers(self):
            """True while the sticker tab is the one in front (the keyboard shortcuts belong to it then)"""
            return self.nb is None or self.nb.index("current") == 0

        # ------------------------------------------------------------ keyboard (one table: vpk_ui.SHORTCUTS)
        def current_scope(self):
            if not self.pages or self.current_page == 0:
                return "stickers"
            return "models" if self.current_page == 1 else "other"

        def page_tab(self):
            """the object of the page in front (the sticker page is the App itself)"""
            if not self.pages:
                return self
            try:
                return self.page_objs[self.current_page]
            except IndexError:
                return None

        def key_page_call(self, name):
            """Ctrl+O / Ctrl+S act on the page that is in front (before: any page but the stickers used the Models tab)"""
            def go():
                t = self.page_tab()
                fn = getattr(t, name, None) if t is not None else None
                if callable(fn):
                    fn()
            return go

        def models_cmd(self, meth):
            """a Models menu entry: bring the page forward, then run the same function the button runs"""
            m = self.models
            if m is None:
                return
            self.show_page(1)
            if meth == "@search":
                m.search_entry.focus_set()
                m.search_entry.select_range(0, "end")
                return
            if meth in ("save", "save_as", "do_undo", "delete", "fit_view", "textures", "export") and not m.pack:
                m.status.set("Open a car .wpk first (Models > Open a car .wpk).")
                return
            getattr(m, meth)()

        def install_keys(self):
            h = {"open": self.key_page_call("open_dialog"), "save": self.key_page_call("save"), "shortcuts": self.show_shortcuts}
            if self.models is not None:
                for sid, meth in (("save_as", "save_as"), ("undo", "do_undo"), ("delete", "delete"), ("search", "@search"),
                                  ("fit", "fit_view"), ("textures", "textures"), ("export_obj", "export"), ("toggle_log", "_toggle_log")):
                    h[sid] = lambda m_=meth: self.models_cmd(m_)
            vpk_ui.install_shortcuts(self.root, vpk_ui.make_dispatch(h, self.current_scope), self.show_page, len(self.pages))

        def show_shortcuts(self):
            win = tk.Toplevel(self.root)
            win.title("Keyboard shortcuts")
            win.transient(self.root)
            win.bind("<Escape>", lambda _e: win.destroy())
            tv = ttk.Treeview(win, columns=("keys", "what", "where"), show="headings", height=16, selectmode="none")
            for c, t, w in (("keys", "Keys", 120), ("what", "Does", 280), ("where", "Where", 170)):
                tv.heading(c, text=t)
                tv.column(c, width=w, anchor="w")
            for row in vpk_ui.help_rows(self.page_names):
                tv.insert("", "end", values=row)
            tv.pack(fill="both", expand=True, padx=10, pady=10)
            ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))

        def save_window_prefs(self):
            """size, place and maximised state for next time (the Models tab saves its panel layout itself)"""
            if not wpk_view:
                return
            try:
                st = vpk_ui.window_state(self.root, tk.TclError)
                old = wpk_view.load_prefs().get("main", {})
                old = old if isinstance(old, dict) else {}
                if st == "normal":
                    wpk_view.save_prefs(main=dict(width=self.root.winfo_width(), height=self.root.winfo_height(),
                                                  x=self.root.winfo_x(), y=self.root.winfo_y(), maximized=False))
                elif st == "zoomed":
                    wpk_view.save_prefs(main=dict(old, maximized=True))
            except tk.TclError:
                pass

        def quit(self):
            if self.ask_discard() and (self.models is None or self.models.confirm_discard()):
                self.save_window_prefs()
                self.root.destroy()

        def new_from_wpk(self):
            """an add-on / imported car has no .vpk: start one with the body texture names read from its .wpk"""
            if not self.ask_discard():
                return
            fp = filedialog.askopenfilename(parent=self.root, title="The car's .wpk (data/cars/<car>.wpk)", filetypes=[("Car pack", "*.wpk"), ("All files", "*.*")])
            if not fp:
                return
            try:
                names, notes = vpk_names_from_wpk(fp)
            except Exception as ex:
                messagebox.showerror(APP, str(ex), parent=self.root)
                return
            self.pack = Pack(names)
            self.pack.src_wpk = fp                           # View > Show on the car / Export UV template use it
            self.cars_dir = os.path.dirname(os.path.abspath(fp))
            self.by_num = {}
            self.sel = None
            self.thumbs.clear()
            self.car_combo.configure(values=[], state="disabled")
            self.carvar.set("")
            self.refresh_all()
            self.status.set("New file for %s (body texture %s). Edit > Add a sticker number…, then Save as %s.vpk in the game's vinyl folder." % (
                os.path.basename(fp), names[0], os.path.splitext(os.path.basename(fp))[0]))
            if notes:
                messagebox.showinfo(APP, "\n".join(notes), parent=self.root)

        def new_empty(self):
            if not self.ask_discard():
                return
            name = simpledialog.askstring(APP, "Name of the car's body texture in its .wpk (no extension),\n"
                                               "e.g. fd3s_body01_white", parent=self.root)
            if not name or not name.strip():
                return
            self.pack = Pack([name.strip()] * 4)
            self.by_num = {}
            self.sel = None
            self.thumbs.clear()
            self.car_combo.configure(values=[], state="disabled")
            self.carvar.set("")
            self.refresh_all()
            self.status.set("New empty file. Use Edit > Add a sticker number…, then Save as…")

        # ------------------------------------------------------------ the grid
        def primary(self, n):
            es = self.pack.entries
            return min(self.by_num[n], key=lambda i: (es[i]["works"] != 0, es[i]["cb"], es[i]["body"]))

        def thumb(self, i, size):
            k = (self.pack.key(i), size)
            ph = self.thumbs.get(k)
            if ph is None:
                im = self.pack.thumb(i, size)
                if size not in self.bg_thumbs:
                    self.bg_thumbs[size] = checker(size, 6)
                bg = self.bg_thumbs[size].convert("RGBA")
                bg.alpha_composite(im)
                ph = self.thumbs[k] = ImageTk.PhotoImage(bg.convert("RGB"))
            return ph

        def on_resize(self, e):
            if e.width != self.grid_w:
                self.grid_w = e.width
                if self._redraw_job:
                    self.root.after_cancel(self._redraw_job)
                self._redraw_job = self.root.after(100, self.draw_grid)

        def wheel(self, e):
            step = -1 if (e.num == 4 or getattr(e, "delta", 0) > 0) else 1
            self.canvas.yview_scroll(step * 2, "units")

        def draw_grid(self):
            self._redraw_job = None
            c = self.canvas
            top = c.yview()[0]
            c.delete("all")
            self.imgs, self.tiles = [], {}
            self._fill_gen = getattr(self, "_fill_gen", 0) + 1       # cancels a fill still running from an older draw
            pending = []
            W = max(c.winfo_width(), 200)
            if not self.pack:
                c.create_text(W // 2, 120, justify="center", fill="#444", font=self.big,
                              text="Open a car's .vpk to begin")
                c.create_text(W // 2, 200, justify="center", fill=GREY, width=min(W - 40, 520),
                              text="Click  Open car .vpk…  and pick a file such as maz_FD3S.vpk from the game's\n"
                                   "vinyl folder. Every sticker of that car appears here, named like the in-game menu.")
                c.configure(scrollregion=(0, 0, W, 400))
                return
            nums = sorted(self.by_num)
            if not nums:
                c.create_text(W // 2, 120, justify="center", fill=GREY, width=min(W - 40, 480),
                              text="This car has no stickers.\nUse Edit > Add a sticker number… to give it one.")
                c.configure(scrollregion=(0, 0, W, 300))
                return
            avail = W - 2 * PAD
            cols = 10 if avail >= 10 * TMIN else max(1, avail // TMIN)
            self.tile = TILE = min(TMAX, avail // cols)
            self.cols = cols
            size = TILE - 12
            groups = []
            for n in nums:
                g = group_of(n)
                if groups and groups[-1][0] == g:
                    groups[-1][1].append(n)
                else:
                    groups.append((g, [n]))
            y = PAD
            for name, ns in groups:
                lo, hi = ns[0], ns[-1]
                c.create_text(PAD, y, anchor="nw", font=self.bold, fill="#222",
                              text="%s   (#%d)" % (name, lo) if lo == hi else "%s   (#%d – %d)" % (name, lo, hi))
                y += 24
                for k, n in enumerate(ns):
                    rr, cc = divmod(k, cols)
                    x, yy = PAD + cc * TILE, y + rr * (TILE + 16)
                    i = self.primary(n)
                    tag = "t%d" % n
                    c.create_rectangle(x, yy, x + TILE - 4, yy + TILE + 10, fill="#fff", outline="#ccc", tags=(tag,))
                    c.create_text(x + (TILE - 4) // 2, yy + size + 12, text=str(n), fill="#333", tags=(tag,))
                    pending.append((n, i, x, yy, tag, any(self.pack.changed(j) for j in self.by_num[n])))
                    c.tag_bind(tag, "<Button-1>", lambda _e, n=n: self.select(n))
                    self.tiles[n] = (x, yy)
                y += ((len(ns) + cols - 1) // cols) * (TILE + 16) + 8
            c.configure(scrollregion=(0, 0, W, y + PAD))
            c.yview_moveto(top)
            self.hilite()
            self.fill_thumbs(pending, size, TILE, self._fill_gen)

        def fill_thumbs(self, pending, size, TILE, gen, start=0):
            """the stickers' pictures, a few at a time, so the window is usable at once (visible rows come first)"""
            if gen != self._fill_gen or not self.pack:
                return
            c = self.canvas
            if start == 0 and pending:                       # the tiles on screen first
                top, h = c.canvasy(0), max(c.winfo_height(), 1)
                pending.sort(key=lambda p: 0 if top - TILE <= p[3] <= top + h else 1)
            for n, i, x, yy, tag, changed in pending[start:start + 10]:
                if i >= len(self.pack.entries):
                    continue
                self.imgs.append(self.thumb(i, size))
                c.create_image(x + 4, yy + 4, anchor="nw", image=self.imgs[-1], tags=(tag,))
                if changed:
                    c.create_oval(x + TILE - 22, yy + 7, x + TILE - 11, yy + 18, fill=ORANGE, outline="white", width=2, tags=(tag,))
            if start + 10 < len(pending):
                self.root.after(1, lambda: self.fill_thumbs(pending, size, TILE, gen, start + 10))
            else:
                self.hilite()

        def hilite(self):
            self.canvas.delete("sel")
            if self.sel in self.tiles:
                x, y = self.tiles[self.sel]
                T = self.tile
                self.canvas.create_rectangle(x - 1, y - 1, x + T - 3, y + T + 11, outline=ACCENT, width=3, tags=("sel",))

        def ensure_visible(self, n):
            if n not in self.tiles:
                return
            x, y = self.tiles[n]
            c = self.canvas
            region = float(c.cget("scrollregion").split()[3]) if c.cget("scrollregion") else 1
            h = c.winfo_height()
            top, bot = c.canvasy(0), c.canvasy(h)
            if y < top:
                c.yview_moveto(max(0.0, (y - PAD - 24) / region))
            elif y + self.tile + 16 > bot:
                c.yview_moveto(min(1.0, (y + self.tile + 16 + PAD - h) / region))

        def move(self, dx, dy):
            if not self.pack or not self.tiles:
                return
            if self.sel not in self.tiles:
                self.select(min(self.tiles))
                return
            cx, cy = self.tiles[self.sel]
            best, bk = None, None
            for n, (x, y) in self.tiles.items():
                if n == self.sel:
                    continue
                if dx and y == cy and (x - cx) * dx > 0:
                    k = abs(x - cx)
                elif dy and (y - cy) * dy > 0:
                    k = (abs(y - cy), abs(x - cx))
                else:
                    continue
                if bk is None or k < bk:
                    best, bk = n, k
            if best is None and dx:
                order = sorted(self.tiles)
                j = order.index(self.sel) + dx
                if 0 <= j < len(order):
                    best = order[j]
            if best is not None:
                self.select(best)
                self.ensure_visible(best)

        # ------------------------------------------------------------ selection / panel
        def versions(self, n):
            """distinct (works, cb) versions of sticker n"""
            es = self.pack.entries
            return sorted({(es[i]["works"], es[i]["cb"]) for i in self.by_num[n]})

        def ver_label(self, v):
            w, cb = v
            s = "Any paint" if not w else "Works %d%s" % (w, " (black paint)" if w == 1 else " (white paint)" if w == 2 else "")
            return s + (", carbon bonnet" if cb else "")

        def ver_entries(self, n, v):
            es = self.pack.entries
            return [i for i in self.by_num[n] if (es[i]["works"], es[i]["cb"]) == v]

        def bodies_differ(self, idxs):
            return len({self.pack.key(i) for i in idxs}) > 1

        def reset_version(self):
            n = self.sel
            vs = self.versions(n)
            self.ver = (0, 0) if (0, 0) in vs else vs[0]
            self.body = min(self.pack.entries[i]["body"] for i in self.ver_entries(n, self.ver))
            self.v_all.set(False)

        def select(self, n):
            if not self.pack or n not in self.by_num:
                return
            self.sel = n
            self.reset_version()
            self.hilite()
            self.show_detail()
            self.update_steps()

        def pick_version(self):
            for v in self.versions(self.sel):
                if self.ver_label(v) == self.v_ver.get():
                    self.ver = v
            bs = sorted(self.pack.entries[i]["body"] for i in self.ver_entries(self.sel, self.ver))
            if self.body not in bs:
                self.body = bs[0]
            self.show_detail()

        def pick_body(self):
            if self.v_body.get() in BODIES:
                self.body = BODIES.index(self.v_body.get())
            self.show_detail()

        def current(self):
            """entry index being shown"""
            for i in self.ver_entries(self.sel, self.ver):
                if self.pack.entries[i]["body"] == self.body:
                    return i
            return self.ver_entries(self.sel, self.ver)[0]

        def targets(self):
            idxs = self.ver_entries(self.sel, self.ver)
            if self.bodies_differ(idxs) and not self.v_all.get():
                return [self.current()]
            return idxs

        def show_detail(self):
            if self.sel is None or not self.pack or self.sel not in self.by_num:
                self.title.configure(text="No sticker selected")
                self.sub.configure(text="")
                self.prev.delete("all")
                self.draw_big()
                self.info.configure(text="")
                self.opts.grid_remove()
                for b in (self.btn_rep, self.btn_col, self.btn_exp, self.btn_undo):
                    b.configure(state="disabled")
                self.hint.configure(text="Click a sticker on the left." if self.pack else "")
                self.update_preview()
                return
            n = self.sel
            idxs = self.ver_entries(n, self.ver)
            differ = self.bodies_differ(idxs)
            vs = self.versions(n)
            self.title.configure(text="%s  ·  #%d" % (group_of(n), n))
            sub = []
            if n in range(1, 78) and group_of(n) != "Works colours" and group_of(n) not in ("Namco", "Pac-Man"):
                a = [g for g in GROUPS if g[0] <= n <= g[1]][0]
                sub.append("colour %d of %d" % (n - a[0] + 1, a[1] - a[0] + 1))
            self.sub.configure(text="  ".join(sub))
            self.opts.grid()
            if len(vs) > 1:
                self.lab_ver.grid()
                self.cb_ver.grid()
                self.cb_ver.configure(values=[self.ver_label(v) for v in vs])
                self.v_ver.set(self.ver_label(self.ver))
            else:
                self.lab_ver.grid_remove()
                self.cb_ver.grid_remove()
            if differ:
                self.lab_body.grid()
                self.cb_body.grid()
                self.chk_all.grid()
                bs = sorted(self.pack.entries[i]["body"] for i in idxs)
                self.cb_body.configure(values=[BODIES[b] for b in bs])
                self.v_body.set(BODIES[self.body])
            else:
                self.lab_body.grid_remove()
                self.cb_body.grid_remove()
                self.chk_all.grid_remove()
            i = self.current()
            e = self.pack.entries[i]
            im = self.pack.thumb(i, 256)
            bg = self.bg_prev.convert("RGBA")
            bg.alpha_composite(im)
            uv = self.uv_layer(i, 256)
            if uv is not None:
                bg.alpha_composite(uv)
            self.photo = ImageTk.PhotoImage(bg.convert("RGB"))
            self.prev.delete("all")
            self.prev.create_image(2, 2, anchor="nw", image=self.photo)
            self.draw_big()
            kind = ("One colour  (%d, %d, %d)  + shape" % e["col"]) if e["kind"] else "Full-colour picture"
            same = "" if differ else ("   ·   same on all %d bod%s" % (len(idxs), "y" if len(idxs) == 1 else "ies")
                                       if len(idxs) > 1 else "")
            ch = any(self.pack.changed(j) for j in self.by_num[n])
            self.info.configure(text=kind + same + ("\nChanged — not saved yet" if ch else ""),
                                foreground=ORANGE if ch else GREY)
            self.btn_rep.configure(state="normal")
            self.btn_col.configure(state="normal" if all(self.pack.entries[j]["kind"] == 1 for j in self.targets()) else "disabled")
            self.btn_exp.configure(state="normal")
            self.btn_undo.configure(state="normal" if ch else "disabled")
            self.hint.configure(text="Draw it on this car's own sticker template. Any size works; it becomes %d × %d%s." % (
                self.import_size(), self.import_size(),
                " (this picture is %d × %d now)" % (self.pack.size_of(i), self.pack.size_of(i))
                if self.pack.size_of(i) != self.import_size() else ""))
            self.update_preview()

        # ------------------------------------------------------------ live car preview
        def ask_wpk(self):
            return filedialog.askopenfilename(
                title="The car's .wpk (the game's data/cars folder)", filetypes=[("Car pack", "*.wpk"), ("All", "*")],
                initialdir=self.cars_dir or (os.path.dirname(self.pack.path) if self.pack and self.pack.path else None))

        def car_wpk(self, ask):
            """the .wpk that belongs to the open .vpk: found by name, else (ask) the user picks it"""
            src = getattr(self.pack, "src_wpk", None) if self.pack else None
            if src and os.path.exists(src):                   # a new file made from a car .wpk knows its car
                return src
            if not self.pack or not self.pack.path:
                return None
            p = wpk_view.find_wpk(self.pack.path, [self.cars_dir] if self.cars_dir else [])
            if p is None and ask:
                p = self.ask_wpk()
            if p:
                self.cars_dir = os.path.dirname(os.path.abspath(p))
            return p or None

        def open_preview(self):
            if not wpk_view:
                messagebox.showinfo(APP, "wpk_view.py is missing next to vpk_tool.py.")
                return
            if not self.pack:
                messagebox.showinfo(APP, "Open a car's .vpk first.")
                return
            if self.preview and self.preview.alive():
                self.preview.top.lift()
                self.update_preview()
                return
            p = self.car_wpk(True)
            if not p:
                return
            self.preview = wpk_view.PreviewWindow(self.root, self.ask_wpk, on_close=self.preview_closed)
            self.preview.set_car(p)
            self.update_preview()

        # ------------------------------------------------------------ UV layout as a layer over the sticker
        def uv_segs(self, i):
            """the car's UV panel borders (vector segments) for the body of entry i, or None"""
            if not wpk_view or not self.pack:
                return None
            body = self.pack.entries[i]["body"]
            stem = (self.pack.page1[body] or self.pack.page1[0]).strip()
            p0 = self.car_wpk(False)
            p = wpk_view.body_wpk(p0, body) if p0 else None
            if not p:
                return None
            try:
                if p not in self.uv_models:
                    self.uv_models[p] = uv_model(p)
                m = self.uv_models[p]
                return wpk_view.uv_segments(m, wpk_view._stem(stem) if stem else wpk_view.body_stem(m),
                                            wpk_view.default_chunks(m))
            except Exception as ex:
                self.v_uv.set(False)
                self.status.set("UV layout not available: %s" % ex)
                return None

        def uv_layer(self, i, size, view=(0.0, 0.0, 1.0, 1.0)):
            """the UV borders as a transparent RGBA picture (size x size) of the window `view` of the sticker,
            or None when the layer is off. Drawn from vector lines: crisp at any size and zoom."""
            if not self.v_uv.get():
                return None
            seg = self.uv_segs(i)
            if seg is None:
                return None
            base = {"Thin": 1, "Normal": 2, "Thick": 4}.get(self.v_uvw.get(), 2)
            width = max(1, int(math.ceil(base * size / 768.0)))
            op = float(self.v_uvop.get())
            return wpk_view.draw_segments(seg, size, view, width, (255, 0, 90, int(255 * op)))

        def toggle_uv(self):
            if self.v_uv.get():
                if not self.pack or not self.car_wpk(True):
                    self.v_uv.set(False)
                    return
            pv = self.preview
            if pv and pv.alive():                         # the car shows the same layer, live
                pv.v_uvcar.set("Borders" if self.v_uv.get() else "Off")
                pv.show()
            self.show_detail()

        # ---- big view: wheel = zoom at the cursor, drag = move, double-click = whole sticker
        def open_big(self):
            if not self.pack or self.sel not in self.by_num:
                messagebox.showinfo(APP, "Click a sticker on the left first.")
                return
            if self.bigwin and self.bigwin.winfo_exists():
                self.bigwin.lift()
            else:
                w = self.bigwin = tk.Toplevel(self.root)
                w.title("Sticker, big view")
                w.resizable(True, True)
                w.minsize(420, 420)
                self.bsz, self.bfull, self.bjob = 768, False, None

                def toggle_big_max(_e=None):
                    self.bfull = not self.bfull
                    vpk_ui.set_maximised(w, self.bfull, tk.TclError)          # Windows / macOS / Linux
                self.toggle_big_max = toggle_big_max
                bar = ttk.Frame(w, padding=(8, 6))
                bar.pack(fill="x")
                ttk.Checkbutton(bar, text="Show UV layout (red = the car's real panel borders)", variable=self.v_uv,
                                command=self.toggle_uv).pack(side="left")
                self.big_lab = ttk.Label(bar, text="100%", width=6)
                self.big_lab.pack(side="right")
                ttk.Button(bar, text="Whole", width=6, command=self.big_reset).pack(side="right", padx=4)
                ttk.Button(bar, text="Maximize", width=9, command=toggle_big_max).pack(side="right", padx=4)
                ttk.Label(bar, text="wheel = zoom, drag = move").pack(side="right", padx=8)
                self.bigcv = tk.Canvas(w, width=768, height=768, highlightthickness=0)
                self.bigcv.pack(fill="both", expand=True)

                def big_resized(e):                                  # the picture is the biggest square that fits
                    n = max(256, min(e.width, e.height))
                    if abs(n - self.bsz) < 8:
                        return
                    if self.bjob:
                        w.after_cancel(self.bjob)
                    self.bjob = w.after(150, lambda: (setattr(self, "bsz", n), self.draw_big(), save_big()))
                def save_big():
                    try:
                        if wpk_view and w.winfo_exists() and vpk_ui.window_state(w, tk.TclError) == "normal":
                            wpk_view.save_prefs(big=dict(width=w.winfo_width(), height=w.winfo_height()))
                    except tk.TclError:
                        pass
                try:
                    bs = (wpk_view.load_prefs() if wpk_view else {}).get("big", {})
                    bw_, bh_ = int(bs.get("width", 0)), int(bs.get("height", 0))
                    if 420 <= bw_ <= self.root.winfo_screenwidth() and 420 <= bh_ <= self.root.winfo_screenheight():
                        w.geometry("%dx%d" % (bw_, bh_))
                except (TypeError, ValueError, tk.TclError):
                    pass
                self.bigcv.bind("<Configure>", big_resized)
                w.bind("<F11>", toggle_big_max)
                self.bz = [1.0, 0.5, 0.5]                    # zoom, centre x, centre y (0..1 of the sticker)
                self.bigdrag = None
                for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    self.bigcv.bind(seq, self.big_wheel)
                self.bigcv.bind("<ButtonPress-1>", lambda e: setattr(self, "bigdrag", (e.x, e.y)))
                self.bigcv.bind("<B1-Motion>", self.big_pan)
                self.bigcv.bind("<Double-Button-1>", lambda _e: self.big_reset())
            self.draw_big()

        def big_view(self):
            z, cx, cy = self.bz
            half = 0.5 / z
            cx, cy = min(max(cx, half), 1 - half), min(max(cy, half), 1 - half)
            self.bz[1], self.bz[2] = cx, cy
            return (cx - half, cy - half, 2 * half, 2 * half)

        def big_wheel(self, e):
            z, cx, cy = self.bz
            x, y, w, h = self.big_view()
            fx, fy = x + w * e.x / float(self.bsz), y + h * e.y / float(self.bsz)           # sticker point under the cursor
            nz = min(32.0, max(1.0, z * (1.25 if (e.num == 4 or getattr(e, "delta", 0) > 0) else 0.8)))
            nw = 1.0 / nz
            self.bz = [nz, fx - nw * (e.x / float(self.bsz) - 0.5), fy - nw * (e.y / float(self.bsz) - 0.5)]
            self.draw_big()
            return "break"

        def big_pan(self, e):
            if not self.bigdrag:
                return
            x, y, w, h = self.big_view()
            self.bz[1] -= (e.x - self.bigdrag[0]) / float(self.bsz) * w
            self.bz[2] -= (e.y - self.bigdrag[1]) / float(self.bsz) * h
            self.bigdrag = (e.x, e.y)
            self.draw_big()

        def big_reset(self):
            self.bz = [1.0, 0.5, 0.5]
            self.draw_big()

        def draw_big(self):
            if not (self.bigwin and self.bigwin.winfo_exists()):
                return
            self.bigcv.delete("all")
            if not self.pack or self.sel not in self.by_num:
                return
            i = self.current()
            x, y, w, h = self.big_view()
            src = self.pack.image(i)
            n = self.bsz
            if getattr(self, "bigbg_n", 0) != n:
                self.bigbg, self.bigbg_n = checker(n, 16).convert("RGBA"), n
            bg = self.bigbg.copy()
            box = (x * src.width, y * src.height, (x + w) * src.width, (y + h) * src.height)
            res = Image.NEAREST if self.bz[0] >= 3 else Image.BICUBIC          # show real pixels when zoomed in
            bg.alpha_composite(src.resize((n, n), res, box=box))
            uv = self.uv_layer(i, n, (x, y, w, h))
            if uv is not None:
                bg.alpha_composite(uv)
            self.bigphoto = ImageTk.PhotoImage(bg.convert("RGB"))
            self.bigcv.create_image(0, 0, anchor="nw", image=self.bigphoto)
            self.big_lab.configure(text="%d%%" % round(self.bz[0] * 100))

        # ---- fit a picture to the UV layout (pins)
        def open_fit(self):
            if not (wpk_view and uv_fit):
                messagebox.showinfo(APP, "uv_fit.py is missing next to vpk_tool.py.")
                return
            if not self.pack or self.sel not in self.by_num:
                messagebox.showinfo(APP, "Click a sticker on the left first.")
                return
            if not self.car_wpk(True):
                return
            i = self.current()
            seg = self.uv_segs(i)
            if seg is None:
                messagebox.showinfo(APP, "Cannot read this car's UV layout.")
                return
            f = filedialog.askopenfilename(title="Picture to fit onto the car's UV layout",
                                           filetypes=[("Images", "*.png *.tga *.bmp *.jpg *.jpeg *.webp"), ("All", "*")])
            if not f:
                return
            try:
                art = Image.open(f)
                art.load()
            except (OSError, ValueError) as ex:
                messagebox.showerror(APP, "Cannot read that image: %s" % ex)
                return
            sel = self.sel

            def apply(im):
                if self.pack and sel in self.by_num:
                    idxs = self.targets()
                    self.pack.set_image(idxs, im, "stretch", self.import_size())
                    self.touched("Sticker #%d: %s fitted to the UV layout on %d entr%s" % (
                        sel, os.path.basename(f), len(idxs), "y" if len(idxs) == 1 else "ies"))
            self.fitwin = uv_fit.FitWindow(self.root, art, seg, "Fit to UV: %s" % os.path.basename(f), apply,
                                           wpk_view.draw_segments)

        def run_bulk(self, title, work, done_text):
            """a progress window for a long export. work(progress, cancelled) runs in the background and returns
            (done, failed); done_text(done, failed) -> the message shown at the end."""
            win = tk.Toplevel(self.root)
            win.title(title)
            win.resizable(False, False)
            lab = ttk.Label(win, text="Starting …", width=60, padding=10)
            lab.pack()
            bar = ttk.Progressbar(win, length=420, maximum=1.0)
            bar.pack(padx=10)
            stop = [False]
            ttk.Button(win, text="Stop", command=lambda: stop.__setitem__(0, True)).pack(pady=10)
            win.protocol("WM_DELETE_WINDOW", lambda: stop.__setitem__(0, True))
            box = {"p": (0, 1, ""), "res": None}

            def run():
                try:
                    box["res"] = work(lambda n, t, nm: box.__setitem__("p", (n, t, nm)), lambda: stop[0])
                except Exception as ex:
                    box["res"] = ex

            threading.Thread(target=run, daemon=True).start()

            def poll():
                n, tot, nm = box["p"]
                lab.configure(text="%d / %d   %s" % (n, tot, nm))
                bar["value"] = n / max(tot, 1)
                if box["res"] is None:
                    win.after(150, poll)
                    return
                win.destroy()
                r = box["res"]
                if isinstance(r, Exception):
                    messagebox.showerror(APP, "Export failed: %s" % r)
                else:
                    msg = done_text(*r)
                    self.status.set(" ".join(msg.split()), "success")
                    messagebox.showinfo(APP, msg)
            poll()

        def ask_cars_and_out(self, what):
            src = filedialog.askdirectory(title="Folder with the cars' .wpk files (sub-folders are included)",
                                          initialdir=self.cars_dir or os.getcwd())
            if not src:
                return None
            out = filedialog.askdirectory(title="Where to save the %s" % what)
            return (src, out) if out else None

        def export_all_uv(self):
            """one UV template PNG for every car .wpk (and aero kit) in a folder, in the background"""
            if not wpk_view:
                messagebox.showinfo(APP, "wpk_view.py is missing next to vpk_tool.py.")
                return
            from tkinter import simpledialog
            so = self.ask_cars_and_out("UV templates")
            if not so:
                return
            src, out = so
            size = simpledialog.askinteger(APP, "Template size in pixels (256 - 4096):", initialvalue=2048,
                                           minvalue=256, maxvalue=4096)
            if not size:
                return
            self.run_bulk("Exporting UV templates",
                          lambda prog, stop: wpk_view.export_all_uv(src, out, size, prog, stop),
                          lambda done, failed: "%d UV templates saved to:\n%s%s" % (
                              done, out, ("\n\n%d files could not be read (listed in index.txt)." % len(failed))
                              if failed else ""))

        def export_all_textures(self):
            """every texture of every car .wpk (and aero kit) in a folder as PNGs, one sub-folder per car"""
            if not wpk_view:
                messagebox.showinfo(APP, "wpk_view.py is missing next to vpk_tool.py.")
                return
            so = self.ask_cars_and_out("car textures")
            if not so:
                return
            src, out = so
            self.run_bulk("Exporting car textures",
                          lambda prog, stop: wpk_view.export_all_textures(src, out, prog, stop),
                          lambda done, failed: "Textures of %d cars saved to:\n%s%s" % (
                              done, out, ("\n\n%d files could not be read (listed in index.txt)." % len(failed))
                              if failed else ""))

        def export_car_textures(self):
            """every texture inside the open car's .wpk (lamps, plate, body paints, wheels ...) as PNG files"""
            if not wpk_view:
                messagebox.showinfo(APP, "wpk_view.py is missing next to vpk_tool.py.")
                return
            if not self.pack:
                messagebox.showinfo(APP, "Open a car's .vpk first.")
                return
            p = self.car_wpk(True)
            if not p:
                return
            d = filedialog.askdirectory(title="Folder for the car's textures")
            if not d:
                return
            try:
                sp = wpk_view.stock_of(p)
                m = wpk_view.read_wpk3(p, wpk_view.read_wpk3(sp)[0] if sp else None)
                target = os.path.join(d, os.path.splitext(os.path.basename(p))[0] + "_textures")
                files = wpk_view.export_textures(m, target)
                self.status.set("Saved %d textures to %s" % (len(files), target))
            except Exception as ex:
                messagebox.showerror(APP, "Cannot export the textures:\n%s" % ex)

        def save_uv_template(self):
            """the car's exact UV layout (for the selected sticker's body) as a PNG to paint on"""
            if not wpk_view:
                messagebox.showinfo(APP, "wpk_view.py is missing next to vpk_tool.py.")
                return
            if not self.pack:
                messagebox.showinfo(APP, "Open a car's .vpk first.")
                return
            p = self.car_wpk(True)
            if not p:
                return
            body = 0
            if self.sel in self.by_num:
                body = self.pack.entries[self.current()]["body"]
            stem = (self.pack.page1[body] or self.pack.page1[0]).strip()
            base = os.path.splitext(os.path.basename(p))[0]
            f = filedialog.asksaveasfilename(title="Export UV template", defaultextension=".png",
                                             initialfile="%s_uv.png" % base, filetypes=[("PNG", "*.png")])
            if not f:
                return
            try:
                m = uv_model(p)
                im, n = wpk_view.uv_template(m, wpk_view._stem(stem) if stem else wpk_view.body_stem(m), 2048,
                                             wpk_view.default_chunks(m))
                im.save(f)
                self.status.set("Saved %s (%d islands). Paint on it, hide the template layer, export 512 px, Replace image."
                                % (os.path.basename(f), n))
            except Exception as ex:
                messagebox.showerror(APP, "Cannot make the template:\n%s" % ex)

        def export_uv_from_wpk(self):
            """the UV template of any car .wpk, with no .vpk open (an add-on car has none yet)"""
            if not wpk_view:
                messagebox.showinfo(APP, "wpk_view.py is missing next to vpk_tool.py.")
                return
            p = self.ask_wpk()
            if not p:
                return
            self.cars_dir = os.path.dirname(os.path.abspath(p))
            f = filedialog.asksaveasfilename(title="Export UV template", defaultextension=".png",
                                             initialfile="%s_uv.png" % os.path.splitext(os.path.basename(p))[0], filetypes=[("PNG", "*.png")])
            if not f:
                return
            try:
                m = uv_model(p)
                im, n = wpk_view.uv_template(m, wpk_view.body_stem(m), 2048, wpk_view.default_chunks(m))
                im.save(f)
                self.status.set("Saved %s (%d islands). Paint on it, hide the template layer, export 512 px, Replace image." % (os.path.basename(f), n))
            except Exception as ex:
                messagebox.showerror(APP, "Cannot make the template:\n%s" % ex)

        def preview_closed(self):
            self.preview = None

        def update_preview(self):
            """show the sticker that is selected on the car (called whenever the selection or a picture changes)"""
            pv = self.preview
            if not pv or not pv.alive():
                return
            tex = ""
            edited = original = None
            if self.pack and self.sel in self.by_num:
                i = self.current()
                e = self.pack.entries[i]
                tex = (self.pack.page1[e["body"]] or self.pack.page1[0]).strip()
                try:
                    edited = self.pack.sticker_image(i)
                    original = self.pack.sticker_image(i, original=True)
                except (ValueError, IndexError):
                    pass
            pv.set_sticker(edited, original, tex)

        # ------------------------------------------------------------ actions
        def touched(self, msg):
            self.by_num = self.pack.by_number()
            if self.sel not in self.by_num:
                self.sel = None
            self.refresh_all()
            self.status.set(msg)

        def import_size(self):
            """the side new stickers are made at"""
            return int(self.size_var.get())

        def size_changed(self):
            n = self.import_size()
            if self.pack:
                self.refresh_all()
            self.status.set("New stickers will be %d × %d%s." % (
                n, n, "  (a file with one needs the game build that reads VPK2)" if n > SIZE else ""))

        def replace_image(self):
            if not self.pack or self.sel is None:
                return
            f = filedialog.askopenfilename(title="Image for sticker #%d" % self.sel,
                                           filetypes=[("Images", "*.png *.tga *.bmp *.jpg *.jpeg *.webp"), ("All", "*")])
            if not f:
                return
            try:
                im = Image.open(f)
                im.load()
            except (OSError, ValueError) as ex:
                messagebox.showerror(APP, "Cannot read that image: %s" % ex)
                return
            idxs = self.targets()
            side = self.import_size()
            self.pack.set_image(idxs, im, self.fit.get(), side)
            note = "  [%d × %d]" % (side, side)
            if im.width != im.height:
                note += "  (not square: %s)" % ("stretched" if self.fit.get() == "stretch" else "padded")
            self.touched("Sticker #%d replaced from %s on %d entr%s%s" % (
                self.sel, os.path.basename(f), len(idxs), "y" if len(idxs) == 1 else "ies", note))

        def change_colour(self):
            idxs = self.targets()
            c0 = self.pack.entries[idxs[0]]["col"]
            rgb, _hexv = colorchooser.askcolor(color="#%02x%02x%02x" % c0, title="Colour of sticker #%d" % self.sel)
            if rgb:
                self.pack.set_colour(idxs, rgb)
                self.touched("Sticker #%d colour changed" % self.sel)

        def export_one(self):
            f = filedialog.asksaveasfilename(title="Save this sticker as PNG", defaultextension=".png",
                                             initialfile="%02d_%s.png" % (self.sel, group_of(self.sel).replace(" ", "-")),
                                             filetypes=[("PNG", "*.png")])
            if f:
                self.pack.image(self.current()).save(f)
                self.status.set("Saved " + f)

        def undo(self):
            if self.sel is None:
                return
            self.pack.revert(self.by_num[self.sel])
            self.thumbs.clear()
            self.touched("Sticker #%d put back" % self.sel)
            if self.sel is not None:
                self.reset_version()
                self.show_detail()

        def undo_all(self):
            if not self.pack or not (self.pack.changed_numbers() or self.pack.head_changed()):
                return
            if not vpk_ui.confirm_destructive(messagebox, self.root, APP, "Undo every change you have not saved?", undoable=False):
                return
            self.pack.revert(list(range(len(self.pack.entries))))
            self.pack.page1, self.pack.works = list(self.pack.orig_head[0]), list(self.pack.orig_head[1])
            self.thumbs.clear()
            self.touched("All changes undone")

        def add_sticker(self):
            if not self.pack:
                messagebox.showinfo(APP, "Open a car file first.")
                return
            n = simpledialog.askinteger(APP, "New sticker number (1 – 255).\nThe in-game menu uses 1 – 77.", parent=self.root,
                                        minvalue=1, maxvalue=255)
            if not n:
                return
            if n in self.by_num:
                messagebox.showinfo(APP, "Sticker %d already exists. Click it and use Replace image." % n)
                return
            f = filedialog.askopenfilename(title="Image for the new sticker #%d" % n,
                                           filetypes=[("Images", "*.png *.tga *.bmp *.jpg *.jpeg *.webp"), ("All", "*")])
            if not f:
                return
            try:
                im = Image.open(f)
                im.load()
                self.pack.add_sticker(n, im, self.fit.get(), size=self.import_size())
            except (OSError, ValueError, VpkError) as ex:
                messagebox.showerror(APP, str(ex))
                return
            self.touched("Sticker #%d added on all four bodies" % n)
            self.select(n)
            self.ensure_visible(n)

        def car_settings(self):
            if not self.pack:
                messagebox.showinfo(APP, "Open a car file first.")
                return
            win = tk.Toplevel(self.root)
            win.title("Car settings")
            win.transient(self.root)
            win.resizable(False, False)
            fr = ttk.Frame(win, padding=12)
            fr.pack()
            vs = [tk.StringVar(value=n) for n in self.pack.page1]
            for i in range(4):
                ttk.Label(fr, text="Texture, " + BODIES[i].lower() + " body").grid(row=i, column=0, sticky="w", pady=2)
                ttk.Entry(fr, textvariable=vs[i], width=34).grid(row=i, column=1, padx=(8, 0), pady=2)
            ttk.Label(fr, foreground=GREY, wraplength=420, justify="left",
                      text="The stickers are blended onto this texture of the car's .wpk. It comes from the file, "
                           "so leave it unless the car's texture has another name. Aero names may be empty.").grid(
                row=4, column=0, columnspan=2, sticky="w", pady=(6, 8))
            wv = tk.StringVar(value=",".join(str(x) for x in self.pack.works))
            ttk.Label(fr, text="Works index by paint").grid(row=5, column=0, sticky="w")
            ttk.Entry(fr, textvariable=wv, width=34).grid(row=5, column=1, padx=(8, 0))
            ttk.Label(fr, foreground=GREY, wraplength=420, justify="left",
                      text="One number per paint (up to 32). Only used by the 'works' livery (1 black, 2 white).").grid(
                row=6, column=0, columnspan=2, sticky="w", pady=(4, 8))

            def ok():
                try:
                    w = [int(x) for x in wv.get().replace(" ", "").split(",") if x != ""] or [1]
                except ValueError:
                    messagebox.showwarning(APP, "The works list must be numbers separated by commas.", parent=win)
                    return
                self.pack.page1 = [v.get().strip() for v in vs]
                self.pack.works = (w + [1] * 32)[:32]
                win.destroy()
                self.refresh_all()
                self.status.set("Car settings changed")
            bf = ttk.Frame(fr)
            bf.grid(row=7, column=0, columnspan=2, sticky="e")
            ttk.Button(bf, text="Cancel", command=win.destroy).pack(side="right")
            ttk.Button(bf, text="OK", command=ok).pack(side="right", padx=6)
            win.grab_set()

        def export_all(self):
            if not self.pack or not self.pack.entries:
                messagebox.showinfo(APP, "Open a car file first.")
                return
            d = filedialog.askdirectory(title="Folder for the PNG files (a new sub-folder is made)")
            if not d:
                return
            name = os.path.splitext(os.path.basename(self.pack.path))[0] if self.pack.path else "stickers"
            out = os.path.join(d, name + "_png")
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            try:
                n = export_pngs(self.pack, out, log=lambda m: None)
            finally:
                self.root.config(cursor="")
            self.status.set("Wrote %d PNG files to %s — edit them, then File > Import PNGs" % (n, out))

        def import_folder(self):
            if not self.pack:
                messagebox.showinfo(APP, "Open a car file first.")
                return
            d = filedialog.askdirectory(title="Folder with the edited PNG files")
            if not d:
                return
            self.root.config(cursor="watch")
            self.root.update_idletasks()
            try:
                changed, notes = import_pngs(self.pack, d, self.fit.get(), log=lambda m: None, size=self.import_size())
            finally:
                self.root.config(cursor="")
            self.touched("%d sticker%s changed from the folder%s" % (len(changed), "" if len(changed) == 1 else "s",
                         ("  ·  %d file(s) skipped" % len(notes)) if notes else ""))
            if notes:
                messagebox.showinfo(APP, "Skipped:\n" + "\n".join(notes[:15]))

        # ------------------------------------------------------------ the textures inside a car .wpk (glass, lamps ...)
        def textures_window(self):
            """list the car pack's textures; export one (or its UV guide) to paint on, put the edited picture back"""
            if not (wpk_tex and wpk_view):
                messagebox.showinfo(APP, "wpk_tex.py / wpk_view.py are missing next to vpk_tool.py.")
                return
            p = self.car_wpk(False) if self.pack and self.pack.path else None
            if not p:
                p = filedialog.askopenfilename(title="The car's .wpk (the game's data/cars folder)",
                                               filetypes=[("Car pack", "*.wpk"), ("All", "*")],
                                               initialdir=self.cars_dir or None)
            if not p:
                return
            st = {"path": p, "model": None}
            win = tk.Toplevel(self.root)
            win.title("Car textures")
            win.geometry("860x520")
            top = ttk.Frame(win, padding=8)
            top.pack(fill="both", expand=True)
            head = ttk.Label(top, text="", justify="left")
            head.pack(anchor="w")
            cols = ("name", "size", "fmt", "use")
            tree = ttk.Treeview(top, columns=cols, show="headings", height=14, selectmode="browse")
            for c, t, w in (("name", "Texture", 250), ("size", "Size", 70), ("fmt", "Format", 80), ("use", "Used by", 400)):
                tree.heading(c, text=t)
                tree.column(c, width=w, anchor="w")
            tree.pack(fill="both", expand=True, pady=6)
            hint = ("The window glass is the texture ending in body02_cola01: its panes use only a small part of the picture, "
                    "so export it at 1024 or 2048 to paint on them. Whatever you put into a texture is on the car for "
                    "everybody (it is not a sticker).")
            ttk.Label(top, text=hint, wraplength=820, justify="left").pack(anchor="w", pady=(0, 6))
            opt = ttk.Frame(top)
            opt.pack(fill="x")
            ttk.Label(opt, text="Export at").pack(side="left")
            exp_size = tk.StringVar(value="2048")
            ttk.Combobox(opt, textvariable=exp_size, values=("as it is", "512", "1024", "2048"), width=8,
                         state="readonly").pack(side="left", padx=6)
            dither = tk.BooleanVar(value=False)
            ttk.Checkbutton(opt, text="Dither on import (smoother gradients in a 4 / 5 bit texture)",
                            variable=dither).pack(side="left", padx=14)
            btns = ttk.Frame(top)
            btns.pack(fill="x", pady=(8, 0))
            rows = []

            def load():
                st["model"] = None
                try:
                    rows[:] = texture_rows(st["path"])
                except (wpk_tex.TexError, wpk_view.WpkError, OSError, ValueError, struct.error) as ex:
                    messagebox.showerror(APP, "Cannot read %s: %s" % (os.path.basename(st["path"]), ex))
                    return
                head.configure(text="%s" % st["path"])
                tree.delete(*tree.get_children())
                for r in rows:
                    use = ("%d triangles%s" % (r["tris"], ", see-through" if r["blended"] else "")) if r["tris"] else "-"
                    if r["borrowed"]:
                        use += "   (borrowed from the stock pack)"
                    elif not r["editable"]:
                        use += "   (compressed: cannot be edited)"
                    tree.insert("", "end", iid=str(r["index"]),
                                values=(r["name"], "%d × %d" % (r["w"], r["h"]), r["fmt_name"], use))
                for r in rows:                                    # select the glass: what most people come for
                    if "body02_col" in r["name"].lower() and not r["borrowed"]:
                        tree.selection_set(str(r["index"]))
                        tree.see(str(r["index"]))
                        break

            def picked(need_edit=False):
                sel = tree.selection()
                if not sel:
                    messagebox.showinfo(APP, "Pick a texture in the list first.")
                    return None
                r = rows[int(sel[0])]
                if r["borrowed"]:
                    messagebox.showinfo(APP, "This aero kit borrows '%s' from the stock pack: open the stock car's .wpk."
                                        % r["name"])
                    return None
                if need_edit and not r["editable"]:
                    messagebox.showinfo(APP, "This texture is compressed and cannot be edited.")
                    return None
                return r

            def model():
                if st["model"] is None:
                    win.config(cursor="watch")
                    win.update_idletasks()
                    try:
                        st["model"] = uv_model(st["path"])
                    finally:
                        win.config(cursor="")
                return st["model"]

            def export_size():
                v = exp_size.get()
                return None if v == "as it is" else int(v)

            def save_name(r, tag):
                return "%s_%s%s.png" % (wpk_tex.car_name(st["path"]), os.path.splitext(r["name"])[0].replace(":", "_"), tag)

            def do_export():
                r = picked()
                if not r:
                    return
                f = filedialog.asksaveasfilename(title="Export the texture as a PNG", defaultextension=".png",
                                                 initialfile=save_name(r, ""), filetypes=[("PNG", "*.png")])
                if not f:
                    return
                try:
                    with open(st["path"], "rb") as fh:
                        im = wpk_tex.export_image(fh.read(), r["index"], os.path.basename(st["path"]), export_size())
                    im.save(f)
                except (wpk_tex.TexError, OSError) as ex:
                    messagebox.showerror(APP, str(ex))
                    return
                self.status.set("Exported %s (%d × %d). Paint on it, keep its size, and import it back." % (
                    os.path.basename(f), im.width, im.height))

            def do_guide():
                r = picked()
                if not r:
                    return
                f = filedialog.asksaveasfilename(title="Save the UV guide (the texture with the UV borders)",
                                                 defaultextension=".png", initialfile=save_name(r, "_uv"),
                                                 filetypes=[("PNG", "*.png")])
                if not f:
                    return
                try:
                    im, drawn = wpk_view.texture_guide(model(), r["index"], export_size() or max(r["w"], r["h"]))
                    im.save(f)
                except (wpk_view.WpkError, OSError) as ex:
                    messagebox.showerror(APP, str(ex))
                    return
                self.status.set("Saved %s%s" % (os.path.basename(f), "" if drawn else "  (no triangle uses this texture)"))

            def do_probe():
                r = picked()
                if not r:
                    return
                try:
                    win.config(cursor="watch")
                    win.update_idletasks()
                    im = wpk_view.texture_probe(model(), r["index"])
                except (wpk_view.WpkError, OSError) as ex:
                    messagebox.showerror(APP, str(ex))
                    return
                finally:
                    win.config(cursor="")
                pw = tk.Toplevel(win)
                pw.title("Where '%s' shows on the car" % r["name"])
                ph = ImageTk.PhotoImage(im)
                lab = ttk.Label(pw, image=ph)
                lab.image = ph
                lab.pack()
                ttk.Label(pw, wraplength=im.width - 20, justify="left", padding=8,
                          text="Bottom row: every spot where this texture shows on the car, in pink. Top row: the car with a "
                               "position pattern in its place - a pane the pattern varies over (red across, green up, blue "
                               "checks) can carry a decal; a pane of one flat colour maps to a single spot of the texture "
                               "and nothing can be placed there.").pack()
                f = filedialog.asksaveasfilename(title="Also save this picture? (cancel to just look)",
                                                 defaultextension=".png", initialfile=save_name(r, "_probe"),
                                                 filetypes=[("PNG", "*.png")])
                if f:
                    im.save(f)

            def do_import():
                r = picked(True)
                if not r:
                    return
                f = filedialog.askopenfilename(title="The edited picture for '%s'" % r["name"],
                                               filetypes=[("Pictures", "*.png *.tga *.bmp *.jpg *.jpeg"), ("All", "*")])
                if not f:
                    return
                try:
                    im = Image.open(f)
                    im.load()
                except (OSError, ValueError) as ex:
                    messagebox.showerror(APP, "Cannot open the picture: %s" % ex)
                    return
                src_dir = os.path.dirname(os.path.abspath(st["path"]))
                out = filedialog.askdirectory(title="Folder for the edited car file(s): the car's own folder replaces "
                                                    "them (old ones kept as .bak), another keeps the originals",
                                              initialdir=src_dir)
                if not out:
                    return
                if os.path.abspath(out) == src_dir and not messagebox.askyesno(
                        APP, "This replaces the car's files in\n%s\n(each kept once as .bak).\n\nContinue?" % src_dir):
                    return
                win.config(cursor="watch")
                win.update_idletasks()
                try:
                    res = wpk_tex.import_to_car(st["path"], r["name"], im, out, None, dither.get())
                except (wpk_tex.TexError, OSError) as ex:
                    messagebox.showerror(APP, "Not written: %s" % ex)
                    return
                finally:
                    win.config(cursor="")
                st["path"] = os.path.join(os.path.abspath(out), os.path.basename(st["path"]))
                self.cars_dir = os.path.abspath(out)
                load()
                if self.preview and self.preview.alive() and self.pack and self.pack.path:
                    np_ = wpk_view.find_wpk(self.pack.path, [self.cars_dir])
                    if np_:
                        self.preview.set_car(np_)
                lines = ["  %s: %s" % (n, w) for n, w in res["files"]] + ["  note: " + n for n in res["notes"]]
                self.status.set("Texture '%s' replaced in %s" % (r["name"], out))
                m = self.models                                   # the Models tab keeps its own copy of a car in memory:
                if m is not None and getattr(m, "path", None) and os.path.abspath(m.path) in {
                        os.path.abspath(os.path.join(out, n)) for n, w in res["files"] if w == "replaced"}:
                    if m.dirty:                                   # saving it later would put the old texture back
                        messagebox.showwarning(APP, "The Models tab has unsaved changes to this car. Saving them would undo "
                                               "the texture you just put in.\n\nSave or discard them there, then open "
                                               "the car again.")
                    else:
                        m.open_path(m.path)
                messagebox.showinfo(APP, "Done: '%s' is now in the car pack.\n\n%s\n\nCopy the file(s) into the game's "
                                    "data/cars folder.%s" % (r["name"], "\n".join(lines),
                                                             "\nThe old files are kept as .bak." if res["bak"] else ""))

            for t, cmd in (("Export PNG…", do_export), ("UV guide PNG…", do_guide), ("Where does it show?", do_probe),
                           ("Import edited PNG…", do_import), ("Close", win.destroy)):
                ttk.Button(btns, text=t, command=cmd).pack(side="left", padx=(0, 8))
            load()

        def show_help(self):
            win = tk.Toplevel(self.root)
            win.title("How it works")
            win.transient(self.root)
            t = tk.Text(win, width=78, height=24, wrap="word", padx=12, pady=10, borderwidth=0)
            t.insert("1.0", HELP)
            t.configure(state="disabled")
            t.pack()
            ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))

        # ------------------------------------------------------------ chrome
        def update_steps(self):
            if not self.pack:
                cur = 0
            elif self.sel is None:
                cur = 1
            elif not (self.pack.changed_numbers() or self.pack.head_changed()):
                cur = 2
            else:
                cur = 3
            for i, lab in enumerate(self.steps):
                lab.configure(font=self.bold if i == cur else "TkDefaultFont",
                              foreground=ACCENT if i == cur else ("#222" if i < cur else GREY))

        def refresh_all(self):
            self.draw_grid()
            self.show_detail()
            self.update_steps()
            p = self.pack
            dirty = bool(p and (p.changed_numbers() or p.head_changed()))
            self.btn_save.configure(state="normal" if dirty and p.path else "disabled")
            self.btn_saveas.configure(state="normal" if p else "disabled")
            if p:
                n = len(p.changed_numbers())
                name = os.path.basename(p.path) if p.path else "new file"
                self.status.set("%s  ·  %d sticker%s%s" % (name, len(self.by_num), "" if len(self.by_num) == 1 else "s",
                                ("  ·  %d changed, not saved" % n) if dirty and n else ("  ·  settings changed, not saved" if dirty else "")))
                self.root.title("%s — %s%s" % (APP, name, " *" if dirty else ""))
            else:
                self.status.set("Nothing open")
                self.root.title(APP + "  " + VERSION)

    root = tk.Tk()
    app = App(root)
    if start:
        root.after(50, lambda: app.open_path(start))
    if _hook:
        _hook(root, app)
        return
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".vpk") and len(sys.argv) == 2:
        run_gui(start=sys.argv[1])
    elif len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    else:
        run_gui()
