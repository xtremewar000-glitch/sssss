"""WPK model editor for the VPK tool: look inside a Re3DX+ car pack (.wpk), export parts as OBJ or FBX, and replace parts
(body, bumpers, wings ...) with your own model.  The 2nd tab of the window ("Models (WPK)") is built on this file;
everything here also works without a window:

  python wpk_edit.py info    CAR.wpk
  python wpk_edit.py export  CAR.wpk OUT.obj|OUT.fbx [--tags 0,290] [--scale 0.1]
  python wpk_edit.py replace CAR.wpk TAG MODEL.obj|MODEL.fbx [OUT.wpk] [--scale 10] [--rot 0|90|180|270] [--flip-faces]
                             [--no-flip-v] [--shadow] [--texture NAME=FILE.png ...]
  python wpk_edit.py addpart CAR.wpk NAME [--from TAG] [--model MODEL.obj|MODEL.fbx [--scale 10] [--rot 0] [--texture NAME=FILE.png]] [OUT.wpk]
                             (new part, e.g. wing_A: a copy of part TAG and/or your own model loaded into it straight away)
  python wpk_edit.py kitcopy CAR.wpk TAGS [--to base,A,B,C]   (copy parts, e.g. 0x122,0x126, into the car's other kit files)
  python wpk_edit.py movepart CAR.wpk FROM_TAG TO (0x127 | wing_B) [OUT.wpk]   (renumber a part into a slot the engine draws)
  python wpk_edit.py windows CAR.wpk [OUT.wpk] [--dark 0-100] [--tone 0-100] [--inner 40-100] [--clear]       (window panes that came in as black body paint -> see-through glass)
  python wpk_edit.py headlights CAR.wpk [OUT.wpk]     (clear lens cover, bulbs also in the garage view)
  python wpk_edit.py selftest DIR_WITH_WPKS

Car space: 10 units = 1 metre, +Z = front of the car, +Y = up, hub height y = 0 (same as the stock packs).
"""
import os
import re
import shutil
import copy
import struct
import sys
import time

import numpy as np
from PIL import Image

VERTEX_LIT = np.dtype([("pos", "<f4", 3), ("uv", "<f4", 2), ("rgba", "u1", 4),
                       ("normal", "<i2", 3), ("pad", "<i2")])
WPK_SHADOW = 0xFFFF
NO_TEX = 0xFFFF
POOL = 65535                     # most vertices in one batch (the game's indices are 16 bit)


import vpk_ui as UI                       # shared confirmations / look (Milestone 6)


class EditError(Exception):
    pass


# ------------------------------------------------------------------------------------------------
# names of the part groups (chunk tags)
# ------------------------------------------------------------------------------------------------
def tag_name(tag):
    if tag == 0:
        return "Body"
    if 0x100 <= tag <= 0x103:
        return "Wheel " + ("front", "rear")[(tag - 0x100) // 2] + (" left", " right")[tag & 1]
    if 0x110 <= tag <= 0x114:
        return "Lamps 0x%x" % tag
    if 0x120 <= tag <= 0x121:
        return "Mirrors " + "AB"[tag - 0x120]
    if 0x122 <= tag <= 0x125:
        return "Wing " + "ABCD"[tag - 0x122]
    if 0x126 <= tag <= 0x129:
        return "Bonnet " + "ABCD"[tag - 0x126]
    if 0x12A <= tag <= 0x12D:
        return "Wheels B tyre %d" % (tag - 0x12A + 1)
    if tag == WPK_SHADOW:
        return "Shadow volume"
    for lo, hi, nm in ((0x130, 0x131, "Exhaust"), (0x132, 0x132, "Roll cage"), (0x180, 0x188, "Bonnet"), (0x190, 0x19E, "Wing"),
                       (0x1A0, 0x1A2, "Mirrors"), (0x1B0, 0x1B1, "Boot"), (0x1C0, 0x1C8, "Roll cage"), (0x201, 0x246, "Roof item")):
        if lo <= tag <= hi:                                   # the newer tag scheme (named by tag: the preview's A, B ... depend on the pack)
            return "%s 0x%x" % (nm, tag)
    return "Part 0x%x" % tag


# ------------------------------------------------------------------------------------------------
# adding a part: a name like bonnet_B stands for a free slot (chunk tag) of the car
# ------------------------------------------------------------------------------------------------
# The .wpk stores NO part names, only a number (tag) per part; every name in the tool is worked out from that number.
# So "naming" a new part means choosing its tag.  Same groups and order as the preview (wpk_view.PART_GROUPS): the
# older tags first, then the newer ones; a group's letters A, B, C ... count the variants the pack really has.
def _rng(lo, hi):
    return list(range(lo, hi + 1))


PART_SLOTS = (("Bonnet", ("bonnet", "hood"), _rng(0x126, 0x129) + _rng(0x180, 0x188)),
              ("Wing", ("rearspoiler", "spoiler", "wing"), _rng(0x122, 0x125) + _rng(0x190, 0x19E)),
              ("Mirrors", ("mirrors", "mirror"), _rng(0x120, 0x121) + _rng(0x1A0, 0x1A2)),
              ("Boot", ("boot", "trunk"), _rng(0x1B0, 0x1B1)),
              ("Exhaust", ("exhaust",), _rng(0x130, 0x131)),
              ("Roll cage", ("rollcage", "cage"), [0x132] + _rng(0x1C0, 0x1C8)),
              ("Roof item", ("roofitem", "roof"), _rng(0x201, 0x246)))
NAME_HINT = "a name like bonnet_B, wing_C, mirror_B, boot_A, exhaust_B, rollcage_A, roofitem_3 - or a tag number like 0x127"


def variant_letter(i):
    """A .. Z, then 27, 28 ... (the preview's labels)"""
    return chr(65 + i) if i < 26 else str(i + 1)


def parse_part_name(text):
    """'bonnet_B' / 'Wing C' / 'mirror_2' -> ('variant', group index, variant index);  '0x127' / '295' -> ('tag', 295);
    anything else -> None"""
    s = text.strip().lower()
    if not s:
        return None
    try:
        return ("tag", int(s, 0))
    except ValueError:
        pass
    for gi, (_n, words, _t) in enumerate(PART_SLOTS):
        for w in words:
            m = re.match(r"^%s(?:[\s_\-]+([a-z]|\d{1,2})|(\d{1,2}))$" % re.escape(w), s)
            if m:
                v = m.group(1) or m.group(2)
                idx = ord(v) - 97 if v.isalpha() else int(v) - 1
                return ("variant", gi, idx) if idx >= 0 else None
    return None


def scheme_slots(pack, gi):
    """the tags the letters A, B, C ... of group gi stand for on THIS car.  The older tags (below 0x130) have fixed letters
    (0x126 = A ... 0x129 = D, the same as the part list shows); a car that only has the newer tags of the group counts
    from the first newer one; a mix counts along the whole list.  Letters do not shift when a slot is empty."""
    _n, _w, tags = PART_SLOTS[gi]
    old, new = [t for t in tags if t < 0x130], [t for t in tags if t >= 0x130]
    has_old = any(pack.chunk(t) is not None for t in old)
    has_new = any(pack.chunk(t) is not None for t in new)
    if old and engine_draws(old[0]):                       # the Re3DX+ engine draws only the older numbers (0x122-0x129): new parts go there
        return old
    if has_old and has_new:
        return tags
    if not has_old and not has_new and old and new:
        # the car has nothing of this group yet (a newly imported car: no wing, no mirrors ...): follow the scheme the
        # rest of the car's parts use, so a new wing is 0x190 on a car whose bonnet is 0x180, not 0x122
        o = n = 0
        for _n2, _w2, t2 in PART_SLOTS:
            for t in t2:
                if pack.chunk(t) is not None:
                    if t < 0x130:
                        o += 1
                    else:
                        n += 1
        if n > o:
            return new
        if o > n:
            return old
    return new if (has_new or not old) else old


def _taken(pack, tag, what):
    """why a slot cannot be added: used, or already in the car but empty (every car converted from an FBX keeps its donor's
    whole part table with the geometry cleared, so its free slots are usually there already)"""
    if not pack.chunk(tag).batches:
        return "%s (tag 0x%x) is already in the car, but empty: select it in the list and use Replace with OBJ…" % (what, tag)
    return "This car already has %s (tag 0x%x)." % (what, tag)


def slot_for_variant(pack, gi, idx):
    """the tag of variant `idx` (0 = A) of group gi on this car; EditError when it is taken or does not exist"""
    name, _w, tags = PART_SLOTS[gi]
    slots = scheme_slots(pack, gi)
    if idx >= len(slots):
        other = [t for t in tags if t not in slots]
        raise EditError("%s has only %d slots (%s - %s) in the tag scheme this car uses.%s" % (
            name, len(slots), variant_letter(0), variant_letter(len(slots) - 1),
            (" To use the other scheme type a tag number instead, e.g. 0x%x (the car's own scheme is kept otherwise)." % other[0]) if other else ""))
    tag = slots[idx]
    if pack.chunk(tag) is not None:
        raise EditError(_taken(pack, tag, "%s %s" % (name, variant_letter(idx))))
    return tag


def suggest_name(pack, tag):
    """(name, note) for a new part of the same kind as the part `tag`: the name is its group's first free letter (Wing B
    selected on a car without Wing A -> wing_A); it is '' when the part is in no group or the group is full, and then
    the note says why"""
    for gi, (name, _w, tags) in enumerate(PART_SLOTS):
        if tag in tags:
            for i, t in enumerate(scheme_slots(pack, gi)):
                if pack.chunk(t) is None:
                    return "%s_%s" % (name.lower().replace(" ", ""), variant_letter(i)), ""
            slots = scheme_slots(pack, gi)
            empty = [variant_letter(i) for i, t in enumerate(slots) if not pack.chunk(t).batches]
            if empty:
                return "", "%s %s %s in the car but empty: select %s in the list and use Replace with OBJ…" % (
                    name, ", ".join(empty), "is" if len(empty) == 1 else "are", "it" if len(empty) == 1 else "one")
            other = [t for t in tags if t not in slots]
            return "", "All %d %s slots (%s - %s) are used on this car.%s" % (
                len(slots), name, variant_letter(0), variant_letter(len(slots) - 1),
                (" Type a tag number to use the other scheme, e.g. 0x%x." % other[0]) if other else "")
    return "", ""


def resolve_part_name(pack, text):
    """what the person typed -> (tag, what the list will call it); EditError says what is wrong"""
    p = parse_part_name(text)
    if p is None:
        raise EditError("Type " + NAME_HINT + ".")
    if p[0] == "variant":
        tag = slot_for_variant(pack, p[1], p[2])
        return tag, "%s %s" % (PART_SLOTS[p[1]][0], variant_letter(p[2]))
    tag = p[1]
    if tag == 0 or not 0 < tag < WPK_SHADOW:
        raise EditError("Tag %d cannot be a new part (0 is the body, 0xFFFF the shadow)." % tag)
    if pack.chunk(tag) is not None:
        raise EditError(_taken(pack, tag, tag_name(tag)))
    return tag, tag_name(tag)


def copy_parts(src, dst, tags, to_tags=None):
    """copy the parts `tags` of pack `src` into pack `dst` (same numbers, or `to_tags` one by one), with the textures they use
    and those textures' paint copies (p1: .. p5:).  A part dst already has is replaced.  -> [notes]"""
    notes, cache = [], {}

    def slot(i):
        if i is None or i >= len(src.tex):
            return i
        if i in cache:
            return cache[i]
        e = src.tex[i]
        j = next((k for k, t in enumerate(dst.tex) if t["name"] == e["name"]), None)
        if j is None:
            if not e.get("raw"):
                raise EditError("The texture %s is empty in the source car." % e["name"])
            dst.tex.append(dict(e))
            j = len(dst.tex) - 1
            notes.append("texture %s added" % e["name"])
        cache[i] = j
        return j

    for n, tag in enumerate(tags):
        sc = src.chunk(tag)
        if sc is None:
            raise EditError("There is no part 0x%x to copy." % tag)
        to = to_tags[n] if to_tags else tag
        bs = []
        for b in sc.batches:
            nb = b.copy()
            nb.tex = slot(b.tex) if b.tex != NO_TEX else NO_TEX
            nb.env = slot(b.env) if b.env != NO_TEX else NO_TEX
            nb.spec = slot(b.spec) if b.spec != NO_TEX else NO_TEX
            bs.append(nb)
            if b.tex != NO_TEX and b.tex < len(src.tex):                # the in-game paint copies of the texture
                for k in range(1, 6):
                    pn = "p%d:%s" % (k, src.tex[b.tex]["name"])
                    pi = next((q for q, t in enumerate(src.tex) if t["name"] == pn), None)
                    if pi is not None:
                        slot(pi)
        dst.set_chunk(to, bs)
        notes.append("part 0x%x -> 0x%x (%d batches)" % (tag, to, len(bs)))
    dst.check()
    return notes


def kit_files(path):
    """the car's other packs beside this one: [(label, path)]  (label: Normal, Aero A, Aero B, Aero C)"""
    d, base = os.path.dirname(os.path.abspath(path)), os.path.basename(path)
    m = re.match(r"(.+?)(?:_aero[ABC])?\.wpk$", base, re.I)
    if not m:
        return []
    out = []
    for suf, lab in (("", "Normal"), ("_aeroA", "Aero A"), ("_aeroB", "Aero B"), ("_aeroC", "Aero C")):
        p = os.path.join(d, m.group(1) + suf + ".wpk")
        if os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(path):
            out.append((lab, p))
    return out


def drawn_choices(pack, tag):
    """the part numbers the engine draws that a part `tag` can move to: [(tag, label)] - its own group's slots (a wing: 0x122-0x125),
    or every dress-up slot (0x120-0x12d) for a part that is in no group"""
    for _n, _w, tags in PART_SLOTS:
        if tag in tags:
            return [(t, tag_name(t)) for t in tags if engine_draws(t)]
    return [(t, tag_name(t)) for t in range(0x120, 0x12E)]


def dest_tag(pack, text):
    """'0x127', '295', 'bonnet_B' -> a destination tag number (taken or not: move_part decides)"""
    p = parse_part_name(text.strip().split()[0] if text.strip().startswith(("0x", "0X")) else text)
    if p is None:
        raise EditError("Type " + NAME_HINT + ".")
    if p[0] == "tag":
        return p[1]
    slots = scheme_slots(pack, p[1])
    if p[2] >= len(slots):
        raise EditError("%s has only %d slots." % (PART_SLOTS[p[1]][0], len(slots)))
    return slots[p[2]]


def move_part(pack, tag, new_tag):
    """give the part `tag` another part number (its geometry and textures stay as they are).  An empty part already sitting
    on the new number is replaced; a part with geometry there is an error.  -> the moved Chunk"""
    c = pack.chunk(tag)
    if c is None:
        raise EditError("There is no part with tag 0x%x." % tag)
    if new_tag == tag:
        raise EditError("The part already has that number.")
    if new_tag == 0 or not 0 < new_tag < WPK_SHADOW or tag == 0 or tag >= WPK_SHADOW:
        raise EditError("The body (0) and the shadow (0xFFFF) cannot be moved, and nothing can be moved onto them.")
    other = pack.chunk(new_tag)
    if other is not None and other.batches:
        raise EditError("0x%x (%s) already has geometry: delete it first, or choose a free number." % (new_tag, tag_name(new_tag)))
    pack.delete_chunk(new_tag)
    pack.chunks = [x for x in pack.chunks if x is not c]
    c.tag = new_tag
    at = next((i for i, x in enumerate(pack.chunks) if x.tag > new_tag), len(pack.chunks))
    pack.chunks.insert(at, c)
    return c


def add_part(pack, tag, source=None):
    """a new part group with this tag: a copy of the part `source` (a tag), or empty when source is None.
    An empty part must get a model (Replace with OBJ) before the file is saved."""
    if tag == 0 or not 0 < tag < WPK_SHADOW:
        raise EditError("Tag %d cannot be a new part." % tag)
    if pack.chunk(tag) is not None:
        raise EditError("This car already has a part with tag 0x%x." % tag)
    batches = []
    if source is not None:
        src = pack.chunk(source)
        if src is None:
            raise EditError("There is no part with tag 0x%x to copy." % source)
        batches = [b.copy() for b in src.batches]
    return pack.set_chunk(tag, batches)


# ------------------------------------------------------------------------------------------------
# texture codecs (16 bit, like the packs)
# ------------------------------------------------------------------------------------------------
def decode16(w, h, fmt, raw):
    px = np.frombuffer(raw, "<u2", count=w * h).reshape(h, w)
    if fmt == 0:
        r, g, b, a = (px >> 11) << 3, ((px >> 5) & 63) << 2, (px & 31) << 3, np.full(px.shape, 255)
    elif fmt == 2:
        r, g, b, a = (px >> 11) << 3, ((px >> 6) & 31) << 3, ((px >> 1) & 31) << 3, (px & 1) * 255
    else:
        r, g, b, a = (px >> 12) << 4, ((px >> 8) & 15) << 4, ((px >> 4) & 15) << 4, (px & 15) << 4
    return np.stack([r, g, b, a], -1).astype(np.uint8)


def _pot(n, cap):
    p = 1
    while p * 2 <= max(n, 1) and p * 2 <= cap:
        p *= 2
    if p * 2 <= cap and n - p > p * 2 - n:
        p *= 2
    return p


def encode16(im, max_size=512):
    """PIL image -> (w, h, fmt, bytes); size = nearest power of two, at most max_size.
    fmt 0 RGB565 (opaque), 2 RGBA5551 (cut-out), 1 RGBA4444 (soft alpha)"""
    im = im.convert("RGBA")
    w, h = _pot(im.size[0], max_size), _pot(im.size[1], max_size)
    if (w, h) != im.size:
        im = im.resize((w, h), Image.LANCZOS)
    a = np.asarray(im, dtype=np.uint16)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    if int(al.min()) == 255:
        px, fmt = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3), 0
    elif np.all((al < 8) | (al > 247)):
        px, fmt = ((r >> 3) << 11) | ((g >> 3) << 6) | ((b >> 3) << 1) | (al >> 7), 2
    else:
        px, fmt = ((r >> 4) << 12) | ((g >> 4) << 8) | ((b >> 4) << 4) | (al >> 4), 1
    return w, h, fmt, px.astype("<u2").tobytes()


def _stem(n):
    return os.path.splitext(n)[0].lower()


# ------------------------------------------------------------------------------------------------
# the pack in memory
# ------------------------------------------------------------------------------------------------
class Batch:
    """one draw: vertices V (VERTEX_LIT), flat u16 triangle list I, and its material"""
    __slots__ = ("tex", "flags", "prog", "pad", "env", "spec", "power", "V", "I")

    def __init__(self, tex, flags, prog, pad, env, spec, power, V, I):
        self.tex, self.flags, self.prog, self.pad, self.env, self.spec, self.power = tex, flags, prog, pad, env, spec, power
        self.V, self.I = V, I

    def copy(self):
        return Batch(self.tex, self.flags, self.prog, self.pad, self.env, self.spec, self.power, self.V.copy(), self.I.copy())

    def material(self):
        return (self.tex, self.flags, self.prog, self.env, self.spec, self.power)


class Chunk:
    __slots__ = ("tag", "lo", "hi", "batches")

    def __init__(self, tag, lo, hi, batches):
        self.tag, self.lo, self.hi, self.batches = tag, lo, hi, batches

    def update_bbox(self):
        pts = [b.V["pos"] for b in self.batches if len(b.V)]
        if pts:
            p = np.concatenate(pts)
            self.lo, self.hi = tuple(float(x) for x in p.min(0)), tuple(float(x) for x in p.max(0))

    def counts(self):
        return (len(self.batches), sum(len(b.V) for b in self.batches), sum(len(b.I) // 3 for b in self.batches))


class Pack:
    """a WPK3 car pack. Textures an aero pack borrows from its stock pack (size 0) stay borrowed."""

    def __init__(self):
        self.path = None
        self.chunk_size = 0.0
        self.tex = []                 # dicts: name, w, h, fmt, raw (b"" = borrowed from the stock pack)
        self.chunks = []

    # ---- reading
    @classmethod
    def load(cls, path):
        try:
            with open(path, "rb") as fh:
                b = fh.read()
            magic, nt, nc, nb, cs, to, co, bo = struct.unpack_from("<4sIIIfIII", b, 0)
        except (OSError, struct.error) as ex:
            raise EditError("Cannot read %s: %s" % (os.path.basename(path), ex))
        if magic != b"WPK3":
            raise EditError("%s is not a WPK3 car pack." % os.path.basename(path))
        p = cls()
        p.path, p.chunk_size = path, cs
        try:
            for i in range(nt):
                nm, w, h, fmt, off, sz = struct.unpack_from("<48sHHIII", b, to + 64 * i)
                if off + sz > len(b):
                    raise EditError("texture %d runs past the end of the file" % i)
                p.tex.append(dict(name=nm.split(b"\0")[0].decode("latin1"), w=w, h=h, fmt=fmt, raw=bytes(b[off:off + sz])))
            batches = []
            for i in range(nb):
                t, fl, vo, vn, io, inn = struct.unpack_from("<HHIIII", b, bo + 28 * i)
                prog, pad, env, spec, pw = struct.unpack_from("<BBHHH", b, bo + 28 * i + 20)
                V = np.frombuffer(b, VERTEX_LIT, count=vn, offset=vo).copy()
                I = np.frombuffer(b, "<u2", count=inn, offset=io).copy()
                batches.append(Batch(t, fl, prog, pad, env, spec, pw, V, I))
            used = [False] * nb
            for i in range(nc):
                v = struct.unpack_from("<6fIII", b, co + 36 * i)
                first, n, tag = v[6], v[7], v[8]
                if first + n > nb:
                    raise EditError("chunk %d points outside the batch table" % i)
                bs = []
                for k in range(first, first + n):
                    bs.append(batches[k].copy() if used[k] else batches[k])
                    used[k] = True
                p.chunks.append(Chunk(tag, tuple(v[0:3]), tuple(v[3:6]), bs))
        except struct.error as ex:
            raise EditError("%s is damaged: %s" % (os.path.basename(path), ex))
        return p

    # ---- writing (same layout as Re3DX+'s tools/stage/pack.py)
    def to_bytes(self):
        batches = [b for c in self.chunks for b in c.batches]
        tex_off = 32
        chunk_off = tex_off + 64 * len(self.tex)
        batch_off = chunk_off + 36 * len(self.chunks)
        data_off = batch_off + 28 * len(batches)
        blob = bytearray()

        def put(data):
            while (data_off + len(blob)) % 4:
                blob.append(0)
            off = data_off + len(blob)
            blob.extend(data)
            return off

        tex_tbl = bytearray()
        for t in self.tex:
            tex_tbl += struct.pack("<48sHHIII", t["name"].encode("latin1")[:47], t["w"], t["h"], t["fmt"],
                                   put(t["raw"]) if t["raw"] else 0, len(t["raw"]))
        batch_tbl = bytearray()
        for b in batches:
            voff = put(b.V.tobytes())
            ioff = put(b.I.tobytes())
            batch_tbl += struct.pack("<HHIIII", b.tex, b.flags, voff, len(b.V), ioff, len(b.I))
            batch_tbl += struct.pack("<BBHHH", b.prog, b.pad, b.env, b.spec, b.power)
        chunk_tbl = bytearray()
        first = 0
        for c in self.chunks:
            chunk_tbl += struct.pack("<6fIII", *c.lo, *c.hi, first, len(c.batches), c.tag)
            first += len(c.batches)
        head = struct.pack("<4sIIIfIII", b"WPK3", len(self.tex), len(self.chunks), len(batches), self.chunk_size,
                           tex_off, chunk_off, batch_off)
        return bytes(head + tex_tbl + chunk_tbl + batch_tbl + blob)

    def save(self, path, backup=True):
        """write the pack; an existing file is kept once as <file>.bak"""
        self.check()
        data = self.to_bytes()
        bak = False
        if backup and os.path.isfile(path) and not os.path.exists(path + ".bak"):
            shutil.copy2(path, path + ".bak")
            bak = True
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        self.path = path
        return dict(bytes=len(data), bak=bak)

    # ---- looking
    def chunk(self, tag):
        return next((c for c in self.chunks if c.tag == tag), None)

    def tex_index(self, name):
        """texture slot of a name (exact, then ignoring the extension and case); None if it is not there"""
        for i, t in enumerate(self.tex):
            if t["name"] == name:
                return i
        s = _stem(name)
        return next((i for i, t in enumerate(self.tex) if _stem(t["name"]) == s), None)

    def decode_tex(self, i, stock=None):
        """RGBA uint8 of texture i; a borrowed one is taken from `stock` (a Pack) by name"""
        t = self.tex[i]
        if t["raw"]:
            return decode16(t["w"], t["h"], t["fmt"], t["raw"])
        j = stock.tex_index(t["name"]) if stock else None
        if j is not None and stock.tex[j]["raw"]:
            return decode16(stock.tex[j]["w"], stock.tex[j]["h"], stock.tex[j]["fmt"], stock.tex[j]["raw"])
        return np.full((max(t["h"], 1), max(t["w"], 1), 4), 128, np.uint8)

    def summary(self):
        return [(c.tag, tag_name(c.tag)) + c.counts() + (sorted({b.tex for b in c.batches if b.tex != NO_TEX}),) for c in self.chunks]

    def check(self):
        """raise EditError for anything the game would reject"""
        for c in self.chunks:
            for b in c.batches:
                if len(b.V) > POOL:
                    raise EditError("Part 0x%x has a batch with %d vertices (the game takes at most %d)." % (c.tag, len(b.V), POOL))
                if len(b.I) % 3:
                    raise EditError("Part 0x%x has a broken triangle list." % c.tag)
                if len(b.I) and int(b.I.max()) >= len(b.V):
                    raise EditError("Part 0x%x has a triangle pointing at a missing vertex." % c.tag)
                if b.tex != NO_TEX and b.tex >= len(self.tex):
                    raise EditError("Part 0x%x uses a texture slot that does not exist." % c.tag)
                if len(b.V) and not np.all(np.isfinite(b.V["pos"])):
                    raise EditError("Part 0x%x has a vertex that is not a number." % c.tag)

    # ---- editing
    def add_texture(self, name, image, max_size=512):
        """add (or replace, same name) a texture from a PIL image; -> slot"""
        w, h, fmt, raw = encode16(image, max_size)
        name = name[:47]
        i = self.tex_index(name)
        entry = dict(name=name, w=w, h=h, fmt=fmt, raw=raw)
        if i is not None and self.tex[i]["name"] == name:
            self.tex[i] = entry
            return i
        self.tex.append(entry)
        return len(self.tex) - 1

    def texture_users(self, i):
        """how many batches use texture i: {'tex': picture, 'env': reflection map, 'spec': specular map} -> counts"""
        n = {"tex": 0, "env": 0, "spec": 0}
        for c in self.chunks:
            for b in c.batches:
                for k in n:
                    if getattr(b, k) == i:
                        n[k] += 1
        return n

    def delete_textures(self, idxs):
        """remove the texture slots `idxs` and renumber what points at the others.  A batch that drew with a deleted picture
        keeps its shape and becomes untextured (flat colour); one that used a deleted reflection / specular map loses it.
        -> (textures removed, batches that lost their picture)"""
        gone = {i for i in idxs if 0 <= i < len(self.tex)}
        if not gone:
            return 0, 0
        remap, k = {}, 0
        for i in range(len(self.tex)):
            if i not in gone:
                remap[i] = k
                k += 1
        lost = 0
        for c in self.chunks:
            for b in c.batches:
                if b.tex in gone:
                    lost += 1
                for f in ("tex", "env", "spec"):
                    v = getattr(b, f)
                    if v != NO_TEX:
                        setattr(b, f, remap.get(v, NO_TEX))
        self.tex = [t for i, t in enumerate(self.tex) if i not in gone]
        return len(gone), lost

    def delete_chunk(self, tag):
        self.chunks = [c for c in self.chunks if c.tag != tag]

    def set_chunk(self, tag, batches):
        """replace what a part group holds (or add the group): keeps the table sorted by tag"""
        c = self.chunk(tag)
        if c is None:
            c = Chunk(tag, (0, 0, 0), (0, 0, 0), [])
            at = next((i for i, x in enumerate(self.chunks) if x.tag > tag), len(self.chunks))
            self.chunks.insert(at, c)
        c.batches = batches
        c.update_bbox()
        return c

    def find_tex(self, stem_part):
        return next((i for i, t in enumerate(self.tex) if stem_part in t["name"].lower()), None)

    # ---- the car shader presets
    def preset(self, name):
        """(flags, prog, env, spec, power) of a kind of material, with the pack's own env/spec maps"""
        env = self.find_tex("alfa")
        env = NO_TEX if env is None else env
        spec = next((i for i, t in enumerate(self.tex) if "spec" in t["name"].lower() and "wheel" not in t["name"].lower()), None)
        spec = NO_TEX if spec is None else spec
        return {"paint": (0, 1, env, NO_TEX, 8000), "flat": (0, 0, NO_TEX, NO_TEX, 0),
                "glass": (1, 2, env, spec, 0), "add": (2, 0, NO_TEX, NO_TEX, 0),
                "metal": (0, 2, env, spec, 0)}[name]


PRESETS = ("paint", "flat", "glass", "add", "metal")

# the Replace dialog's "Look" choices (the game's car shaders): paint = car.cg (sphere-map reflection, a specular
# highlight and, with the game's Reflections option, the street's reflection); metal / glass = car_blend.cg
LOOK_NAMES = ("(automatic)", "Glossy paint", "Metal / chrome", "Glass", "Flat (no shine)")
LOOK_KINDS = (None, "paint", "metal", "glass", "flat")


def reflection_indices(pack):
    """(env, spec) texture indices of the maps the car shaders use: 'alfa' (the sky / street sphere map) and the specular map"""
    env = pack.find_tex("alfa")
    spec = next((i for i, t in enumerate(pack.tex) if "spec" in t["name"].lower() and "wheel" not in t["name"].lower()), None)
    return env, spec


def add_reflection_maps(pack, donor):
    """copy the reflection (alfa) and specular maps a stock car pack has into `pack`, when it lacks them -> how many textures were added"""
    e, sp = reflection_indices(donor)
    if e is None:
        raise EditError("There is no 'alfa' reflection map in that file. Pick a stock car's .wpk.")
    have = {t["name"] for t in pack.tex}
    n = 0
    for i in (e, sp):
        if i is not None and donor.tex[i]["name"] not in have:
            pack.tex.append(copy.deepcopy(donor.tex[i]))
            n += 1
    return n


def preset_of(b):
    """name of the material kind a batch is (used to name the OBJ materials)"""
    if b.flags & 3 == 2:
        return "add"
    if b.prog == 1:
        return "paint"
    if b.prog == 2:
        return "glass" if b.flags & 3 == 1 else "metal"
    return "flat"


# ------------------------------------------------------------------------------------------------
# OBJ export
# ------------------------------------------------------------------------------------------------
def _safe(n):
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", n)


def mat_name(pack, b):
    """<texture>__<kind>[<reflection %>], e.g. fc3s_body01_white__paint80"""
    stem = _safe(_stem(pack.tex[b.tex]["name"])) if b.tex != NO_TEX and b.tex < len(pack.tex) else "none"
    kind = preset_of(b)
    return "%s__%s%s" % (stem, kind, str(int(round(b.power / 100.0))) if b.power and kind in ("paint", "glass", "metal") else "")


def export_obj(pack, path, tags=None, scale=0.1, stock=None, with_textures=True):
    """write the parts `tags` (None = all but the shadow) as OBJ + MTL (+ the textures as PNG beside it).
    Objects are named t<tag>_b<n> and materials <texture>__<kind>, which Replace reads back."""
    base = os.path.splitext(path)[0]
    name = os.path.basename(base)
    texdir = base + "_tex"
    lines = ["# %s  (units x %g, +Z front, +Y up)" % (os.path.basename(pack.path or "car.wpk"), scale), "mtllib %s.mtl" % name]
    mats = {}
    off = 0
    n_tri = 0
    for c in pack.chunks:
        if (tags is None and c.tag == WPK_SHADOW) or (tags is not None and c.tag not in tags):
            continue
        for k, b in enumerate(c.batches):
            if not len(b.V):
                continue
            m = mat_name(pack, b)
            mats[m] = b.tex
            lines.append("o t%d_b%d" % (c.tag, k))
            P = b.V["pos"].astype(np.float64) * scale
            col = b.V["rgba"][:, :3].astype(np.float64) / 255
            tinted = bool(np.any(b.V["rgba"][:, :3] != 255))
            for i in range(len(P)):
                lines.append("v %.5f %.5f %.5f %.3f %.3f %.3f" % (*P[i], *col[i]) if tinted else "v %.5f %.5f %.5f" % tuple(P[i]))
            for u, v in b.V["uv"]:
                lines.append("vt %.5f %.5f" % (u, 1.0 - v))
            for nx, ny, nz in b.V["normal"] / 32767.0:
                lines.append("vn %.4f %.4f %.4f" % (nx, ny, nz))
            lines.append("usemtl " + m)
            T = b.I.reshape(-1, 3).astype(np.int64) + 1 + off
            for a, bb, cc in T:
                lines.append("f %d/%d/%d %d/%d/%d %d/%d/%d" % (a, a, a, bb, bb, bb, cc, cc, cc))
            off += len(P)
            n_tri += len(T)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    mtl = []
    if with_textures:
        os.makedirs(texdir, exist_ok=True)
    for m, ti in mats.items():
        mtl += ["newmtl " + m, "Kd 1 1 1"]
        if with_textures and ti != NO_TEX and ti < len(pack.tex):
            fn = _safe(_stem(pack.tex[ti]["name"])) + ".png"
            fp = os.path.join(texdir, fn)
            if not os.path.exists(fp):
                Image.fromarray(pack.decode_tex(ti, stock), "RGBA").save(fp)
            mtl.append("map_Kd %s_tex/%s" % (name, fn))
        mtl.append("")
    with open(base + ".mtl", "w", encoding="utf-8") as fh:
        fh.write("\n".join(mtl))
    return dict(objects=len(mats), triangles=n_tri, files=[path, base + ".mtl"])


def export_fbx(pack, path, tags=None, scale=0.1, stock=None, with_textures=True):
    """same parts as export_obj, written as one binary FBX (metres, +Y up, +Z front) with the textures as PNG beside it."""
    import fbx_io
    base = os.path.splitext(path)[0]
    name = os.path.basename(base)
    texdir = base + "_tex"
    meshes, mats, n_tri = [], {}, 0
    for c in pack.chunks:
        if (tags is None and c.tag == WPK_SHADOW) or (tags is not None and c.tag not in tags):
            continue
        for k, b in enumerate(c.batches):
            if not len(b.V):
                continue
            m = mat_name(pack, b)
            mats[m] = b.tex
            nrm = b.V["normal"].astype(np.float64) / 32767.0
            meshes.append(dict(name="t%d_b%d" % (c.tag, k), material=m, pos=b.V["pos"].astype(np.float64) * scale,
                               nrm=nrm, uv=np.stack([b.V["uv"][:, 0], 1.0 - b.V["uv"][:, 1]], 1).astype(np.float64),
                               tris=b.I.reshape(-1, 3).astype(np.int64)))
            n_tri += len(meshes[-1]["tris"])
    texmap = {}
    if with_textures:
        os.makedirs(texdir, exist_ok=True)
        for m, ti in mats.items():
            if ti != NO_TEX and ti < len(pack.tex):
                fn = _safe(_stem(pack.tex[ti]["name"])) + ".png"
                fp = os.path.join(texdir, fn)
                if not os.path.exists(fp):
                    Image.fromarray(pack.decode_tex(ti, stock), "RGBA").save(fp)
                texmap[m] = "%s_tex/%s" % (name, fn)
    fbx_io.write_fbx(path, meshes, texmap, unit_cm=100.0 / (scale * 10.0) if scale else 100.0)
    return dict(objects=len(mats), triangles=n_tri, files=[path])


def export_model(pack, path, tags=None, scale=0.1, stock=None, with_textures=True):
    """OBJ or FBX, by the file extension"""
    if path.lower().endswith(".fbx"):
        return export_fbx(pack, path, tags, scale, stock, with_textures)
    return export_obj(pack, path, tags, scale, stock, with_textures)


# ------------------------------------------------------------------------------------------------
# OBJ import
# ------------------------------------------------------------------------------------------------
class Mesh:
    def __init__(self, name, material, tag, V, tris):
        self.name, self.material, self.tag, self.V, self.tris = name, material, tag, V, tris


def _face_index(tok, n):
    i = int(tok)
    return i - 1 if i > 0 else n + i


def parse_obj(path, scale=10.0, rot=0, flip_v=True, flip_faces=False, offset=(0.0, 0.0, 0.0), fbx_select=None):
    """-> [Mesh]: one per (object, material). Triangles are fanned from polygons; missing normals are made smooth.
    rot: turn the model about the up axis by 0/90/180/270 degrees (a model facing the other way)."""
    P, UV, NR, CL = [], [], [], []
    groups = {}
    cur, mat = "model", "none"
    if path.lower().endswith(".fbx"):
        try:
            import fbx_io
            d = fbx_io.read_fbx(path, select=fbx_select)
        except (fbx_io.FbxError, OSError, ValueError, KeyError, IndexError, struct.error) as ex:
            raise EditError("Cannot read %s: %s" % (os.path.basename(path), ex))
        P, UV, NR, CL, groups = d["P"], d["UV"], d["NR"], d["CL"], d["groups"]
    else:
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError as ex:
            raise EditError("Cannot read %s: %s" % (os.path.basename(path), ex))
        with fh:
            for ln in fh:
                if not ln or ln[0] == "#":
                    continue
                t = ln.split()
                if not t:
                    continue
                k = t[0]
                try:
                    if k == "v":
                        P.append([float(x) for x in t[1:4]])
                        CL.append([float(x) for x in t[4:7]] if len(t) >= 7 else None)
                    elif k == "vt":
                        UV.append([float(t[1]), float(t[2])])
                    elif k == "vn":
                        NR.append([float(x) for x in t[1:4]])
                    elif k in ("o", "g"):
                        cur = t[1] if len(t) > 1 else cur
                    elif k == "usemtl":
                        mat = re.sub(r"\.\d{3}$", "", t[1]) if len(t) > 1 else "none"
                    elif k == "f":
                        refs = []
                        for tok in t[1:]:
                            s = tok.split("/")
                            vi = _face_index(s[0], len(P))
                            ti = _face_index(s[1], len(UV)) if len(s) > 1 and s[1] else -1
                            ni = _face_index(s[2], len(NR)) if len(s) > 2 and s[2] else -1
                            refs.append((vi, ti, ni))
                        if len(refs) >= 3:
                            g = groups.setdefault((cur, mat), [])
                            for j in range(1, len(refs) - 1):
                                g.append((refs[0], refs[j], refs[j + 1]))
                except (ValueError, IndexError):
                    raise EditError("%s: cannot read the line  %s" % (os.path.basename(path), ln.strip()[:60]))
    if not P or not groups:
        raise EditError("%s has no triangles." % os.path.basename(path))
    P = np.array(P, np.float64)
    UVa = np.array(UV, np.float64) if UV else np.zeros((1, 2))
    NRa = np.array(NR, np.float64) if NR else None
    ang = np.radians(rot)
    ca, sa = round(np.cos(ang)), round(np.sin(ang))
    R = np.array([[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]], np.float64)
    meshes = []
    for (name, material), faces in groups.items():
        F = np.array(faces, np.int64).reshape(-1, 3)              # (tri, corner, v/t/n)
        flat = F.reshape(-1, 3)
        key, inv = np.unique(flat, axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        tris = inv.reshape(-1, 3)
        vi, ti, ni = key[:, 0], key[:, 1], key[:, 2]
        pos = (P[vi] @ R.T) * scale + np.array(offset)
        uv = UVa[np.where(ti >= 0, ti, 0)].copy()
        if not UV:
            uv[:] = 0
        elif flip_v:
            uv[:, 1] = 1.0 - uv[:, 1]
        if NRa is not None and np.all(ni >= 0):
            nrm = (NRa[ni] @ R.T)
        else:                                                      # smooth normals, welded by position
            tp = pos[tris]
            fn = np.cross(tp[:, 1] - tp[:, 0], tp[:, 2] - tp[:, 0])
            acc = np.zeros((len(P), 3))
            for c in range(3):
                np.add.at(acc, vi[tris[:, c]], fn)
            nrm = acc[vi]
        if flip_faces:
            tris = tris[:, [0, 2, 1]]
            nrm = -nrm
        ln = np.linalg.norm(nrm, axis=1)
        nrm = np.where(ln[:, None] > 1e-9, nrm / np.maximum(ln, 1e-9)[:, None], [0, 1, 0])
        V = np.zeros(len(pos), VERTEX_LIT)
        V["pos"], V["uv"] = pos, uv
        V["rgba"] = 255
        if any(c is not None for c in CL):
            col = np.array([c if c is not None else [1, 1, 1] for c in CL], np.float64)
            V["rgba"][:, :3] = np.clip(np.round(col[vi] * 255), 0, 255)
        V["normal"] = np.clip(np.round(nrm * 32767), -32767, 32767)
        m = re.match(r"^t(\d+)_", name)
        meshes.append(Mesh(name, material, int(m.group(1)) if m else None, V, tris.astype(np.int64)))
    return meshes


KIND_RE = re.compile(r"^(paint|flat|glass|add|metal)(\d{1,3})?$")


def split_material(material):
    """'fc3s_body01_white__paint80' -> ('fc3s_body01_white', 'paint', 80); 'name' -> ('name', None, None)"""
    if "__" in material:
        a, b = material.rsplit("__", 1)
        m = KIND_RE.match(b)
        if m:
            return a, m.group(1), int(m.group(2)) if m.group(2) else None
    return material, None, None


def resolve_material(pack, material, chunk=None, texmap=None):
    """(tex slot or None if the name is unknown, flags, prog, env, spec, power) for an OBJ material name"""
    texname, kind, power = split_material(material)
    if texmap and material in texmap:
        tex = texmap[material]
    elif texname.lower() in ("none", "untextured", "", "default"):
        tex = NO_TEX
    else:
        tex = pack.tex_index(texname)
    if kind:
        fl, prog, env, spec, pw = pack.preset(kind)
        return (tex, fl, prog, env, spec, min(power * 100, 65535) if power is not None else pw)
    if tex is not None:                                          # same texture as existing batches: their commonest look
        for pool in ([chunk] if chunk else []) + [None]:
            use = {}
            for c in ([pool] if pool else pack.chunks):
                for b in c.batches:
                    if b.tex == tex:
                        use[b.material()] = use.get(b.material(), 0) + len(b.V)
            if use:
                return max(use.items(), key=lambda kv: kv[1])[0]
    return (tex,) + pack.preset("glass" if re.search(r"glass|window|lens", texname, re.I) else "flat")


def dominant_material(pack, chunk):
    """the look most of a part's surface has (by vertices); the body's when the part is new or empty"""
    for pool in (chunk, pack.chunk(0)):
        if pool is None or not pool.batches:
            continue
        use = {}
        for b in pool.batches:
            if b.flags & 3 == 0:                                   # not the see-through ones
                use[b.material()] = use.get(b.material(), 0) + len(b.V)
        if use:
            return max(use.items(), key=lambda kv: kv[1])[0]
    return None


def build_batches(pack, meshes, chunk=None, texmap=None):
    """meshes -> [Batch] (opaque first, then see-through; split at 65535 vertices). Unknown materials -> list of names"""
    groups, missing = {}, []
    for m in meshes:
        tex, fl, prog, env, spec, pw = resolve_material(pack, m.material, chunk, texmap)
        if tex is None:                                              # a material name the car does not know (3D programs rename them):
            if m.material not in missing:                            # give it the part's usual look (paint on a body part), not plain white
                missing.append(m.material)
            dm = dominant_material(pack, chunk)
            if dm is not None:
                tex, fl, prog, env, spec, pw = dm
            else:
                tex = NO_TEX
        groups.setdefault((tex, fl, prog, env, spec, pw), []).append(m)
    out = []
    for (tex, fl, prog, env, spec, pw), items in sorted(groups.items(), key=lambda kv: (kv[0][1] & 3 != 0, kv[0][0], kv[0][2])):
        vbuf, ibuf, base = [], [], 0
        for m in items:
            used, inv = np.unique(m.tris, return_inverse=True)
            if len(used) > POOL:
                raise EditError("Mesh '%s' has more than %d vertices in one piece: split it in two." % (m.name, POOL))
            if base + len(used) > POOL:
                out.append(Batch(tex, fl, prog, 0, env, spec, pw, np.concatenate(vbuf), np.concatenate(ibuf)))
                vbuf, ibuf, base = [], [], 0
            vbuf.append(m.V[used])
            ibuf.append((inv.reshape(-1) + base).astype("<u2"))
            base += len(used)
        if vbuf:
            out.append(Batch(tex, fl, prog, 0, env, spec, pw, np.concatenate(vbuf), np.concatenate(ibuf)))
    return out, missing


def replace_parts(pack, meshes, default_tag=None, texmap=None, only_tags=None):
    """put the meshes into the pack: each mesh goes to the part group named in its object name (t<tag>_...) or else
    default_tag. The group's old geometry is dropped. -> {tag: (triangles, [unknown material names])}"""
    by_tag = {}
    for m in meshes:
        tag = m.tag if m.tag is not None else default_tag
        if tag is None:
            raise EditError("Object '%s' does not say which part it is: choose the part to replace." % m.name)
        if only_tags is None or tag in only_tags:
            by_tag.setdefault(tag, []).append(m)
    if not by_tag:
        raise EditError("Nothing in the model matches the part(s) chosen.")
    res = {}
    for tag, ms in by_tag.items():
        batches, missing = build_batches(pack, ms, pack.chunk(tag), texmap)
        pack.set_chunk(tag, batches)
        res[tag] = (sum(len(b.I) // 3 for b in batches), missing)
    return res


def unknown_materials(pack, meshes, texmap=None):
    return sorted({m.material for m in meshes if resolve_material(pack, m.material, None, texmap)[0] is None})


# ------------------------------------------------------------------------------------------------
# shadow volume (the car's footprint pushed down 8 units, like the stock packs)
# ------------------------------------------------------------------------------------------------
def _hull(p):
    pts = sorted(set(map(tuple, np.round(p, 2))))
    if len(pts) < 3:
        return np.array(pts)

    def half(seq):
        h = []
        for q in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (q[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (q[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(q)
        return h
    lo, up = half(pts), half(pts[::-1])
    return np.array(lo[:-1] + up[:-1])


def rebuild_shadow(pack, top=0.0, depth=8.0, low=5.0, inset=0.5):
    body = pack.chunk(0)
    if body is None or not body.batches:
        raise EditError("There is no body to take the shadow from.")
    pos = np.concatenate([b.V["pos"] for b in body.batches]).astype(np.float64)
    pts = pos[pos[:, 1] < low][:, [0, 2]]
    hull = _hull(pts if len(pts) >= 3 else pos[:, [0, 2]])
    c = hull.mean(0)
    w, l = max(np.ptp(hull[:, 0]), 1e-3), max(np.ptp(hull[:, 1]), 1e-3)
    hull = c + (hull - c) * np.array([max(w - 2 * inset, 1) / w, max(l - 2 * inset, 1) / l])
    n = len(hull)
    Pp = [(x, top, z) for x, z in hull] + [(x, top - depth, z) for x, z in hull]
    T = []
    for i in range(1, n - 1):
        T += [(0, i, i + 1), (n, n + i + 1, n + i)]
    for i in range(n):
        j = (i + 1) % n
        T += [(i, n + i, n + j), (i, n + j, j)]
    Pp = np.array(Pp, np.float64)
    T = np.array(T, np.int64)
    c3 = Pp.mean(0)
    if sum(np.dot(Pp[a] - c3, np.cross(Pp[b] - c3, Pp[cc] - c3)) for a, b, cc in T) < 0:
        T = T[:, [0, 2, 1]]
    V = np.zeros(len(Pp), VERTEX_LIT)
    V["pos"], V["rgba"] = Pp, 255
    pack.set_chunk(WPK_SHADOW, [Batch(NO_TEX, 0, 0, 0, NO_TEX, NO_TEX, 0, V, T.reshape(-1).astype("<u2"))])


# ------------------------------------------------------------------------------------------------
# headlights: a lens cover that is solid, lamp bulbs that only exist for a race
# ------------------------------------------------------------------------------------------------
LAMP_OFF, LAMP_ON = 0x110, 0x111
LENS_TEX = "lens_clear.dds"


LIGHT_MODES = ("Garage / menus (lamps off)", "Race, lamps on", "Race, braking")


ENGINE_TAGS = ((0, 2), (0x100, 0x103), (0x110, 0x114), (0x120, 0x12D), (0x141, 0x17F), (0xFFFF, 0xFFFF))


def engine_draws(tag):
    """True for the part numbers the Re3DX+ engine knows (render.c: ground / short / long range 0-2, wheels, lamps, the dress-up
    parts and custom tyres 0x120-0x12d, the high brake lamps 0x141-0x17f, the shadow).  Any other number is a 'named object'
    which a car never turns on, so the engine does not draw it."""
    return any(lo <= tag <= hi for lo, hi in ENGINE_TAGS)


def light_shown(tag, mode):
    """does the game draw this part in that lighting?  (render_hq: LAMP_OFF in the menus, LAMP_ON and the stop lamps in a race)"""
    on = mode != LIGHT_MODES[0]
    if tag == LAMP_OFF:
        return not on
    if tag in (0x111, 0x112, 0x113, 0x114) or 0x140 <= tag < 0x180:
        if not on:
            return False
        if tag == 0x112:
            return mode != LIGHT_MODES[2]
        if tag == 0x113 or 0x140 <= tag < 0x180:
            return mode == LIGHT_MODES[2]
    return True


def _weld_islands(pos, tris):
    """connected pieces of a triangle list (vertices welded by position) -> island number per triangle"""
    key = np.round(np.asarray(pos, np.float64) / 0.05).astype(np.int64)
    _, vid = np.unique(key, axis=0, return_inverse=True)
    vid = vid.reshape(-1)
    par = list(range(int(vid.max()) + 1 if len(vid) else 0))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for a, b, c in tris:
        ra = find(vid[a])
        for q in (vid[b], vid[c]):
            rq = find(q)
            if rq != ra:
                par[rq] = ra
    root = np.array([find(vid[t[0]]) for t in tris], np.int64)
    return np.unique(root, return_inverse=True)[1].reshape(-1)


def _lamp_clusters(on):
    """the solid (not glowing) pieces of the lamps-ON part, grouped into lamps: [(lo, hi, [(batch, triangle rows)])]
    Pieces whose boxes touch (within 0.6 units) belong to one lamp (a lamp is a reflector with a row of bulbs)."""
    pieces = []                                              # (lo, hi, batch index, rows)
    for bi, b in enumerate(on.batches):
        if not len(b.I) or b.flags & 3:
            continue
        tri = b.I.reshape(-1, 3).astype(np.int64)
        lab = _weld_islands(b.V["pos"][tri.reshape(-1)].reshape(-1, 3), np.arange(len(tri) * 3).reshape(-1, 3))
        for k in range(int(lab.max()) + 1):
            rows = np.nonzero(lab == k)[0]
            P = b.V["pos"][tri[rows].reshape(-1)]
            pieces.append((P.min(0).astype(np.float64), P.max(0).astype(np.float64), bi, rows))
    groups = []
    for lo, hi, bi, rows in pieces:
        hit = [g for g in groups if np.all(lo <= g[1] + 0.6) and np.all(hi >= g[0] - 0.6)]
        g = [lo.copy(), hi.copy(), [(bi, rows)]]
        for h in hit:
            g[0], g[1] = np.minimum(g[0], h[0]), np.maximum(g[1], h[1])
            g[2] = g[2] + h[2]
        groups = [x for x in groups if not any(x is h for h in hit)]
        groups.append(g)
    return [tuple(g) for g in groups]


def fix_headlights(pack, dry=False, dark=50, reach=1.0, tint=None):
    """Make a car's headlights look in the game the way they do in a 3D program.

    Two things go wrong when a body is brought in from another model:
      * the lens cover is a solid dark piece, so the bulbs behind it are hidden.  The game has no "transparent" flag on a
        solid material; a see-through cover needs the glass material (alpha blended).  The cover becomes clear glass.
      * the bulbs / reflector sit only in the lamps-ON part (0x111), which the game draws in a race, not in the menus
        and garage (they draw 0x110).  The bulbs are copied into 0x110, so both show them.
    Only lamps at the front or back of the car are touched; nothing happens when no such lamp or cover is found.
    dry=True only looks: nothing changes, rep["cover"] = {body batch index: triangle rows} says what WOULD become glass.
    dark = how dark a paint texel must be (0-255 brightness) to count as a black lens in the painted shell; reach = how far
    around a lamp (1 = the normal reach, 0.5 = half) triangles are taken.
    tint = (r, g, b, alpha 0-255) of the lens glass, None = the default almost clear lens.
    -> dict(covers = triangles made glass, lamps = lamps found, bulbs = triangles copied to 0x110, notes = [text])"""
    rep = dict(covers=0, lamps=0, bulbs=0, notes=[], cover={})
    body, on = pack.chunk(0), pack.chunk(LAMP_ON)
    if body is None or not body.batches or on is None or not on.batches:
        rep["notes"].append("No lamps-ON part (0x111) with solid bulbs: nothing to do.")
        return rep
    zmax = max(abs(float(b.V["pos"][:, 2].max())) for b in body.batches if len(b.V))
    lamps = [g for g in _lamp_clusters(on)
             if abs((g[0][2] + g[1][2]) / 2) >= 0.55 * zmax and abs((g[0][0] + g[1][0]) / 2) >= 2.0]
    rep["lamps"] = len(lamps)
    if not lamps:
        rep["notes"].append("No headlight or tail light bulbs found in the lamps-ON part.")
        return rep
    # --- the cover: a solid, unlit piece of the body that wraps the bulbs (its box holds the lamp's box, and is not much bigger)
    cover = {}                                               # batch index -> list of triangle row arrays
    for bi, b in enumerate(body.batches):
        if not len(b.I) or b.flags & 3 or b.tex == NO_TEX:
            continue                                         # glass and glow pieces are not covers
        tri = b.I.reshape(-1, 3).astype(np.int64)
        if b.prog != 1:
            lab = _weld_islands(b.V["pos"][tri.reshape(-1)].reshape(-1, 3), np.arange(len(tri) * 3).reshape(-1, 3))
            for k in range(int(lab.max()) + 1):
                rows = np.nonzero(lab == k)[0]
                if len(rows) > 1500:
                    continue
                P = b.V["pos"][tri[rows].reshape(-1)]
                lo, hi = P.min(0), P.max(0)
                for glo, ghi, _items in lamps:
                    if np.all(lo <= glo + 0.25) and np.all(hi >= ghi - 0.25) and np.all((hi - lo) <= (ghi - glo) + 3.0):
                        cover.setdefault(bi, []).append(rows)
                        break
        else:
            # a cover that is part of the painted body shell: it is the black part of the paint texture over the bulbs
            tx = pack.decode_tex(b.tex)
            th, tw = tx.shape[:2]
            cen = b.V["pos"][tri].mean(1)
            uv = b.V["uv"][tri].mean(1)
            lum = tx[(uv[:, 1] % 1 * th).astype(int).clip(0, th - 1), (uv[:, 0] % 1 * tw).astype(int).clip(0, tw - 1), :3].mean(1)
            for glo, ghi, _items in lamps:
                m = np.all((cen >= glo - np.array((0.8, 0.5, 1.3)) * reach) & (cen <= ghi + np.array((0.8, 0.5, 1.3)) * reach), 1) & (lum < dark)
                if m.any():
                    cover.setdefault(bi, []).append(np.nonzero(m)[0])
    rep["cover"] = {bi: np.unique(np.concatenate(rl)) for bi, rl in cover.items()}
    rep["covers"] = int(sum(len(r) for r in rep["cover"].values()))
    if dry:
        rep["covers"] = int(sum(len(r) for r in rep["cover"].values()))
        rep["notes"].append("%d triangles would become clear glass." % rep["covers"])
        return rep
    rep["covers"] = 0
    new_batches = []
    if cover:
        col = tuple(tint) if tint is not None else (215, 220, 230, 51)
        slot = pack.add_texture(LENS_TEX, Image.new("RGBA", (32, 32), col), max_size=32)
        fl, prog, env, spec, pw = pack.preset("glass")
        for bi, rowlist in cover.items():
            b = body.batches[bi]
            tri = b.I.reshape(-1, 3).astype(np.int64)
            rows = np.unique(np.concatenate(rowlist))
            keep = np.ones(len(tri), bool)
            keep[rows] = False
            used, inv = np.unique(tri[rows], return_inverse=True)
            V = b.V[used].copy()
            V["uv"] = (0.5, 0.5)
            V["rgba"] = 255
            new_batches.append(Batch(slot, fl, prog, 0, env, spec, pw, V, inv.reshape(-1).astype("<u2")))
            b.I = tri[keep].reshape(-1).astype(b.I.dtype)
            rep["covers"] += len(rows)
        body.batches = [b for b in body.batches if len(b.I)] + new_batches
        body.update_bbox()
        rep["notes"].append("%d triangles of lens cover are now clear glass." % rep["covers"])
    else:
        rep["notes"].append("No solid lens cover over the bulbs (already see-through, or none).")
    # --- the bulbs for the menus / garage (0x110)
    off = pack.chunk(LAMP_OFF)
    if off is None or not off.batches:
        out = []
        for _lo, _hi, items in lamps:
            for bi, rows in items:
                b = on.batches[bi]
                tri = b.I.reshape(-1, 3).astype(np.int64)[rows]
                used, inv = np.unique(tri, return_inverse=True)
                out.append(Batch(b.tex, b.flags, b.prog, b.pad, b.env, b.spec, b.power, b.V[used].copy(), inv.reshape(-1).astype("<u2")))
                rep["bulbs"] += len(rows)
        merged = {}
        for b in out:                                        # one batch per material
            merged.setdefault(b.material(), []).append(b)
        res = []
        for _m, bs in merged.items():
            vs, ix, base = [], [], 0
            for b in bs:
                vs.append(b.V)
                ix.append(b.I.astype(np.int64) + base)
                base += len(b.V)
            b0 = bs[0]
            res.append(Batch(b0.tex, b0.flags, b0.prog, b0.pad, b0.env, b0.spec, b0.power, np.concatenate(vs), np.concatenate(ix).astype("<u2")))
        pack.set_chunk(LAMP_OFF, res)
        rep["notes"].append("%d triangles of bulbs copied to the lamps-OFF part (0x110) for the menus and garage." % rep["bulbs"])
    else:
        rep["notes"].append("The lamps-OFF part (0x110) already has its own geometry: left alone.")
    pack.check()
    return rep


# ------------------------------------------------------------------------------------------------
# the model the software renderer (wpk_view) draws
# ------------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------------
# windows: panes that were imported as dark body paint (a car made from one texture atlas) -> see-through glass
# ------------------------------------------------------------------------------------------------
def _glass_template(pack, body):
    """(tex, flags, prog, pad, env, spec, power) of the glass material to use: the car's own glass batch when it has one
    (headlight covers ...), else the pack's glass preset with a texture named like glass / lens / window"""
    for c in pack.chunks:
        for b in c.batches:
            if preset_of(b) == "glass" and b.tex != NO_TEX:
                return b.tex, b.flags, b.prog, b.pad, b.env, b.spec, b.power
    tex = next((i for i, t in enumerate(pack.tex) if re.search(r"glass|window|lens", t["name"], re.I) and "spec" not in t["name"].lower()), None)
    if tex is None:
        return None
    fl, prog, env, spec, pw = pack.preset("glass")
    return tex, fl, prog, 0, env, spec, pw


WINDOW_INNER = 0.72                  # side mirrors: nothing wider than this share of the body's half width becomes glass
GLASS_TINT = (90, 94, 98, 112)       # a stock car's glass texture averages about this: grey, 40-45 % opaque


def window_candidates(pack, dark=60, tiny=4.0, above=0.48, tick=None):
    """The slow half of find_window_panes (seconds): every flat-dark body triangle above the lower body, whether the outside of the car
    can see it, its neighbours.  It does not depend on `inner` / `min_pane` / `min_span`, so the Clear windows window keeps it and
    those sliders are instant.  tick() is called now and then (the window passes root.update so it stays responsive)."""
    body = pack.chunk(0)
    if body is None or not body.batches:
        raise EditError("This car has no body to look at.")
    tpl = _glass_template(pack, body)
    if tpl is None:
        raise EditError("The car has no glass material to copy (no glass batch and no texture named glass / lens / window).")
    # every solid piece of the body can hide a pane's outside: the occluders (glass itself does not)
    occ = [b.V["pos"][b.I.reshape(-1, 3)].astype(np.float64) for b in body.batches if len(b.I) and preset_of(b) != "glass"]
    if not occ:
        raise EditError("This car has no solid body.")
    A = np.concatenate(occ)
    E1, E2 = A[:, 1] - A[:, 0], A[:, 2] - A[:, 0]
    allp = A.reshape(-1, 3)
    ylo, yhi = float(allp[:, 1].min()), float(allp[:, 1].max())
    ycut = ylo + above * (yhi - ylo)
    centre = np.array([0.0, (ylo + yhi) / 2, 0.0])
    ylow = ylo + 0.30 * (yhi - ylo)
    xmax = float(np.abs(allp[:, 0]).max())
    # every flat-dark body triangle above ylow is a candidate; `seed` ones are also high enough and free on the outside
    C_b, C_r, C_n, C_k, C_seed, C_x = [], [], [], [], [], []   # batch, row, outward normal, 3 welded corner keys, seed?, |x|
    keyid = {}
    for bi, b in enumerate(body.batches):
        if not len(b.I) or b.flags & 3 or b.tex == NO_TEX or preset_of(b) == "glass" or not pack.tex[b.tex]["raw"]:
            continue
        t = pack.tex[b.tex]
        im = decode16(t["w"], t["h"], t["fmt"], t["raw"])[..., :3].astype(int)
        tri = b.I.reshape(-1, 3).astype(np.int64)
        uv = b.V["uv"][tri].astype(np.float64)
        ext = (uv.max(1) - uv.min(1)).max(1) * max(t["w"], t["h"])
        cu = uv.mean(1)
        px = (np.clip(cu[:, 0] % 1.0, 0, 0.999) * t["w"]).astype(int)
        py = (np.clip(cu[:, 1] % 1.0, 0, 0.999) * t["h"]).astype(int)
        flat_dark = (ext < tiny) & (im[py, px].sum(1) < dark)
        P = b.V["pos"][tri].astype(np.float64)
        cp = P.mean(1)
        idx = np.nonzero(flat_dark & (cp[:, 1] > ylow))[0]
        if not len(idx):
            continue
        n = np.cross(P[idx, 1] - P[idx, 0], P[idx, 2] - P[idx, 0])
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
        sg = np.sign((n * (cp[idx] - centre)).sum(1))
        n *= np.where(sg == 0, 1, sg)[:, None]
        org = cp[idx] + n * 0.05
        free = np.zeros(len(idx), bool)
        for s0 in range(0, len(idx), 60):
            if tick:
                tick()
            O, D = org[s0:s0 + 60, None, :], n[s0:s0 + 60, None, :]
            h = np.cross(D, E2[None])
            a = (E1[None] * h).sum(-1)
            with np.errstate(all="ignore"):
                f = 1.0 / a
                sv = O - A[None, :, 0]
                u = f * (sv * h).sum(-1)
                q = np.cross(sv, E1[None])
                v_ = f * (D * q).sum(-1)
                tt = f * (E2[None] * q).sum(-1)
                hit = (np.abs(a) > 1e-9) & (u >= 0) & (v_ >= 0) & (u + v_ <= 1) & (tt > 0.3)    # (a neighbouring skin 3 cm away is no cover)
            free[s0:s0 + 60] = ~hit.any(1)
        Pq = np.round(b.V["pos"].astype(np.float64), 2)
        for j, r in enumerate(idx):
            ks = []
            for i in tri[r]:
                kk = tuple(Pq[i])
                ks.append(keyid.setdefault(kk, len(keyid)))
            C_b.append(bi); C_r.append(int(r)); C_n.append(n[j]); C_k.append(ks)
            C_seed.append(bool(free[j] and cp[r, 1] > ycut))
            C_x.append(abs(float(cp[r, 0])))
    kpos = np.zeros((len(keyid), 3))
    for kk, i_ in keyid.items():
        kpos[i_] = kk
    return {"tpl": tpl, "b": C_b, "r": C_r, "n": np.array(C_n), "k": C_k, "seed": np.array(C_seed, bool), "x": np.array(C_x),
            "xmax": xmax, "kpos": kpos}


def find_window_panes(pack, dark=60, tiny=4.0, above=0.48, min_pane=40, min_span=4.0, inner=WINDOW_INNER, cands=None, tick=None):
    """Window panes of a newly imported car are often plain body paint: the model had one texture atlas, the panes sit on a
    dark spot of it, so they come out solid black.  This moves them into a glass batch (alpha blended, like a stock car's).
    A body triangle becomes glass when ALL of these hold (`min_span` = 4 units = 40 cm: smaller groups stay; `inner` = 0.72: nothing wider than 72 % of the body's half width, so the side mirrors stay):
      - its three corners sit within `tiny` texels of each other on a dark texel (a flat colour, not a pattern), and
      - it is on the OUTSIDE of the car (a ray leaving it along its outward normal hits nothing: the seats, dash and
        steering wheel behind a pane are not touched), and
      - it is above `above` (0.48 = just under half) of the body's height (bumper and sill trim stay black), and
      - it is part of a connected group of at least `min_pane` triangles (thin trim and wipers stay black).
    -> (rows, template): rows = {batch index: [triangle rows]} of the body, template = the glass material (see _glass_template).  Slow (seconds):
    keep the result when the tint is tried several times."""
    if cands is None:
        cands = window_candidates(pack, dark, tiny, above, tick)
    tpl, C_b, C_r, C_n, C_k, kpos = cands["tpl"], cands["b"], cands["r"], cands["n"], cands["k"], cands["kpos"]
    ok = cands["x"] <= inner * cands["xmax"]            # side mirrors stick out past the body: nothing wider than this is a pane
    final = {}
    if C_b:
        N = len(C_b)
        sel = cands["seed"] & ok
        edges = {}
        for t_, ks in enumerate(C_k):
            for e in ((ks[0], ks[1]), (ks[1], ks[2]), (ks[2], ks[0])):
                edges.setdefault((min(e), max(e)), []).append(t_)
        nb = [[] for _ in range(N)]
        for tl in edges.values():
            for x in tl:
                nb[x].extend(y for y in tl if y != x)
        # grow along shared edges into flat-dark triangles that face the same way (the rim rows of a pane)
        for _ in range(3):
            add = [y for x in np.nonzero(sel)[0] for y in nb[x] if ok[y] and not sel[y] and float(C_n[x] @ C_n[y]) > 0.85]
            if not add:
                break
            sel[np.array(add)] = True
        # keep connected panes only
        parent = list(range(len(kpos)))

        def find(a_):
            while parent[a_] != a_:
                parent[a_] = parent[parent[a_]]
                a_ = parent[a_]
            return a_
        for t_ in np.nonzero(sel)[0]:
            ks = C_k[t_]
            for q in ks[1:]:
                ra, rb = find(ks[0]), find(q)
                if ra != rb:
                    parent[ra] = rb
        size, lo, hi = {}, {}, {}
        for t_ in np.nonzero(sel)[0]:
            r_ = find(C_k[t_][0])
            size[r_] = size.get(r_, 0) + 1
            pts = kpos[C_k[t_]]
            lo[r_] = np.minimum(lo.get(r_, pts.min(0)), pts.min(0))
            hi[r_] = np.maximum(hi.get(r_, pts.max(0)), pts.max(0))
        for t_ in np.nonzero(sel)[0]:
            r_ = find(C_k[t_][0])
            # a pane is a big group: small compact ones (mirror housings, badges ...) stay as they are
            if size[r_] >= min_pane and float((hi[r_] - lo[r_]).max()) >= min_span:
                final.setdefault(C_b[t_], []).append(C_r[t_])
    return final, tpl, cands


def make_windows_glass(pack, dry=False, tint=GLASS_TINT, panes=None, **kw):
    """Move the window panes (find_window_panes) into a glass batch.
    tint = (r, g, b, alpha 0-255): the panes get a small texture of that colour, like a stock car's tinted glass texture (the default
    looks like a stock car's glass); tint=None keeps the car's own glass texture (an imported car's headlight lens is almost clear).
    panes: the result of find_window_panes, when it was done already (the Clear windows dialog tries many tints).
    dry=True only counts.  -> dict(tris = triangles made glass, notes = [text])"""
    rep = {"tris": 0, "notes": []}
    body = pack.chunk(0)
    final, tpl = (panes if panes is not None else find_window_panes(pack, **kw))[:2]
    rep["tris"] = sum(len(v_) for v_ in final.values())
    if not rep["tris"]:
        rep["notes"].append("No window panes found: nothing on the outside of the body is a flat dark colour above the belt line.")
        return rep
    rep["notes"].append("%d triangles are now glass." % rep["tris"])
    if dry:
        return rep
    new = []
    if tint is not None:
        tpl = (pack.add_texture("window_tint", Image.new("RGBA", (16, 16), tuple(int(x) for x in tint))),) + tuple(tpl[1:])
    for bi, rows in final.items():
        b = body.batches[bi]
        tri = b.I.reshape(-1, 3).astype(np.int64)
        m = np.zeros(len(tri), bool)
        m[rows] = True
        used, inv = np.unique(tri[m], return_inverse=True)
        new.append(Batch(tpl[0], tpl[1], tpl[2], tpl[3], tpl[4], tpl[5], tpl[6], b.V[used].copy(), inv.reshape(-1).astype("<u2")))
        b.I = tri[~m].reshape(-1).astype(b.I.dtype)
    body.batches = [b for b in body.batches if len(b.I)] + new
    body.update_bbox()
    return rep


def view_model(pack, stock=None):
    """(textures [(name, rgba)], chunks [(6 floats, first, n, tag)], batches [dict]) for wpk_view.rasterize / compose"""
    tex = [(t["name"], pack.decode_tex(i, stock)) for i, t in enumerate(pack.tex)]
    nt = len(tex)
    chunks, batches = [], []
    for c in pack.chunks:
        chunks.append(tuple(c.lo) + tuple(c.hi) + (len(batches), len(c.batches), c.tag))
        for b in c.batches:
            batches.append(dict(tex=b.tex, flags=b.flags, V=b.V, I=b.I.reshape(-1, 3), prog=b.prog,
                                env=b.env if b.env < nt else 0xFFFF, spec=b.spec if b.spec < nt else 0xFFFF,
                                power=b.power / 10000.0))
    return tex, chunks, batches


def stock_pack_of(path):
    """maz_FC3S_aeroA.wpk -> the Pack of maz_FC3S.wpk when it sits beside it, else None"""
    m = re.match(r"(.+)_aero[ABC]\.wpk$", os.path.basename(path), re.I)
    sp = os.path.join(os.path.dirname(os.path.abspath(path)), m.group(1) + ".wpk") if m else None
    try:
        return Pack.load(sp) if sp and os.path.isfile(sp) else None
    except EditError:
        return None


# ------------------------------------------------------------------------------------------------
# the "Models (WPK)" tab
# ------------------------------------------------------------------------------------------------
HELP = """Models (WPK)  -  swap a car's body, bumpers, wings ... for your own model

1. Open car .wpk      pick data/cars/<car>.wpk or one of its aero kits (<car>_aeroA / B / C.wpk). "Kit" jumps between them.
                      Every aero kit is a complete car of its own, so you edit one kit at a time.
2. Look               the ASSET BROWSER lists the car's parts in groups (Body, Wheels, Wing, Bonnet, Lights ...). Search by name, tag
                      (0x122 or 290) or texture name; the chips filter by group. Click a part to select it (Ctrl/Shift for several).
                      In the 3D VIEWPORT: drag = orbit, right-drag or Shift-drag = pan, wheel = zoom, click a part = select it
                      (the browser follows), double-click = fit. The selected part is outlined. PROPERTIES shows what the file
                      holds for it (read-only). Show / hide (the Vis column or the Visible box) only changes the preview.
                      The OPERATION LOG at the bottom records opens, saves, copies and errors (Copy all puts it on the clipboard).
3. Export OBJ         writes the selected parts (or all) as OBJ + MTL + the textures as PNG. Open it in Blender (or any
                      3D program): the car is in metres, +Z front, +Y up. Objects are named t<part>_b<n>, materials
                      <texture>__<kind><reflection>, e.g. fc3s_body01_white__paint80. Keep those names and the file
                      goes back into the right part by itself.
4. Replace with OBJ   pick your model, choose the part it replaces, check the materials, OK. The old geometry of that part
                      is dropped. Scale 10 = your OBJ is in metres. Wrong way round: Rotate 180. Looks inside out: Flip faces.
5. Save to game       writes the .wpk you opened; the old file is kept once as <file>.bak.

Add part              gives the car one more part, e.g. a second bonnet.  Type its name (bonnet_B, wing_C, mirror_B, boot_A,
                      exhaust_B, rollcage_A, roofitem_3, or a tag number like 0x127) and choose to start from a copy of an existing
                      part or from nothing.  Then select it and use Replace with OBJ.  The .wpk stores only a number per part, no
                      names, so the name just picks the slot: bonnet_B = tag 0x127, as the list shows.  A slot that is already
                      used is refused (the stock cars have Bonnet A-D, Wing A-D and Mirrors A-B all in use; a few lack one:
                      the NCEC has no Wing A).  A part that is still empty must get a model before Save.

Materials (the word after __ in the OBJ material name)
  paint    the car's paint: reflective, uses the body texture   (paint80 = reflection strength, 0-100)
  flat     plain lit surface (interior, tyres, plastic, carbon)
  glass    see-through window with the car's glass tint + reflection
  add      glowing, added to what is behind it (lamp glow)
  metal    reflective, no paint (wheels)
  A name that is just a texture name (no __kind) copies the look of an existing part that uses that texture.

Textures: a new model should either use the car's own textures (its UVs must match that car's UV sheet: use Export OBJ to
  see them) or bring its own picture: Textures... > Add new from picture, then name the material after it.
Limits: 65 535 vertices in one piece (split bigger meshes), triangles are made from any polygons, no skeleton / animation.
Wheels must stay in their own parts (they spin and steer); a custom wheel goes in the Wheel parts, centred on the hub (0,0,0).
Not checked: loading a changed .wpk in the Android app itself.
"""


def build_tab(parent, root, app=None):
    import copy
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    import traceback

    from PIL import Image, ImageTk

    import wpk_browser as WB
    import wpk_texmgr as TM
    try:
        import wpk_view
    except ImportError:
        wpk_view = None
    try:
        import gl_viewport as GLV                          # the GPU viewport (moderngl + pyopengltk); optional
        GLV_WHY = GLV.import_error()
    except Exception as _ex:
        GLV, GLV_WHY = None, "gl_viewport.py could not be loaded (%s)" % _ex
    VIEWS = wpk_view.VIEWS if wpk_view else []
    FREE = wpk_view.FREE if wpk_view else 6                 # value of the "Free" view radio (after the fixed views)
    LEVEL_COLOUR = {"info": "#8f9eb2", "success": "#3fb950", "warning": "#e8a317", "error": "#f85149"}
    OK, ORANGE = "#1b7f3b", "#e8710a"
    W_, H_ = 600, 340

    class TexDialog:
        """Textures and materials window (Milestone 5).  Reads through wpk_texmgr; every change goes through the existing
        Pack.add_texture / Pack.delete_textures with tab.snapshot() first (so Undo works) and tab.edited() after."""
        BOX = 256

        def __init__(self, tab):
            self.tab = tab
            self.cache = TM.PreviewCache()
            self.cat = None
            self.cur = None                       # slot shown in the right pane
            self.photo = None
            self.closed = False
            w = self.win = tk.Toplevel(root)
            w.title("Textures and materials")
            w.transient(root)
            w.minsize(900, 540)
            w.protocol("WM_DELETE_WINDOW", self.close)
            w.bind("<Escape>", lambda _e: self.close())
            self.query, self.flt, self.bg = tk.StringVar(), tk.StringVar(value="All"), tk.StringVar(value="checker")
            self.count_txt, self.empty_txt = tk.StringVar(), tk.StringVar()
            self.name_txt, self.info_txt, self.note_txt, self.prev_txt = (tk.StringVar() for _ in range(4))
            w.columnconfigure(0, weight=1)
            w.rowconfigure(1, weight=1)

            top = ttk.Frame(w)
            top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
            top.columnconfigure(1, weight=1)
            ttk.Label(top, text="Search").grid(row=0, column=0, padx=(0, 6))
            self.search_box = ttk.Entry(top, textvariable=self.query)
            self.search_box.grid(row=0, column=1, sticky="ew")
            self.flt_box = ttk.Combobox(top, textvariable=self.flt, values=list(TM.FILTERS), state="readonly", width=12)
            self.flt_box.grid(row=0, column=2, padx=6)
            self.query.trace_add("write", lambda *_: self.fill())
            self.flt_box.bind("<<ComboboxSelected>>", lambda _e: self.fill())
            self.count_lbl = ttk.Label(top, textvariable=self.count_txt)
            self.count_lbl.grid(row=0, column=3, padx=(6, 0))
            ttk.Label(top, text="name, size (256x256), format (565), state, part name or tag (0x122)", foreground="#8f9eb2"
                      ).grid(row=1, column=1, sticky="w")

            pw = ttk.PanedWindow(w, orient="horizontal")
            pw.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
            left = ttk.Frame(pw)
            right = ttk.Frame(pw)
            pw.add(left, weight=3)
            pw.add(right, weight=2)
            left.columnconfigure(0, weight=1)
            left.rowconfigure(0, weight=1)
            cols = (("n", "Texture", 230), ("size", "Size", 70), ("fmt", "Format", 80), ("state", "Status", 130), ("use", "Used by", 70))
            self.tv = ttk.Treeview(left, columns=[c[0] for c in cols], show="headings", selectmode="extended", height=16)
            for c, t, wd in cols:
                self.tv.heading(c, text=t)
                self.tv.column(c, width=wd, anchor="w", stretch=(c == "n"), minwidth=40)
            self.tv.tag_configure("bad", foreground="#f85149")
            self.tv.tag_configure("dim", foreground="#8f9eb2")
            sb = ttk.Scrollbar(left, orient="vertical", command=self.tv.yview)
            self.tv.configure(yscrollcommand=sb.set)
            self.tv.grid(row=0, column=0, sticky="nsew")
            sb.grid(row=0, column=1, sticky="ns")
            self.tv.bind("<<TreeviewSelect>>", lambda _e: self.on_select())
            self.empty_lbl = ttk.Label(left, textvariable=self.empty_txt, foreground="#8f9eb2", justify="center")
            self.empty_lbl.grid(row=0, column=0)

            right.columnconfigure(0, weight=1)
            ttk.Label(right, textvariable=self.name_txt, font=("TkDefaultFont", 10, "bold"), wraplength=330).grid(row=0, column=0, sticky="w")
            ttk.Label(right, textvariable=self.info_txt, foreground="#8f9eb2").grid(row=1, column=0, sticky="w")
            self.canvas = tk.Canvas(right, width=self.BOX, height=self.BOX, highlightthickness=1, background="#1c2128")
            self.canvas.grid(row=2, column=0, pady=6)
            bgrow = ttk.Frame(right)
            bgrow.grid(row=3, column=0, sticky="w")
            ttk.Label(bgrow, text="Background").pack(side="left")
            for val, txt in (("checker", "Checkerboard"), ("dark", "Dark"), ("light", "Light")):
                ttk.Radiobutton(bgrow, text=txt, value=val, variable=self.bg, command=self.show).pack(side="left", padx=4)
            ttk.Label(right, textvariable=self.prev_txt, foreground="#8f9eb2").grid(row=4, column=0, sticky="w")
            ttk.Label(right, text="Used by (parts and how)").grid(row=5, column=0, sticky="w", pady=(8, 0))
            self.users = ttk.Treeview(right, columns=("part", "tag", "draws", "as"), show="headings", height=5, selectmode="none")
            for c, t, wd in (("part", "Part", 130), ("tag", "Tag", 60), ("draws", "Draws", 50), ("as", "As", 110)):
                self.users.heading(c, text=t)
                self.users.column(c, width=wd, anchor="w", stretch=(c == "part"), minwidth=36)
            self.users.grid(row=6, column=0, sticky="ew")
            ttk.Label(right, textvariable=self.note_txt, wraplength=330, justify="left").grid(row=7, column=0, sticky="w", pady=(6, 0))

            row = ttk.Frame(w)
            row.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 4))
            self.b_replace = ttk.Button(row, text="Replace from picture…", command=self.replace_selected)
            self.b_add = ttk.Button(row, text="Add new from picture…", command=self.add_new)
            self.b_export = ttk.Button(row, text="Export PNG…", command=self.export_selected)
            self.b_parts = ttk.Button(row, text="Select parts in browser", command=self.select_parts)
            self.b_delete = ttk.Button(row, text="Delete…", command=self.delete_selected, style="VPK.Danger.TButton")
            self.b_report = ttk.Button(row, text="Report…", command=self.show_report)
            for b in (self.b_replace, self.b_add, self.b_export, self.b_parts, self.b_delete):
                b.pack(side="left", padx=(0, 6))
            ttk.Button(row, text="Close", command=self.close).pack(side="right")
            self.b_report.pack(side="right", padx=6)
            ttk.Label(w, text="Select one or more (Ctrl / Shift click).  Pictures are made a power of two, at most 512.  "
                              "Every change can be undone with Undo; nothing is saved until you Save.",
                      foreground="#8f9eb2").grid(row=3, column=0, sticky="w", padx=8, pady=(0, 8))
            self.refresh()
            self.search_box.focus_set()

        # ---- state
        def alive(self):
            return not self.closed

        def close(self):
            self.closed = True
            if self.tab.tex_dlg is self:
                self.tab.tex_dlg = None
            try:
                self.win.destroy()
            except tk.TclError:
                pass

        def refresh(self):
            """re-read the pack (after any edit, undo, kit switch or open)"""
            tab = self.tab
            if not tab.pack:
                self.close()
                return
            self.cat = TM.catalog(tab.pack, tab.stock, tag_name)
            self.win.title("Textures and materials - %s" % os.path.basename(tab.path or ""))
            self.fill()

        def fill(self):
            if self.closed or self.cat is None:
                return
            keep = [i for i in self.tv.selection()]
            ents = TM.filter_entries(self.cat.entries, self.query.get(), self.flt.get())
            self.tv.delete(*self.tv.get_children())
            for e in ents:
                self.tv.insert("", "end", iid=str(e.index), tags=("bad",) if e.problem else (("dim",) if "unused" in e.flags else ()),
                               values=(e.name, e.size_text, TM.fmt_name(e.fmt), e.status_text(), ("%d part%s" % (len(e.refs), "" if len(e.refs) == 1 else "s")) if e.refs else "-"))
            ids = [i for i in keep if self.tv.exists(i)]
            if ids:
                self.tv.selection_set(ids)
            c = self.cat.counts()
            self.count_txt.set("%d of %d  ·  %d unused  ·  %d borrowed  ·  %s" % (
                len(ents), c["total"], c["unused"], c["borrowed"],
                "%d problem%s" % (c["problems"] + c["dangling"], "" if c["problems"] + c["dangling"] == 1 else "s") if c["problems"] + c["dangling"] else "no problems"))
            try:
                self.count_lbl.configure(foreground="#f85149" if c["problems"] + c["dangling"] else "#3fb950")
            except tk.TclError:
                pass
            if ents:
                self.empty_lbl.grid_remove()
            else:
                self.empty_txt.set("This pack has no textures." if not self.cat.entries else
                                   "No texture matches '%s'%s." % (self.query.get(), "" if self.flt.get() == "All" else " in '%s'" % self.flt.get()))
                self.empty_lbl.grid()
            self.on_select()

        def indexes(self):
            return sorted(int(i) for i in self.tv.selection())

        def single(self):
            s = self.indexes()
            return s[0] if len(s) == 1 else None

        def select_name(self, name):
            i = next((e.index for e in self.cat.entries if e.name == name), None)
            if i is None:
                return
            if not self.tv.exists(str(i)):
                self.query.set("")
                self.flt.set("All")
                self.fill()
            self.tv.selection_set([str(i)])
            self.tv.see(str(i))

        def on_select(self):
            if self.closed:
                return
            s = self.indexes()
            one = len(s) == 1
            st = lambda ok: "normal" if ok else "disabled"
            self.b_replace.configure(state=st(one))
            self.b_export.configure(state=st(one))
            self.b_parts.configure(state=st(s and any(self.cat.entries[i].refs for i in s)))
            self.b_delete.configure(state=st(s))
            self.b_report.configure(state=st(self.cat.warnings))
            self.cur = s[0] if one else None
            self.show(len(s))

        # ---- right pane
        def show(self, nsel=None):
            e = self.cat.by_index(self.cur) if (self.cur is not None and self.cat) else None
            self.canvas.delete("all")
            self.users.delete(*self.users.get_children())
            self.photo = None
            if e is None:
                self.name_txt.set("")
                self.info_txt.set("")
                self.prev_txt.set("")
                self.note_txt.set("" if not nsel else "%d textures selected." % nsel)
                self.canvas.create_text(self.BOX // 2, self.BOX // 2, text="Select a texture to preview it" if not nsel else "Select one texture to preview it",
                                        fill="#8f9eb2")
                return
            tab = self.tab
            self.name_txt.set(e.name)
            self.info_txt.set("%s  ·  %s  ·  %s" % (e.size_text, TM.fmt_name(e.fmt),
                                                      ("%.1f KB" % (e.nbytes / 1024.0)) if e.nbytes else "no data (borrowed)"))
            img, note = self.cache.get(tab.pack, e.index, tab.stock, self.bg.get(), self.BOX)
            if img is None:
                self.canvas.create_text(self.BOX // 2, self.BOX // 2, text=note, fill="#f85149", width=self.BOX - 20)
                self.prev_txt.set("")
            else:
                self.photo = ImageTk.PhotoImage(Image.fromarray(img))
                self.canvas.create_image(self.BOX // 2, self.BOX // 2, image=self.photo, anchor="center")
                self.prev_txt.set(note)
            for t, nm, d, how in TM.users(e, tag_name):
                self.users.insert("", "end", values=(nm, "0x%x" % t, d, how))
            notes = []
            if e.state == "borrowed":
                notes.append("Borrowed from the stock pack (this kit stores no data for it).")
            elif e.state == "MISSING":
                notes.append("MISSING: empty here and not found in the stock pack.")
            elif e.state == "BAD DATA":
                notes.append("BAD DATA: the stored bytes are shorter than the size says.")
            if e.paint_of is not None:
                notes.append("Paint copy of %s." % self.cat.entries[e.paint_of].name)
            if e.paint_copies:
                notes.append("Has %d paint cop%s (%s)." % (len(e.paint_copies), "y" if len(e.paint_copies) == 1 else "ies",
                                                         ", ".join(self.cat.entries[j].name.split(":")[0] for j in e.paint_copies)))
            if "orphan paint copy" in e.flags:
                notes.append("Its base texture is not in this pack.")
            if "duplicate name" in e.flags:
                notes.append("Another slot has the same name.")
            if "unused" in e.flags:
                notes.append("No part draws it.")
            self.note_txt.set("  ".join(notes))

        # ---- actions (all through the existing pack code)
        def _kits(self, e):
            tab = self.tab
            if e.borrowed or not TM.is_stock_path(tab.path or ""):
                return None
            try:
                return TM.kit_borrowers(tab.path, e.name)
            except Exception as ex:                      # a sibling that cannot be read must not stop the dialog
                tab.log("warning", "Could not check the aero kits for %s" % e.name, str(ex))
                return None

        def replace_selected(self):
            i = self.single()
            if i is None:
                return
            fp = filedialog.askopenfilename(parent=self.win, title="Picture for %s" % self.tab.pack.tex[i]["name"],
                                            filetypes=[("Pictures", "*.png *.tga *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
            if fp:
                self.apply_picture(self.tab.pack.tex[i]["name"], fp)

        def add_new(self):
            fp = filedialog.askopenfilename(parent=self.win, title="Picture",
                                            filetypes=[("Pictures", "*.png *.tga *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
            if not fp:
                return
            nm = simpledialog.askstring("Texture name", "Name in the car pack.  A name that already exists REPLACES that texture\n"
                                        "(you will be asked first); then name a material after it:",
                                        initialvalue=os.path.splitext(os.path.basename(fp))[0] + ".tga", parent=self.win)
            if nm and nm.strip():
                self.apply_picture(nm.strip(), fp)

        def apply_picture(self, name, fp):
            """add a texture, or replace the one with exactly this name (what Pack.add_texture does), after the warnings"""
            tab = self.tab
            pack = tab.pack
            name = name[:47]
            try:
                im = Image.open(fp)
                im.load()
            except Exception as ex:
                tab.log("error", "Cannot read picture %s" % os.path.basename(fp), str(ex))
                messagebox.showerror("Texture", "Cannot read %s:\n%s" % (os.path.basename(fp), ex), parent=self.win)
                return
            exact = next((k for k, t in enumerate(pack.tex) if t["name"] == name), None)
            near = pack.tex_index(name)
            if exact is None and near is not None:
                if not messagebox.askyesno("Different file name", "The pack already has '%s'.\n\n'%s' is a different name, so this adds a "
                                           "SEPARATE texture; no part uses it until a material is named after it.\n\nAdd it anyway?"
                                           % (pack.tex[near]["name"], name), icon="warning", parent=self.win):
                    return
            if exact is not None:
                e = self.cat.entries[exact]
                try:
                    plan = TM.plan_replace(pack, exact, im)
                except Exception as ex:
                    messagebox.showerror("Texture", str(ex), parent=self.win)
                    return
                imp = TM.impact(pack, self.cat, exact, tab.path, tab.stock, "replace", self._kits(e))
                new = "%d × %d %s" % (plan["new"][0], plan["new"][1], TM.fmt_name(plan["new"][2]))
                old = "%d × %d %s" % (plan["old"][0], plan["old"][1], TM.fmt_name(plan["old"][2])) if plan["old"][3] else "borrowed"
                if imp["shared"] or plan["alpha_lost"]:
                    det = ["Now: %s" % old, "Will be written as: %s%s" % (new, "   (picture resized from %d × %d)" % plan["source_size"] if plan["resized"] else "")]
                    if plan["alpha_lost"]:
                        det.append("The picture has no transparency, so the texture loses the transparency it had.")
                    if not UI.confirm_destructive(messagebox, self.win, "Replace texture", "Replace texture %s?" % name, det + imp["lines"],
                                                  "Nothing is saved until you Save.", undoable=True):
                        return
            tab.snapshot()
            try:
                slot = pack.add_texture(name, im, 512)
            except Exception as ex:
                if tab.undo:
                    tab.undo.pop()
                tab.log("error", "Texture %s not written" % name, str(ex))
                messagebox.showerror("Texture", str(ex), parent=self.win)
                return
            t = pack.tex[slot]
            tab.edited("texture %s %s -> slot %d (%d × %d %s)" % (name, "replaced" if exact is not None else "added", slot,
                                                                 t["w"], t["h"], TM.fmt_name(t["fmt"])))
            if not self.closed and self.tv.exists(str(slot)):
                self.tv.selection_set([str(slot)])
                self.tv.see(str(slot))

        def export_selected(self):
            i = self.single()
            if i is None:
                return
            tab = self.tab
            fp = filedialog.asksaveasfilename(parent=self.win, defaultextension=".png", initialfile=_stem(tab.pack.tex[i]["name"]) + ".png",
                                              filetypes=[("PNG", "*.png")])
            if not fp:
                return
            try:
                Image.fromarray(tab.pack.decode_tex(i, tab.stock)).save(fp)
            except Exception as ex:
                tab.log("error", "Cannot export texture %s" % tab.pack.tex[i]["name"], str(ex))
                messagebox.showerror("Export PNG", str(ex), parent=self.win)
                return
            tab.log("success", "Exported texture %s to %s" % (tab.pack.tex[i]["name"], fp))

        def select_parts(self):
            tags = sorted({t for i in self.indexes() for t in self.cat.entries[i].refs})
            for k, t in enumerate(tags):
                self.tab.select_tag(t, additive=k > 0)
            if tags:
                self.tab.status.set("Selected %d part%s that draw%s with the chosen texture(s)." % (
                    len(tags), "" if len(tags) == 1 else "s", "s" if len(tags) == 1 else ""))

        def delete_selected(self):
            idxs = self.indexes()
            if not idxs:
                return
            tab = self.tab
            lines, shared = [], False
            for i in idxs:
                e = self.cat.entries[i]
                imp = TM.impact(tab.pack, self.cat, i, tab.path, tab.stock, "delete", self._kits(e))
                if e.in_use or e.paint_copies or any(k[2] for k in imp["kits"]):
                    lines.append("%s: %s" % (e.name, " ".join(imp["lines"])))
            if not UI.confirm_destructive(
                    messagebox, self.win, "Delete textures", "Delete the %d selected texture%s?" % (len(idxs), "" if len(idxs) == 1 else "s"), lines,
                    "The parts that use them stay but lose that picture (flat colour)." if lines else "Nothing uses them.", undoable=True):
                return
            tab.snapshot()
            n, lost = tab.pack.delete_textures(idxs)
            tab.edited("deleted %d texture(s)%s" % (n, ", %d batch(es) lost their picture" % lost if lost else ""))

        def show_report(self):
            if not self.cat or not self.cat.warnings:
                return
            text = "\n".join(self.cat.warnings)
            w = tk.Toplevel(self.win)
            w.title("Texture problems")
            w.transient(self.win)
            t = tk.Text(w, width=90, height=14, wrap="word")
            t.insert("end", text)
            t.configure(state="disabled")
            t.pack(fill="both", expand=True, padx=8, pady=8)

            def cp():
                w.clipboard_clear()
                w.clipboard_append(text)
            row = ttk.Frame(w)
            row.pack(fill="x", padx=8, pady=(0, 8))
            ttk.Button(row, text="Copy", command=cp).pack(side="left")
            ttk.Button(row, text="Close", command=w.destroy).pack(side="right")
            self.tab.log("warning", "%d texture problem(s) in %s" % (len(self.cat.warnings), os.path.basename(self.tab.path or "")), text)

    class Tab:
        def __init__(self):
            self.tex_dlg = None                    # the Textures window, when open
            self.pack = None
            self.stock = None
            self.path = None
            self.dirty = False
            self.undo = []
            self.hidden = set()
            self.added = set()                     # tags of parts added empty with Add part: they need a model before saving
            self.q = queue.Queue()
            self.gen = 0
            self.photo = None
            self.status = UI.StatusLine("Open a car .wpk to start.")
            self.kit = tk.StringVar()
            self.car = tk.StringVar()
            self.cars_txt = tk.StringVar(value="(not set)")
            self.arc_txt = tk.StringVar(value="(not set: asked the first time you import)")
            self.cars_dir = None
            self.arc_dir = None
            self.game_dir = None
            self.game_txt = tk.StringVar(value="(not set: optional, Bake AO has its own)")
            self.cars = {}
            self.arcs = {}
            self.view = tk.IntVar(value=2)
            self.only_sel = tk.BooleanVar(value=False)
            self.engine_only = tk.BooleanVar(value=False)
            self.lights = tk.StringVar(value=LIGHT_MODES[0])
            self.kits = {}
            # asset browser / viewport / inspector state (display only: nothing here is written to the WPK)
            self.search = tk.StringVar()
            self.cat_vars = {c: tk.BooleanVar(value=False) for c in WB.CATEGORIES}
            self.collapsed = set()                  # category groups the user folded
            self.rows = []
            self.cam = WB.Camera(*(VIEWS[self.view.get()][1:] if VIEWS else (-35.0, 14.0)))
            self.oplog = WB.OpLog()
            self._model = None                      # cached view_model(): reused for orbit / pan / zoom / visibility
            self._T = None                          # per-pixel part tag of the last drawn picture (for picking + highlight)
            self._T_size = (1, 1)
            self._drag = None
            self._upp = 0.01
            self._sjob = self._final_job = self._cfg_job = None
            self._busy, self._again, self._wid = False, False, 0     # one picture is drawn at a time; a drag keeps only the NEWEST request
            self._div, self._frame_ms = 2, 50.0                     # drag quality: the picture is drawn at 1/_div size, adapted to the speed
            self._img_item = None
            self._ov = None
            self.gl = None                          # the GPU viewport while it is in use (None: the software one draws)
            self._gl_ok = False
            self.shading = tk.StringVar(value=GLV.DEFAULT_SHADING if GLV else "")
            self.refl_on = tk.BooleanVar(value=True)
            self.ao_on = tk.BooleanVar(value=True)
            self.persp_on = tk.BooleanVar(value=True)
            self.gpu_on = tk.BooleanVar(value=True)
            self.build()
            self.log("info", "Open a car .wpk to start.")
            self.fill()                              # empty-state texts of the browser, inspector and viewport
            self._draw_empty()
            parent.after(100, self.pump)
            self.start()

        # ---------------------------------------------------------------- layout
        def build(self):
            # Compact, task-oriented WPK workspace.  All original callbacks and
            # editor state are retained; this only reorganizes the existing UI.
            shell = ttk.Frame(parent, padding=(12, 10, 12, 8))
            shell.pack(fill="both", expand=True)
            shell.columnconfigure(0, weight=1)
            shell.rowconfigure(2, weight=1)

            header = ttk.Frame(shell)
            header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            header.columnconfigure(1, weight=1)
            ttk.Label(header, text="MODEL WORKSPACE", font=("TkDefaultFont", 10, "bold")).grid(
                row=0, column=0, sticky="w", padx=(0, 12))
            ttk.Label(header, text="Car",).grid(row=0, column=1, sticky="w")
            self.car_box = ttk.Combobox(header, textvariable=self.car, state="disabled", width=24)
            self.car_box.grid(row=0, column=2, sticky="w", padx=(5, 12))
            self.car_box.bind("<<ComboboxSelected>>", lambda _e: self.pick_car())
            ttk.Label(header, text="Aero kit").grid(row=0, column=3, sticky="w")
            self.kit_box = ttk.Combobox(header, textvariable=self.kit, state="disabled", width=14)
            self.kit_box.grid(row=0, column=4, sticky="w", padx=(5, 12))
            self.kit_box.bind("<<ComboboxSelected>>", lambda _e: self.switch_kit())
            ttk.Button(header, text="Open WPK…", command=self.open_dialog).grid(row=0, column=5, padx=(0, 6))
            ttk.Button(header, text="Guide", command=self.help).grid(row=0, column=6)

            # Folder locations are useful but secondary: keep them in one compact
            # horizontal strip rather than three full-width rows.
            locations = ttk.LabelFrame(shell, text="Source locations", padding=(8, 5))
            locations.grid(row=1, column=0, sticky="ew", pady=(0, 8))
            for col in (1, 4, 7):
                locations.columnconfigure(col, weight=1)
            ttk.Label(locations, text="Cars").grid(row=0, column=0, sticky="w")
            ttk.Label(locations, textvariable=self.cars_txt, foreground="#8f9eb2").grid(row=0, column=1, sticky="ew", padx=(5, 5))
            ttk.Button(locations, text="Browse", command=self.browse_cars).grid(row=0, column=2, padx=(0, 12))
            ttk.Label(locations, text="Arcade").grid(row=0, column=3, sticky="w")
            ttk.Label(locations, textvariable=self.arc_txt, foreground="#8f9eb2").grid(row=0, column=4, sticky="ew", padx=(5, 5))
            ttk.Button(locations, text="Browse", command=self.browse_arcade).grid(row=0, column=5, padx=(0, 12))
            ttk.Label(locations, text="Game source").grid(row=0, column=6, sticky="w")
            ttk.Label(locations, textvariable=self.game_txt, foreground="#8f9eb2").grid(row=0, column=7, sticky="ew", padx=(5, 5))
            ttk.Button(locations, text="Browse", command=self.browse_game).grid(row=0, column=8)

            # ---- three resizable columns: ASSET BROWSER | 3D VIEWPORT | PROPERTIES (+ model tools)
            body = ttk.PanedWindow(shell, orient="horizontal")
            body.grid(row=2, column=0, sticky="nsew")
            left = ttk.Frame(body, padding=(0, 0, 6, 0))
            center = ttk.Frame(body, padding=(3, 0, 3, 0))
            right = ttk.Frame(body, padding=(6, 0, 0, 0))
            body.add(left, weight=0)
            body.add(center, weight=1)
            body.add(right, weight=0)
            self._body = body
            self._sashes_set = False
            body.bind("<Configure>", self._place_sashes)

            # ---------------- left: asset browser
            left.rowconfigure(3, weight=1)
            left.columnconfigure(0, weight=1)
            ttk.Label(left, text="ASSET BROWSER", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
            sbar = ttk.Frame(left)
            sbar.grid(row=1, column=0, sticky="ew")
            sbar.columnconfigure(1, weight=1)
            ttk.Label(sbar, text="Search").grid(row=0, column=0, padx=(0, 5))
            self.search_entry = ttk.Entry(sbar, textvariable=self.search)
            self.search_entry.grid(row=0, column=1, sticky="ew")
            ttk.Button(sbar, text="Clear", width=6, command=self.clear_filters).grid(row=0, column=2, padx=(5, 0))
            self.search.trace_add("write", lambda *_a: self._search_changed())
            chips = ttk.Frame(left)
            chips.grid(row=2, column=0, sticky="ew", pady=(5, 5))
            for i, cname in enumerate(WB.CATEGORIES):
                chips.columnconfigure(i % 4, weight=1)
                ttk.Checkbutton(chips, text=cname, variable=self.cat_vars[cname], style="Toolbutton",
                                command=self.fill).grid(row=i // 4, column=i % 4, sticky="ew", padx=1, pady=1)
            tf = ttk.Frame(left)
            tf.grid(row=3, column=0, sticky="nsew")
            tf.rowconfigure(0, weight=1)
            tf.columnconfigure(0, weight=1)
            self.tree = ttk.Treeview(tf, columns=("show", "batches", "tris"), show="tree headings", selectmode="extended", height=14)
            self.tree.heading("#0", text="Part / tag")
            self.tree.column("#0", width=170, anchor="w", stretch=True, minwidth=110)
            for c, title, width in (("show", "Vis", 34), ("batches", "Draws", 48), ("tris", "Tris", 56)):
                self.tree.heading(c, text=title)
                self.tree.column(c, width=width, anchor="center" if c == "show" else "e", stretch=False, minwidth=width)
            tsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=tsb.set)
            self.tree.grid(row=0, column=0, sticky="nsew")
            tsb.grid(row=0, column=1, sticky="ns")
            self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())
            self.tree.bind("<<TreeviewOpen>>", lambda _e: self._fold(False))
            self.tree.bind("<<TreeviewClose>>", lambda _e: self._fold(True))
            self.tree.bind("<Button-1>", self._tree_click)
            self.empty = ttk.Label(tf, text="", foreground="#8f9eb2", justify="center", wraplength=220)
            ttk.Label(left, text="Click a part (or the ● in the Vis column) to select / show it.  * = number the game engine does not draw",
                      foreground="#8f9eb2", wraplength=290, justify="left").grid(row=4, column=0, sticky="w", pady=(4, 6))

            part_actions = ttk.LabelFrame(left, text="Part actions", padding=6)
            part_actions.grid(row=5, column=0, sticky="ew")
            for col in range(2):
                part_actions.columnconfigure(col, weight=1)
            self.b_export = ttk.Button(part_actions, text="Export OBJ…", command=self.export, state="disabled")
            self.b_replace = ttk.Button(part_actions, text="Replace OBJ…", command=self.replace, state="disabled")
            self.b_add = ttk.Button(part_actions, text="Add part…", command=self.add_part, state="disabled")
            self.b_move = ttk.Button(part_actions, text="Move tag…", command=self.move_dialog, state="disabled")
            self.b_kits = ttk.Button(part_actions, text="Copy to kits…", command=self.kits_dialog, state="disabled")
            self.b_del = ttk.Button(part_actions, text="Delete", command=self.delete, state="disabled", style="VPK.Danger.TButton")
            self.b_vis = ttk.Button(part_actions, text="Show / hide", command=self.toggle_vis, state="disabled")
            self.b_undo = ttk.Button(part_actions, text="Undo", command=self.do_undo, state="disabled")
            for i, b in enumerate((self.b_export, self.b_replace, self.b_add, self.b_move,
                                   self.b_kits, self.b_del, self.b_vis, self.b_undo)):
                b.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)

            # ---------------- centre: interactive viewport
            center.rowconfigure(1, weight=1)
            center.columnconfigure(0, weight=1)
            vhead = ttk.Frame(center)
            vhead.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            ttk.Label(vhead, text="3D VIEWPORT", font=("TkDefaultFont", 10, "bold")).pack(side="left")
            ttk.Label(vhead, text="drag: orbit   right-drag / Shift-drag: pan   wheel: zoom   click: select   double-click: fit",
                      foreground="#8f9eb2").pack(side="right")
            self.canvas = tk.Canvas(center, width=W_, height=H_, background="#111a25", highlightthickness=1,
                                    highlightbackground="#2a3a4d", cursor="fleur")
            self.canvas.grid(row=1, column=0, sticky="nsew")
            self.canvas.bind("<ButtonPress-1>", self._vp_press)
            self.canvas.bind("<B1-Motion>", self._vp_motion)
            self.canvas.bind("<ButtonRelease-1>", self._vp_release)
            for btn in (2, 3):
                self.canvas.bind("<ButtonPress-%d>" % btn, self._vp_press_pan)
                self.canvas.bind("<B%d-Motion>" % btn, self._vp_motion)
                self.canvas.bind("<ButtonRelease-%d>" % btn, self._vp_release)
            self.canvas.bind("<Shift-ButtonPress-1>", self._vp_press_pan)
            self.canvas.bind("<MouseWheel>", self._vp_wheel)
            self.canvas.bind("<Button-4>", self._vp_wheel)
            self.canvas.bind("<Button-5>", self._vp_wheel)
            self.canvas.bind("<Double-Button-1>", lambda _e: self.fit_view())
            self.canvas.bind("<Configure>", self._canvas_resized)
            controls = ttk.Frame(center)
            controls.grid(row=2, column=0, sticky="ew", pady=(6, 0))
            for i, (name, _y, _p) in enumerate(VIEWS):
                ttk.Radiobutton(controls, text=name, variable=self.view, value=i, command=self.view_changed,
                                style="Toolbutton").grid(row=i // 4, column=i % 4, sticky="ew", padx=1, pady=1)
            ttk.Radiobutton(controls, text="Free", variable=self.view, value=FREE, command=self.view_changed,
                            style="Toolbutton").grid(row=len(VIEWS) // 4, column=len(VIEWS) % 4, sticky="ew", padx=1, pady=1)
            for col in range(4):
                controls.columnconfigure(col, weight=1)
            ttk.Button(controls, text="Fit / reset camera", command=self.fit_view).grid(row=1, column=3, sticky="ew", padx=1, pady=1)
            options = ttk.Frame(center)
            options.grid(row=3, column=0, sticky="ew", pady=(5, 0))
            ttk.Checkbutton(options, text="Selected parts only", variable=self.only_sel, command=lambda: self.render(reuse=True)).pack(side="left")
            ttk.Checkbutton(options, text="Game-visible parts only", variable=self.engine_only, command=lambda: self.render(reuse=True)).pack(side="left", padx=(10, 0))
            ttk.Label(options, text="Lighting").pack(side="left", padx=(14, 0))
            lb = ttk.Combobox(options, textvariable=self.lights, values=LIGHT_MODES, state="readonly", width=22)
            lb.pack(side="left", padx=(6, 0))
            lb.bind("<<ComboboxSelected>>", lambda _e: self.render(reuse=True))
            self.gl_row = ttk.Frame(center)
            self.gl_row.grid(row=4, column=0, sticky="ew", pady=(5, 0))
            ttk.Checkbutton(self.gl_row, text="GPU viewport", variable=self.gpu_on, command=self._gpu_toggled).pack(side="left")
            ttk.Label(self.gl_row, text="Shading").pack(side="left", padx=(12, 0))
            sb = ttk.Combobox(self.gl_row, textvariable=self.shading, values=list(GLV.SHADINGS) if GLV else [], state="readonly", width=30)
            sb.pack(side="left", padx=(6, 0))
            sb.bind("<<ComboboxSelected>>", lambda _e: self._gl_look())
            for txt, var in (("Reflections", self.refl_on), ("Baked AO", self.ao_on), ("Perspective", self.persp_on)):
                ttk.Checkbutton(self.gl_row, text=txt, variable=var, command=self._gl_look).pack(side="left", padx=(10, 0))
            self.gl_info = ttk.Label(center, text="", foreground="#8f9eb2")
            self.gl_info.grid(row=6, column=0, sticky="ew")
            self.warn = ttk.Label(center, text="", wraplength=520, foreground=ORANGE, justify="left")
            self.warn.grid(row=5, column=0, sticky="ew", pady=(4, 0))
            self._build_gl(center)
            if not wpk_view:
                self.warn.configure(text="wpk_view.py is missing: preview unavailable; editing still works.")

            # ---------------- right: properties inspector + model tools
            right.columnconfigure(0, weight=1)
            props = ttk.LabelFrame(right, text="PROPERTIES", padding=7)
            props.grid(row=0, column=0, sticky="nsew")
            props.columnconfigure(0, weight=1)
            self.p_name = tk.StringVar()
            self.p_sub = tk.StringVar()
            self.p_stats = tk.StringVar()
            self.p_size = tk.StringVar()
            self.p_note = tk.StringVar()
            self.p_vis = tk.BooleanVar(value=True)
            self.p_empty = ttk.Label(props, text="Select a part in the browser or click it in the viewport.", foreground="#8f9eb2",
                                     wraplength=250, justify="left")
            self.p_empty.grid(row=0, column=0, sticky="w")
            pf = ttk.Frame(props)
            self.p_frame = pf
            pf.columnconfigure(0, weight=1)
            ttk.Label(pf, textvariable=self.p_name, font=("TkDefaultFont", 11, "bold"), wraplength=250).grid(row=0, column=0, sticky="w")
            ttk.Label(pf, textvariable=self.p_sub, foreground="#8f9eb2", wraplength=250, justify="left").grid(row=1, column=0, sticky="w")
            ttk.Label(pf, textvariable=self.p_stats, wraplength=250, justify="left").grid(row=2, column=0, sticky="w", pady=(6, 0))
            ttk.Label(pf, textvariable=self.p_size, wraplength=250, justify="left", foreground="#8f9eb2").grid(row=3, column=0, sticky="w")
            self.p_check = ttk.Checkbutton(pf, text="Visible in preview", variable=self.p_vis, command=self._props_visible)
            self.p_check.grid(row=4, column=0, sticky="w", pady=(6, 0))
            ttk.Label(pf, text="Materials (per draw)", font=("TkDefaultFont", 9, "bold")).grid(row=5, column=0, sticky="w", pady=(8, 2))
            self.mat_tree = ttk.Treeview(pf, columns=("tex", "blend", "tris"), show="headings", height=4, selectmode="none")
            for c, title, width in (("tex", "Texture", 120), ("blend", "Blend", 60), ("tris", "Tris", 50)):
                self.mat_tree.heading(c, text=title)
                self.mat_tree.column(c, width=width, anchor="w" if c == "tex" else "e", stretch=(c == "tex"), minwidth=40)
            self.mat_tree.grid(row=6, column=0, sticky="ew")
            ttk.Label(pf, text="Texture references", font=("TkDefaultFont", 9, "bold")).grid(row=7, column=0, sticky="w", pady=(8, 2))
            self.tex_tree = ttk.Treeview(pf, columns=("name", "size", "status"), show="headings", height=4, selectmode="none")
            for c, title, width in (("name", "Texture", 100), ("size", "Size", 58), ("status", "Status", 90)):
                self.tex_tree.heading(c, text=title)
                self.tex_tree.column(c, width=width, anchor="w", stretch=(c == "name"), minwidth=40)
            self.tex_tree.tag_configure("bad", foreground="#f85149")
            self.tex_tree.bind("<Double-1>", self._props_tex_open)
            self.tex_tree.grid(row=8, column=0, sticky="ew")
            ttk.Label(pf, textvariable=self.p_note, foreground="#8f9eb2", wraplength=250, justify="left").grid(row=9, column=0, sticky="w", pady=(6, 0))

            model_tools = ttk.LabelFrame(right, text="Model tools", padding=6)
            model_tools.grid(row=1, column=0, sticky="ew", pady=(8, 0))
            for col in range(2):
                model_tools.columnconfigure(col, weight=1)
            self.b_tex = ttk.Button(model_tools, text="Textures…", command=self.textures, state="disabled")
            self.b_shadow = ttk.Button(model_tools, text="Rebuild shadow", command=self.shadow, state="disabled")
            self.b_gloss = ttk.Button(model_tools, text="Gloss…", command=self.gloss_dialog, state="disabled")
            self.b_lamps = ttk.Button(model_tools, text="Fix headlights", command=self.fix_lamps, state="disabled")
            self.b_win = ttk.Button(model_tools, text="Clear windows", command=self.fix_windows, state="disabled")
            self.b_plc = ttk.Button(model_tools, text="Import arcade car…", command=self.plc, state="disabled")
            self.b_part = ttk.Button(model_tools, text="Part from arcade…", command=self.plc_part, state="disabled")
            self.b_fbx = ttk.Button(model_tools, text="Convert FBX…", command=self.convert_fbx)
            self.b_livery = ttk.Button(model_tools, text="Livery…", command=self.livery_dialog)
            self.b_palette = ttk.Button(model_tools, text="Paint colours…", command=self.palette_dialog)
            self.b_arcrebuild = ttk.Button(model_tools, text="Rebuild from arcade…", command=self.arcade_rebuild_dialog)
            for i, b in enumerate((self.b_tex, self.b_shadow, self.b_gloss, self.b_lamps, self.b_win,
                                   self.b_plc, self.b_part, self.b_fbx, self.b_livery,
                                   self.b_palette, self.b_arcrebuild)):
                b.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)

            # ---------------- bottom: operation log
            logf = ttk.Frame(shell)
            logf.grid(row=3, column=0, sticky="ew", pady=(8, 0))
            logf.columnconfigure(0, weight=1)
            lh = ttk.Frame(logf)
            lh.grid(row=0, column=0, sticky="ew")
            ttk.Label(lh, text="OPERATION LOG", font=("TkDefaultFont", 10, "bold")).pack(side="left")
            self.log_counts = tk.StringVar()
            ttk.Label(lh, textvariable=self.log_counts, foreground="#8f9eb2").pack(side="left", padx=(10, 0))
            self.log_toggle = ttk.Button(lh, text="Hide", width=6, command=self._toggle_log)
            self.log_toggle.pack(side="right")
            ttk.Button(lh, text="Clear", width=6, command=self.log_clear).pack(side="right", padx=(0, 5))
            ttk.Button(lh, text="Copy all", width=8, command=self.log_copy).pack(side="right", padx=(0, 5))
            self.progress = ttk.Progressbar(lh, orient="horizontal", length=160, mode="determinate", maximum=100)
            self.progress.pack(side="right", padx=(0, 10))
            self.log_body = ttk.Frame(logf)
            self.log_body.grid(row=1, column=0, sticky="ew", pady=(3, 0))
            self.log_body.columnconfigure(0, weight=1)
            self.log_text = tk.Text(self.log_body, height=5, wrap="word", state="disabled", relief="flat", padx=6, pady=4,
                                    background="#0f1620", foreground="#c9d4e3", font="TkFixedFont")
            lsb = ttk.Scrollbar(self.log_body, orient="vertical", command=self.log_text.yview)
            self.log_text.configure(yscrollcommand=lsb.set)
            self.log_text.grid(row=0, column=0, sticky="ew")
            lsb.grid(row=0, column=1, sticky="ns")
            for lv, col in LEVEL_COLOUR.items():
                self.log_text.tag_configure(lv, foreground=col)

            footer = ttk.Frame(shell, padding=(0, 8, 0, 0))
            footer.grid(row=4, column=0, sticky="ew", pady=(7, 0))
            ttk.Separator(footer).pack(fill="x", pady=(0, 7))
            self.status.bar(footer, ttk, tk, side="left", fill="x", expand=True)
            self.b_save = ttk.Button(footer, text="Save to game", command=self.save, state="disabled", style="VPK.Primary.TButton")
            self.b_save.pack(side="right")
            self.b_saveas = ttk.Button(footer, text="Save as…", command=self.save_as, state="disabled")
            self.b_saveas.pack(side="right", padx=(0, 7))
            self.b_bake = ttk.Button(footer, text="Bake AO…", command=self.bake_ao, state="disabled")
            self.b_bake.pack(side="right", padx=(0, 7))

        def help(self):
            w = tk.Toplevel(root)
            w.title("Models (WPK) - how it works")
            t = tk.Text(w, wrap="word", width=96, height=34, padx=10, pady=8)
            t.insert("1.0", HELP)
            t.configure(state="disabled")
            t.pack(fill="both", expand=True)

        # ---------------------------------------------------------------- folders and the car list (remembered)
        def pref(self, key):
            try:
                return wpk_view.load_prefs().get(key) if wpk_view else None
            except Exception:
                return None

        def setpref(self, **kv):
            try:
                if wpk_view:
                    wpk_view.save_prefs(**kv)
            except Exception:
                pass

        def start(self):
            """folders and the last car come back from the last session"""
            cd = self.pref("models_cars_dir") or (getattr(app, "cars_dir", None) if app else None)
            if cd and os.path.isdir(cd):
                self.set_cars_dir(cd, save=False)
            ad = self.pref("models_arcade_dir")
            if ad and os.path.isdir(ad):
                self.set_arc_dir(ad, save=False)
            gd = self.pref("models_game_dir")
            if gd and os.path.isdir(gd):
                self.set_game_dir(gd, save=False)
            last = self.pref("models_last")
            if last and os.path.isfile(last):
                self.open_path(last)

        def _short(self, p):
            return p if len(p) <= 60 else "…" + p[-59:]

        def set_cars_dir(self, d, save=True):
            self.cars_dir = d
            self.cars_txt.set(self._short(d))
            if save:
                self.setpref(models_cars_dir=d)
            self.refresh_cars()

        def refresh_cars(self):
            self.cars = {}
            if self.cars_dir and os.path.isdir(self.cars_dir):
                for f in sorted(os.listdir(self.cars_dir)):
                    if f.lower().endswith(".wpk") and not re.search(r"_aero[ABC]\.wpk$", f, re.I):
                        self.cars[f[:-4]] = os.path.join(self.cars_dir, f)
            self.car_box.configure(values=list(self.cars), state="readonly" if self.cars else "disabled")
            cur = self.cur_car_name()
            self.car.set(cur if cur in self.cars else "")

        def cur_car_name(self):
            if not self.path:
                return ""
            return re.sub(r"_aero[ABC]$", "", os.path.splitext(os.path.basename(self.path))[0], flags=re.I)

        def browse_cars(self):
            d = filedialog.askdirectory(parent=root, title="Folder with the car .wpk files", initialdir=self.cars_dir)
            if d:
                self.set_cars_dir(d)

        def pick_car(self):
            name = self.car.get()
            fp = self.cars.get(name)
            if not fp or os.path.abspath(fp) == os.path.abspath(self.path or ""):
                return
            if not self.confirm_discard():
                self.car.set(self.cur_car_name())
                return
            want = re.search(r"_aero([ABC])\.wpk$", self.path or "", re.I)         # same kit again when the new car has it
            if want:
                alt = os.path.join(self.cars_dir, "%s_aero%s.wpk" % (name, want.group(1).upper()))
                if os.path.isfile(alt):
                    fp = alt
            self.open_path(fp)

        def set_arc_dir(self, d, save=True):
            self.arc_dir = d
            self.arc_txt.set(self._short(d))
            if save:
                self.setpref(models_arcade_dir=d)
            self.arcs = {}
            try:
                import plc_import
                roots = [d, os.path.join(d, "playerCar")]
                for base in roots:
                    if os.path.isdir(base):
                        for x in sorted(os.listdir(base)):
                            if x.upper().startswith("PLC_") and os.path.isfile(os.path.join(base, x, x + ".mdl")):
                                self.arcs[x[4:]] = os.path.join(base, x)
                        if self.arcs:
                            break
                if not self.arcs and os.path.isfile(os.path.join(d, os.path.basename(d) + ".mdl")):
                    self.arcs[os.path.basename(d)[4:] if os.path.basename(d).upper().startswith("PLC_") else os.path.basename(d)] = d
            except ImportError:
                pass

        def browse_arcade(self):
            d = filedialog.askdirectory(parent=root, title="Arcade data: the playerCar folder (holds the PLC_<car> folders)", initialdir=self.arc_dir)
            if d:
                self.set_arc_dir(d)
                if not self.arcs:
                    messagebox.showwarning("Arcade folder", "No PLC_<car> folders with a .mdl in there.", parent=root)

        def set_game_dir(self, d, save=True):
            self.game_dir = d
            self.game_txt.set(self._short(d))
            if save:
                self.setpref(models_game_dir=d)

        def browse_game(self):
            d = filedialog.askdirectory(parent=root, title="The Re3DX+ folder (holds src and tools)", initialdir=self.game_dir)
            if d:
                self.set_game_dir(d)

        def bake_ao(self):
            """bake <pack>.ao for the saved pack with the game's own bake_ao.c"""
            try:
                import ao_bake
            except ImportError:
                messagebox.showerror("Bake AO", "ao_bake.py is missing next to the tool.", parent=root)
                return
            if not ao_bake.source(self.game_dir) and not ao_bake.prebuilt():
                messagebox.showerror("Bake AO", "bake_ao.c is missing next to the tool.", parent=root)
                return
            if self.dirty:
                if not messagebox.askyesno("Bake AO", "The AO is baked from the saved file. Save the car first?", parent=root):
                    return
                self.save()
                if self.dirty:
                    return
            path, game = self.path, self.game_dir
            self.status.set("Baking the ambient occlusion … (a few seconds)")
            self.log("info", "Baking the ambient occlusion for %s …" % os.path.basename(path))
            res = {}

            def work():
                try:
                    res["ok"] = ao_bake.bake(game, path)
                except Exception as ex:
                    res["err"] = str(ex)

            def poll():
                if res:
                    self.say()
                    if "ok" in res:
                        self.log("success", "Bake AO written: %s" % res["ok"][0])
                        messagebox.showinfo("Bake AO", "Written %s\n\n%s" % (res["ok"][0], res["ok"][1][-300:]), parent=root)
                    else:
                        self.log("error", "Bake AO failed", res["err"])
                        messagebox.showerror("Bake AO", res["err"], parent=root)
                else:
                    parent.after(300, poll)
            threading.Thread(target=work, daemon=True).start()
            parent.after(300, poll)

        def ensure_arc(self):
            """the arcade folder is asked once, then remembered"""
            if not self.arcs:
                d = filedialog.askdirectory(parent=root, title="Arcade data: the playerCar folder (holds the PLC_<car> folders)", initialdir=self.arc_dir)
                if not d:
                    return False
                self.set_arc_dir(d)
            if not self.arcs:
                messagebox.showwarning("Arcade folder", "No PLC_<car> folders with a .mdl in %s." % (self.arc_dir or "that folder"), parent=root)
                return False
            return True

        def guess_arc(self):
            """the arcade car that matches the open .wpk, if any"""
            try:
                import plc_import
                g = plc_import.guess_folder(self.path, self.arc_dir) if self.path and self.arc_dir else None
            except Exception:
                g = None
            if g:
                for n, p in self.arcs.items():
                    if os.path.abspath(p) == os.path.abspath(g):
                        return n
            return None

        def get_arcade(self, name):
            import plc_import
            if not hasattr(self, "_arc_cache"):
                self._arc_cache = {}
            if name not in self._arc_cache:
                self._arc_cache[name] = plc_import.Arcade(self.arcs[name])
            return self._arc_cache[name]

        # ---------------------------------------------------------------- opening
        def prefs_dir(self):
            d = getattr(app, "cars_dir", None) if app else None
            if d and os.path.isdir(d):
                return d
            try:
                return wpk_view.load_prefs().get("wpk_dir") if wpk_view else None
            except Exception:
                return None

        def open_dialog(self):
            if not self.confirm_discard():
                return
            f = filedialog.askopenfilename(parent=root, title="Open a car .wpk", initialdir=self.prefs_dir(),
                                           filetypes=[("Car pack", "*.wpk"), ("All files", "*.*")])
            if f:
                self.open_path(f)

        def confirm_discard(self):
            return UI.discard_changes(messagebox, root, "this car", self.dirty)

        def open_path(self, f):
            try:
                self.pack = Pack.load(f)
            except EditError as ex:
                self.log("error", "Cannot open %s" % os.path.basename(f), str(ex))
                messagebox.showerror("Cannot open", str(ex), parent=root)
                return
            self.path = f
            self._model = self._T = None
            self.cam.fit()
            self.stock = stock_pack_of(f)
            self.undo, self.dirty = [], False
            self.added = set()
            self.hidden = {c.tag for c in self.pack.chunks if c.tag == WPK_SHADOW}
            if wpk_view:                                      # every variant but the first of a group starts hidden (no wing, no roll cage)
                self.hidden |= wpk_view.hidden_tags(c.tag for c in self.pack.chunks)
            else:
                for lo, hi in ((0x123, 0x125), (0x127, 0x129), (0x121, 0x121), (0x12A, 0x12D)):
                    self.hidden |= set(range(lo, hi + 1))
            d = os.path.dirname(os.path.abspath(f))
            self.setpref(wpk_dir=d, models_last=os.path.abspath(f))
            if not self.cars_dir or os.path.abspath(self.cars_dir) != d:
                self.set_cars_dir(d)
            else:
                self.refresh_cars()
            self.find_kits()
            self.fill()
            self.set_buttons()
            self.render()
            self.say()
            self._tex_sync()
            nt = sum(c.counts()[2] for c in self.pack.chunks if c.tag != WPK_SHADOW)
            self.log("success", "Opened %s: %d parts, %d triangles%s" % (
                os.path.basename(f), len(self.pack.chunks), nt, "" if self.stock is None and not self._borrowed() else
                "  (aero kit: textures borrowed from the stock pack)"))

        def _borrowed(self):
            return any(not t["raw"] for t in self.pack.tex)

        def find_kits(self):
            d = os.path.dirname(os.path.abspath(self.path))
            m = re.match(r"^(.+?)(?:_aero([ABC]))?\.wpk$", os.path.basename(self.path), re.I)
            stem = m.group(1) if m else ""
            self.kits = {}
            for k, suf in (("Stock car", ""), ("Aero kit A", "_aeroA"), ("Aero kit B", "_aeroB"), ("Aero kit C", "_aeroC")):
                fp = os.path.join(d, stem + suf + ".wpk")
                if os.path.isfile(fp):
                    self.kits[k] = fp
            self.kit_box.configure(values=list(self.kits), state="readonly" if len(self.kits) > 1 else "disabled")
            self.car.set(self.cur_car_name() if self.cur_car_name() in self.cars else self.car.get())
            self.kit.set(next((k for k, v in self.kits.items() if os.path.abspath(v) == os.path.abspath(self.path)), ""))

        def switch_kit(self):
            fp = self.kits.get(self.kit.get())
            if fp and os.path.abspath(fp) != os.path.abspath(self.path or ""):
                if self.confirm_discard():
                    self.open_path(fp)
                else:
                    self.find_kits()

        # ---------------------------------------------------------------- list
        def fill(self, keep=None):
            sel = keep if keep is not None else self.sel_tags()
            self.tree.delete(*self.tree.get_children())
            self.rows = WB.build_rows(self.pack, self.hidden, tag_name, engine_draws) if self.pack else []
            cats = [c for c, v in self.cat_vars.items() if v.get()]
            query = self.search.get().strip()
            shown = WB.filter_rows(self.rows, query, cats)
            filtering = bool(query or cats)
            for cat, rs in WB.group_rows(shown):
                self.tree.insert("", "end", iid="cat:" + cat, text="%s  (%d)" % (cat, len(rs)),
                                 values=("", sum(r.draws for r in rs), sum(r.tris for r in rs)),
                                 open=filtering or cat not in self.collapsed)
                for r in rs:
                    self.tree.insert("cat:" + cat, "end", iid=str(r.tag), text=r.label,
                                     values=("●" if r.visible else "○", r.draws, r.tris))
            if not self.pack:
                self._set_empty("No WPK loaded.\nUse Open WPK… to list a car's parts.")
            elif not shown:
                self._set_empty("No parts match the current search / filter." if filtering else "This pack has no parts.")
            else:
                self._set_empty("")
            ids = [str(t) for t in sel if self.tree.exists(str(t))]
            if ids:
                self.tree.selection_set(ids)
            self.update_props()
            self.say()

        def _set_empty(self, text):
            if text:
                self.empty.configure(text=text)
                self.empty.place(relx=0.5, rely=0.3, anchor="center")
            else:
                self.empty.place_forget()

        def _fold(self, closed):
            iid = self.tree.focus()
            if iid.startswith("cat:"):
                (self.collapsed.add if closed else self.collapsed.discard)(iid[4:])

        def _search_changed(self):
            if self._sjob:
                parent.after_cancel(self._sjob)
            self._sjob = parent.after(150, self.fill)

        def clear_filters(self):
            self.search.set("")
            for v in self.cat_vars.values():
                v.set(False)
            self.fill()

        def sel_tags(self):
            return [int(i) for i in self.tree.selection() if i.isdigit()]

        def select_tag(self, tag, additive=False):
            """select a part by tag (browser <- viewport); clears a search / filter that would hide it"""
            if not (self.pack and self.pack.chunk(tag) is not None):
                return
            if not self.tree.exists(str(tag)):
                self.search.set("")
                for v in self.cat_vars.values():
                    v.set(False)
                self.fill()
            cur = self.sel_tags()
            new = ([t for t in cur if t != tag] if tag in cur else cur + [tag]) if additive else [tag]
            self.tree.selection_set([str(t) for t in new])
            if str(tag) in [str(t) for t in new]:
                self.tree.see(str(tag))

        def on_select(self):
            ids = self.tree.selection()
            groups = [i for i in ids if i.startswith("cat:")]
            if groups:                                      # a group header selects its parts
                new = [i for i in ids if not i.startswith("cat:")]
                for g in groups:
                    new += list(self.tree.get_children(g))
                self.tree.selection_set(sorted(set(new), key=int))
                return
            self.set_buttons()
            self.update_props()
            self.draw_overlay()
            if self.only_sel.get():
                self.render(reuse=True)

        def _tree_click(self, e):
            if self.tree.identify_region(e.x, e.y) == "cell" and self.tree.identify_column(e.x) == "#1":
                iid = self.tree.identify_row(e.y)
                if iid.isdigit():
                    self.set_visible([int(iid)])
                    return "break"
            return None

        def set_visible(self, tags, vis=None):
            """show / hide parts in the preview (vis None = flip each). Display state only: the WPK is not touched."""
            for t in tags:
                want = (t in self.hidden) if vis is None else vis
                (self.hidden.discard if want else self.hidden.add)(t)
            self.fill()
            self.render(reuse=True)

        def set_buttons(self):
            have = self.pack is not None
            n = len(self.sel_tags()) if have else 0
            st = lambda ok: "normal" if ok else "disabled"
            self.b_export.configure(state=st(have))
            self.b_move.configure(state=st(have and n == 1))
            self.b_kits.configure(state=st(have and n >= 1 and bool(self.path)))
            self.b_replace.configure(state=st(have))
            self.b_vis.configure(state=st(n))
            self.b_del.configure(state=st(n))
            self.b_add.configure(state=st(have))
            self.b_tex.configure(state=st(have))
            self.b_gloss.configure(state=st(have))
            self.b_shadow.configure(state=st(have and self.pack.chunk(0) is not None))
            self.b_lamps.configure(state=st(have and self.pack.chunk(0) is not None))
            self.b_win.configure(state=st(have and self.pack.chunk(0) is not None))
            self.b_plc.configure(state=st(have))
            self.b_part.configure(state=st(have and n == 1))
            self.b_undo.configure(state=st(self.undo))
            self.b_save.configure(state=st(have))
            self.b_saveas.configure(state=st(have))
            self.b_bake.configure(state=st(have))

        def say(self, extra=""):
            if not self.pack:
                return
            nt = sum(c.counts()[2] for c in self.pack.chunks if c.tag != WPK_SHADOW)
            self.status.set("%s%s   %d triangles in %d parts%s" % (os.path.basename(self.path), " *" if self.dirty else "", nt,
                                                                   len(self.pack.chunks), ("   " + extra) if extra else ""))
            if extra:
                self.log("success" if extra.startswith("saved") else "info", "%s: %s" % (os.path.basename(self.path), extra))

        # ---------------------------------------------------------------- edits
        def snapshot(self):
            self.undo.append(copy.deepcopy((self.pack.tex, self.pack.chunks)))
            del self.undo[:-8]

        def edited(self, msg=""):
            self.dirty = True
            self.fill()
            self.set_buttons()
            self.render()
            self.say(msg)
            self._tex_sync()

        def do_undo(self):
            if self.undo:
                self.pack.tex, self.pack.chunks = self.undo.pop()
                self.edited("undone")

        # ---- live preview while a dialog is open: apply, look, then keep (OK) or put back (Cancel)
        def live_begin(self):
            self._base = copy.deepcopy((self.pack.tex, self.pack.chunks))
            self._base_dirty = self.dirty

        def live_apply(self, fn):
            """put the car back as it was, run fn() on it, redraw; returns the report or the error text"""
            self.pack.tex, self.pack.chunks = copy.deepcopy(self._base)
            self.dirty = True
            try:
                msg = fn()
                err = None
            except (EditError, ValueError, struct.error, IndexError, KeyError) as ex:
                self.pack.tex, self.pack.chunks = copy.deepcopy(self._base)
                msg, err = "", str(ex)
            self.hidden.discard(0)
            self.fill()
            self.render()
            return msg, err

        def live_cancel(self):
            if getattr(self, "_base", None) is not None:
                self.pack.tex, self.pack.chunks = self._base
                self.dirty = self._base_dirty
                self._base = None
                self.fill()
                self.set_buttons()
                self.render()

        def live_commit(self, msg):
            self.undo.append(self._base)
            del self.undo[:-8]
            self._base = None
            self.edited(msg)

        def toggle_vis(self):
            self.set_visible(self.sel_tags())

        def delete(self):
            tags = self.sel_tags()
            if not tags or not UI.confirm_destructive(
                    messagebox, root, "Delete", "Remove %d part%s from the car?" % (len(tags), "" if len(tags) == 1 else "s"),
                    ["%s (0x%x)" % (tag_name(t), t) for t in tags], "Nothing is saved until you Save.", undoable=True):
                return
            self.snapshot()
            for t in tags:
                self.pack.delete_chunk(t)
            self.edited("deleted")

        def add_part(self):
            AddPartDialog(self)

        def kits_dialog(self):
            tags = [t for t in self.sel_tags() if t not in (0, WPK_SHADOW)]
            if not tags or not self.path:
                messagebox.showinfo("Kits", "Select the part(s) to copy in the list (not the body).", parent=root)
                return
            if self.dirty and not messagebox.askyesno("Kits", "The car has unsaved changes. The copy uses what is in memory now (the other files are written, this one is not).\nContinue?", parent=root):
                return
            kits = kit_files(self.path)
            if not kits:
                messagebox.showinfo("Kits", "No other kit files of this car sit beside it (name_aeroA.wpk, name_aeroB.wpk, name_aeroC.wpk, name.wpk).", parent=root)
                return
            w = tk.Toplevel(root)
            w.title("Copy to other kits")
            w.transient(root)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="Copy %s into:" % ", ".join("%s (0x%x)" % (tag_name(t), t) for t in tags), font=("TkDefaultFont", 10, "bold"),
                      wraplength=380).pack(anchor="w")
            vs = []
            for lab, p in kits:
                v = tk.BooleanVar(value=True)
                vs.append(v)
                ttk.Checkbutton(f, text="%s   (%s)" % (lab, os.path.basename(p)), variable=v).pack(anchor="w", pady=(4, 0))
            ttk.Label(f, text="Each kit is its own file; a part lives only in the files you copy it to. The same number is used,\nwith its textures and paint copies. A part the kit already has there is replaced.\n"
                              "The old files are kept as .bak.", foreground="#8f9eb2", justify="left").pack(anchor="w", pady=(8, 0))

            def go():
                done = []
                src = os.path.basename(self.path)
                try:
                    for (lab, p), v in zip(kits, vs):
                        if not v.get():
                            continue
                        d = Pack.load(p)
                        copy_parts(self.pack, d, tags)
                        d.save(p)
                        done.append(lab)
                        self.log("success", "Copy to kits: %s -> %s  (%s)" % (src, os.path.basename(p), ", ".join("0x%x" % t for t in tags)))
                except (EditError, OSError, ValueError, struct.error) as ex:
                    self.log("error", "Copy to kits failed (written: %s)" % (", ".join(done) or "nothing"), str(ex))
                    messagebox.showerror("Kits", "%s\n(done so far: %s)" % (ex, ", ".join(done) or "nothing"), parent=w)
                    return
                w.destroy()
                messagebox.showinfo("Kits", "Copied into: %s" % (", ".join(done) or "nothing selected"), parent=root)
            bt = ttk.Frame(f)
            bt.pack(anchor="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=w.destroy).pack(side="right")
            ttk.Button(bt, text="Copy", command=go).pack(side="right", padx=(0, 8))

        def move_dialog(self):
            tags = self.sel_tags()
            if len(tags) != 1:
                messagebox.showinfo("Move", "Select one part in the list.", parent=root)
                return
            tag = tags[0]
            if tag == 0 or tag >= WPK_SHADOW:
                messagebox.showinfo("Move", "The body and the shadow cannot be renumbered.", parent=root)
                return
            ch = ["0x%x   %s%s" % (t, lab, "" if self.pack.chunk(t) is None else ("  (empty)" if not self.pack.chunk(t).batches else "  (in use)"))
                  for t, lab in drawn_choices(self.pack, tag) if t != tag]
            w = tk.Toplevel(root)
            w.title("Move to part number")
            w.transient(root)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="%s (0x%x)%s" % (tag_name(tag), tag, "" if engine_draws(tag) else "   * the engine does not draw this number"),
                      font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(f, text="New number").grid(row=1, column=0, sticky="w", pady=(10, 0))
            v = tk.StringVar(value=next((c for c in ch if "in use" not in c), ch[0] if ch else ""))
            ttk.Combobox(f, textvariable=v, values=ch, width=34).grid(row=1, column=1, padx=8, pady=(10, 0))
            ttk.Label(f, text="Pick a slot the engine draws (the list), or type a number like 0x127 or a name like wing_B.\n"
                              "The geometry and textures stay; only the number changes (Undo puts it back).\n"
                              "A free slot or an empty one is used; a slot with a part in it is refused.",
                      foreground="#8f9eb2", justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

            def go():
                try:
                    dst = dest_tag(self.pack, v.get())
                    if not engine_draws(dst) and not messagebox.askyesno("Move", "0x%x is a number the engine does not draw, so the part stays invisible in the game. Move it anyway?" % dst, parent=w):
                        return
                    self.snapshot()
                    move_part(self.pack, tag, dst)
                except EditError as ex:
                    if self.undo:
                        self.undo.pop()
                    self.log("error", "Move tag refused", str(ex))
                    messagebox.showerror("Move", str(ex), parent=w)
                    return
                w.destroy()
                self.edited("part 0x%x moved to 0x%x (%s)" % (tag, dst, tag_name(dst)))
            bt = ttk.Frame(f)
            bt.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=w.destroy).pack(side="right")
            ttk.Button(bt, text="Move", command=go).pack(side="right", padx=(0, 8))

        def shadow(self):
            try:
                self.snapshot()
                rebuild_shadow(self.pack)
            except EditError as ex:
                self.undo.pop()
                messagebox.showerror("Shadow", str(ex), parent=root)
                return
            self.edited("shadow rebuilt from the body")

        def fix_lamps(self):
            HeadlightsDialog(self)

        def fix_lamps_old(self):
            try:
                dry = fix_headlights(self.pack, dry=True)             # look first: nothing changes
            except EditError as ex:
                messagebox.showerror("Headlights", str(ex), parent=root)
                return
            if dry["covers"] and not messagebox.askyesno(
                    "Headlights", "%d triangles of the body would become clear glass (the solid dark cover over the lamps).\n\n"
                    "If the car has black trim right beside a lamp, some of it may be caught. The Lamps tab can show exactly which "
                    "triangles (\"Show what would turn to glass\", pink) and lets you make the rule stricter.\n\nApply it here?" % dry["covers"], parent=root):
                return
            try:
                self.snapshot()
                rep = fix_headlights(self.pack)
            except EditError as ex:
                self.undo.pop()
                messagebox.showerror("Headlights", str(ex), parent=root)
                return
            if not (rep["covers"] or rep["bulbs"]):
                self.undo.pop()
                messagebox.showinfo("Headlights", "\n".join(rep["notes"]), parent=root)
                return
            self.edited("headlights fixed: %d cover triangles now glass, %d bulb triangles in the garage view" % (rep["covers"], rep["bulbs"]))

        def fix_windows(self):
            WindowsDialog(self)

        # ---------------------------------------------------------------- export / replace
        def export(self):
            tags = set(self.sel_tags()) or None
            name = os.path.splitext(os.path.basename(self.path))[0] + ("_parts" if tags else "")
            f = filedialog.asksaveasfilename(parent=root, title="Export model", defaultextension=".obj", initialfile=name + ".obj",
                                             filetypes=[("Wavefront OBJ", "*.obj"), ("Autodesk FBX (binary)", "*.fbx")])
            if not f:
                return
            try:
                r = export_model(self.pack, f, tags, 0.1, self.stock)
            except (EditError, OSError) as ex:
                messagebox.showerror("Export", str(ex), parent=root)
                return
            messagebox.showinfo("Exported", "%d objects, %d triangles.\n\n%s\n\nIn the 3D program: metres, +Z front, +Y up.\nKeep the object and material names; bring the "
                                "file back with Replace with OBJ." % (r["objects"], r["triangles"], "\n".join(os.path.basename(x) for x in r["files"] + [os.path.splitext(f)[0] + "_tex/ (textures)"])),
                                parent=root)

        def replace(self):
            f = filedialog.askopenfilename(parent=root, title="Your model (OBJ or FBX)", filetypes=[("OBJ or FBX", "*.obj *.fbx"), ("Wavefront OBJ", "*.obj"), ("Autodesk FBX", "*.fbx"), ("All files", "*.*")])
            if f:
                if f.lower().endswith(".fbx"):
                    FbxReplaceDialog(self, f)
                else:
                    ReplaceDialog(self, f)

        # ---------------------------------------------------------------- FBX car -> WPK
        def convert_fbx(self):
            try:
                import fbx2wpk
            except ImportError:
                messagebox.showerror("Convert FBX", "fbx2wpk.py / fbx_io.py are missing next to the tool.", parent=root)
                return
            w = tk.Toplevel(root)
            w.title("Convert FBX car to WPK")
            w.transient(root)
            w.resizable(False, False)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            v_src, v_donor, v_out = tk.StringVar(), tk.StringVar(value=self.path or ""), tk.StringVar()
            v_skip, v_open, v_kits = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
            info = tk.StringVar(value="Pick the car's folder (the one with the .fbx and the PNG textures), or a folder with many cars.")
            q = queue.Queue()
            state = {"cancel": False, "busy": False}

            def row(r, label, var, cmd):
                ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3)
                ttk.Entry(f, textvariable=var, width=58).grid(row=r, column=1, padx=6)
                ttk.Button(f, text="Browse…", command=cmd).grid(row=r, column=2)

            def pick_src():
                d = filedialog.askdirectory(parent=w, title="Car folder (or a folder of cars)")
                if d:
                    v_src.set(d)
                    n = len(fbx2wpk.car_folders(d))
                    info.set("%d car%s found." % (n, "" if n == 1 else "s") if n else "No .fbx found in that folder.")
                    if n > 1 and not v_out.get():
                        v_out.set(os.path.join(d, "wpk_out"))

            def pick_donor():
                p = filedialog.askopenfilename(parent=w, title="Donor car .wpk (any stock car, e.g. maz_FD3S.wpk)",
                                               initialdir=self.prefs_dir(), filetypes=[("Car pack", "*.wpk")])
                if p:
                    v_donor.set(p)

            def pick_out():
                n = len(fbx2wpk.car_folders(v_src.get())) if v_src.get() else 0
                if n > 1:
                    d = filedialog.askdirectory(parent=w, title="Output folder")
                    if d:
                        v_out.set(d)
                else:
                    p = filedialog.asksaveasfilename(parent=w, title="Save the new car pack", defaultextension=".wpk",
                                                     filetypes=[("Car pack", "*.wpk")])
                    if p:
                        v_out.set(p)

            row(0, "Car folder", v_src, pick_src)
            row(1, "Donor .wpk", v_donor, pick_donor)
            row(2, "Output (file, or folder for many)", v_out, pick_out)
            ttk.Label(f, text="The donor gives the pack layout and shaders; its own geometry is replaced by the FBX's body, wheels and wings.\n"
                              "Many cars: one .wpk per car, named CarXXXXXX_<name>.wpk.", foreground="#8f9eb2").grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 2))
            ttk.Checkbutton(f, text="Skip cars already converted in the output folder", variable=v_skip).grid(row=4, column=0, columnspan=3, sticky="w")
            ttk.Checkbutton(f, text="Open the result when it is one car", variable=v_open).grid(row=5, column=0, sticky="w")
            ttk.Checkbutton(f, text="Also build aero kits A / B / C", variable=v_kits).grid(row=5, column=1, sticky="w")
            ttk.Label(f, textvariable=info, wraplength=520).grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 2))
            bar = ttk.Progressbar(f, length=520, mode="determinate")
            bar.grid(row=7, column=0, columnspan=3, pady=(2, 6))
            log = tk.Text(f, width=76, height=8, state="disabled", background="#f4f4f4")
            log.grid(row=8, column=0, columnspan=3)
            btns = ttk.Frame(f)
            btns.grid(row=9, column=0, columnspan=3, sticky="e", pady=(8, 0))
            b_go = ttk.Button(btns, text="Convert")
            b_go.pack(side="left", padx=(0, 6))
            b_close = ttk.Button(btns, text="Close", command=w.destroy)
            b_close.pack(side="left")

            def say(t):
                log.configure(state="normal")
                log.insert("end", t + "\n")
                log.see("end")
                log.configure(state="disabled")

            def work(src, donor, out, many, skip, kits):
                try:
                    if many:
                        n, bad, sk = fbx2wpk.convert_all(src, donor, out, log=lambda t: q.put(("log", t)),
                                                         progress=lambda a, b, t: q.put(("prog", a, b, t)),
                                                         cancelled=lambda: state["cancel"], skip_existing=skip, kits=kits)
                        q.put(("done", "%d converted, %d already there, %d failed%s." % (n, sk, len(bad), " (stopped)" if state["cancel"] else ""), None))
                    else:
                        rs = fbx2wpk.convert_with_kits(src, donor, out, None, False, lambda t: q.put(("log", t)), kits)
                        r = rs[0]
                        q.put(("log", "%s: %.1f MB, triangles per part: %s" % (os.path.basename(out), r["bytes"] / 1e6,
                                                                              ", ".join("%s %d" % (hex(k), v) for k, v in r["tris"].items()))))
                        for k in rs:
                            if k:
                                q.put(("log", "aero kit %s: %s" % ("ABC"[k - 1], os.path.basename(fbx2wpk.kit_path(out, k)))))
                        q.put(("done", "Done.", out))
                except Exception as ex:                      # noqa: BLE001 - shown to the person
                    q.put(("done", "Error: %s" % ex, None))

            def poll():
                try:
                    while True:
                        m = q.get_nowait()
                        if m[0] == "log":
                            say(m[1])
                        elif m[0] == "prog":
                            bar.configure(maximum=m[2], value=m[1])
                            info.set("%d / %d   %s" % (m[1], m[2], m[3]))
                        else:
                            state["busy"] = False
                            b_go.configure(text="Convert", command=go)
                            info.set(m[1])
                            bar.configure(mode="determinate")
                            bar.stop()
                            if m[2] and v_open.get() and w.winfo_exists() and self.confirm_discard():
                                self.open_path(m[2])
                            return
                except queue.Empty:
                    pass
                if w.winfo_exists():
                    w.after(120, poll)

            def go():
                src, donor, out = v_src.get().strip(), v_donor.get().strip(), v_out.get().strip()
                folders = fbx2wpk.car_folders(src) if src and os.path.isdir(src) else []
                if not folders:
                    messagebox.showerror("Convert FBX", "No .fbx found in the car folder.", parent=w)
                    return
                if not donor or not os.path.isfile(donor):
                    messagebox.showerror("Convert FBX", "Pick a donor car .wpk (any stock car pack).", parent=w)
                    return
                many = len(folders) > 1
                if not out:
                    messagebox.showerror("Convert FBX", "Pick where to save the result.", parent=w)
                    return
                state["cancel"], state["busy"] = False, True
                log.configure(state="normal"); log.delete("1.0", "end"); log.configure(state="disabled")
                if many:
                    bar.configure(mode="determinate", maximum=len(folders), value=0)
                else:
                    bar.configure(mode="indeterminate"); bar.start(12)
                    info.set("Converting… (a few seconds)")
                b_go.configure(text="Stop", command=lambda: (state.__setitem__("cancel", True), info.set("Stopping after this car…")))
                threading.Thread(target=work, args=(folders[0] if not many else src, donor, out, many, v_skip.get(), v_kits.get()), daemon=True).start()
                poll()

            b_go.configure(command=go)
            w.protocol("WM_DELETE_WINDOW", lambda: (state.__setitem__("cancel", True), w.destroy()))

        # ---------------------------------------------------------------- livery painter
        def livery_dialog(self):
            if self.pack is None:
                messagebox.showinfo("Livery", "Open a car first.", parent=root)
                return
            try:
                import livery
            except ImportError:
                messagebox.showerror("Livery", "livery.py is missing next to the tool.", parent=root)
                return
            from tkinter import colorchooser
            w = tk.Toplevel(root)
            w.title("Livery")
            w.transient(root)
            f = ttk.Frame(w, padding=12)
            f.pack()
            rows = {}

            def add(r, key, label, color, extra=None):
                v_on = tk.BooleanVar(value=(key == "color"))
                v_col = tk.StringVar(value=color)
                if key != "color":
                    ttk.Checkbutton(f, text=label, variable=v_on).grid(row=r, column=0, sticky="w")
                else:
                    ttk.Label(f, text=label).grid(row=r, column=0, sticky="w")
                sw = tk.Label(f, width=4, background=color, relief="sunken")
                sw.grid(row=r, column=1, padx=6, pady=2)

                def pick():
                    c = colorchooser.askcolor(v_col.get(), parent=w)[1]
                    if c:
                        v_col.set(c)
                        sw.configure(background=c)
                ttk.Button(f, text="Colour…", command=pick).grid(row=r, column=2)
                rows[key] = (v_on, v_col)
                if extra:
                    extra(r)

            v_num = tk.StringVar(value="86")
            add(0, "color", "Body colour", "#d01818")
            add(1, "stripes", "Centre stripes (bonnet, roof, boot)", "#ffffff")
            add(2, "side", "Side band", "#ffffff")
            add(3, "roof", "Two-tone roof", "#101010")
            add(4, "gradient", "Fade towards the rear", "#ff8a00")
            add(5, "number", "Number on both doors:", "#ffffff", lambda r: ttk.Entry(f, textvariable=v_num, width=6).grid(row=r, column=3, padx=6))
            ttk.Label(f, text="Patterns follow the car's shape, so they run across panels and textures.\nThe baked shading is kept; the paint copies (p1..p5) are repainted too.",
                      foreground="#8f9eb2").grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 2))
            bt = ttk.Frame(f)
            bt.grid(row=7, column=0, columnspan=4, sticky="e", pady=(8, 0))

            def go():
                spec = {"color": rows["color"][1].get()}
                for k in ("stripes", "side", "roof", "gradient"):
                    if rows[k][0].get():
                        spec[k] = {"color": rows[k][1].get()}
                if rows["number"][0].get() and v_num.get().strip():
                    spec["number"] = {"text": v_num.get().strip()[:3], "color": rows["number"][1].get(), "bg": "#101010"}
                try:
                    self.snapshot()
                    r = livery.apply_livery(self.pack, spec, stock=getattr(self, "stock", None))
                except Exception as ex:                      # noqa: BLE001 - shown to the person
                    if self.undo:
                        self.undo.pop()
                    messagebox.showerror("Livery", str(ex), parent=w)
                    return
                w.destroy()
                self.edited("livery (%d textures)" % len(r["textures"]))
            ttk.Button(bt, text="Cancel", command=w.destroy).pack(side="right")
            ttk.Button(bt, text="Paint", command=go).pack(side="right", padx=(0, 8))

        # ---------------------------------------------------------------- gloss of a car that is already built
        def gloss_dialog(self):
            """change the shine of the car's materials in place: no model is re-imported.  Works on the selected parts, or the
            whole car when none is selected."""
            if self.pack is None:
                messagebox.showinfo("Gloss", "Open a car first.", parent=root)
                return
            if reflection_indices(self.pack)[0] is None:
                if messagebox.askyesno("Gloss", "This car has no reflection map (the 'alfa' texture), so paint cannot reflect.\n\n"
                                       "Copy the reflection maps from a stock car's .wpk into it now? (Undo takes them out again.)", parent=root):
                    p = filedialog.askopenfilename(parent=root, title="A stock car's .wpk (it holds the reflection map)",
                                                   filetypes=[("Car pack", "*.wpk"), ("All files", "*.*")])
                    if p:
                        try:
                            donor = Pack.load(p)
                            self.snapshot()
                            n = add_reflection_maps(self.pack, donor)
                        except (EditError, OSError, ValueError, struct.error, IndexError) as ex:
                            messagebox.showerror("Gloss", str(ex), parent=root)
                            return
                        self.edited("added %d reflection map texture(s)" % n)
            sel = set(self.sel_tags())
            KEEP = "(keep)"
            looks = (KEEP,) + LOOK_NAMES[1:]
            groups = {}                                   # (texture, see-through bits) -> [vertices, {material kind: vertices}, {power: vertices}]
            for c in self.pack.chunks:
                if c.tag == WPK_SHADOW or (sel and c.tag not in sel):
                    continue
                for b in c.batches:
                    g = groups.setdefault((b.tex, b.flags & 3), [0, {}, {}])
                    n = len(b.V)
                    g[0] += n
                    k = preset_of(b)
                    g[1][k] = g[1].get(k, 0) + n
                    g[2][b.power] = g[2].get(b.power, 0) + n
            if not groups:
                messagebox.showinfo("Gloss", "There is nothing to change in that selection.", parent=root)
                return
            no_env = reflection_indices(self.pack)[0] is None
            self.live_begin()
            w = tk.Toplevel(root)
            w.title("Gloss")
            w.transient(root)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="Shine of the car's materials  (%s)" % ("the selected parts" if sel else "the whole car"),
                      font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
            ttk.Label(f, text="Glossy paint reflects the sky and the street and has a sharp highlight.  Gloss %: 0 = matte, 100 = mirror "
                              "(stock paint is about 20).  For a chrome or metal-looking body part use Glossy paint at 70-100.  "
                              "Metal / Glass use the car's glass mask, so they are offered only for glass, lamps and chrome trim.",
                      wraplength=560, justify="left", foreground="#8f9eb2").grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 6))
            if no_env:
                ttk.Label(f, text="This car still has no reflection map (an 'alfa' texture), so paint cannot reflect much.",
                          wraplength=560, justify="left", foreground="#f85149").grid(row=2, column=0, columnspan=4, sticky="w")
            ttk.Label(f, text="Material", font=("TkDefaultFont", 9, "bold")).grid(row=3, column=0, sticky="w")
            ttk.Label(f, text="Now", font=("TkDefaultFont", 9, "bold")).grid(row=3, column=1, sticky="w", padx=8)
            ttk.Label(f, text="Look", font=("TkDefaultFont", 9, "bold")).grid(row=3, column=2, sticky="w")
            ttk.Label(f, text="Gloss %", font=("TkDefaultFont", 9, "bold")).grid(row=3, column=3, sticky="w", padx=8)
            rows = {}
            order = sorted(groups, key=lambda k: -groups[k][0])
            for r, key in enumerate(order[:24], start=4):
                tex, see = key
                nm = _stem(self.pack.tex[tex]["name"]) if tex != NO_TEX and tex < len(self.pack.tex) else "(no texture)"
                cur = max(groups[key][1].items(), key=lambda kv: kv[1])[0]
                pw = max(groups[key][2].items(), key=lambda kv: kv[1])[0]
                now = {"paint": "paint, gloss %d%%" % (100 - int(round(pw / 100.0))), "flat": "flat", "glass": "glass", "metal": "metal", "add": "glow"}[cur]
                ttk.Label(f, text="%s%s  (%d verts)" % (nm, "  [see-through]" if see == 1 else "", groups[key][0])).grid(row=r, column=0, sticky="w")
                ttk.Label(f, text=now, foreground="#8f9eb2").grid(row=r, column=1, sticky="w", padx=8)
                lv = tk.StringVar(value=KEEP)
                gv = tk.StringVar(value=str(100 - int(round(pw / 100.0))) if cur == "paint" else "20")
                # Metal / Glass multiply the picture by the car's specular mask, which is drawn in the GLASS texture's layout: on a
                # body or trim texture it lands in the wrong places and the surface turns to a mess (glass is also see-through).
                # So they are offered only where they can work, and a see-through material stays see-through.
                special = cur in ("glass", "metal") or bool(re.search(r"glass|window|lens|chrome|metal|lamp|light|mirror", nm, re.I))
                allowed = (KEEP, LOOK_NAMES[3], LOOK_NAMES[2]) if see == 1 else looks if special else (KEEP, LOOK_NAMES[1], LOOK_NAMES[4])
                ttk.Combobox(f, textvariable=lv, values=allowed, width=16, state="readonly").grid(row=r, column=2, sticky="w")
                ttk.Entry(f, textvariable=gv, width=4).grid(row=r, column=3, sticky="w", padx=8)
                rows[key] = (lv, gv)
            if len(order) > 24:
                ttk.Label(f, text="… and %d smaller materials (unchanged)" % (len(order) - 24), foreground="#8f9eb2").grid(row=28, column=0, sticky="w")
            quick = ttk.Frame(f)
            quick.grid(row=29, column=0, columnspan=4, sticky="w", pady=(8, 0))
            allv = tk.StringVar(value="35")

            def all_paint():
                """every opaque textured material that is paint, or flat with a 'body' / 'paint' name, becomes glossy paint"""
                for key, (lv, gv) in rows.items():
                    tex, see = key
                    nm = self.pack.tex[tex]["name"].lower() if tex != NO_TEX and tex < len(self.pack.tex) else ""
                    cur = max(groups[key][1].items(), key=lambda kv: kv[1])[0]
                    if see == 0 and (cur == "paint" or (cur == "flat" and ("body" in nm or "paint" in nm))):
                        lv.set(LOOK_NAMES[1])
                        gv.set(allv.get())
            ttk.Button(quick, text="All body paint → glossy, gloss %", command=all_paint).pack(side="left")
            ttk.Entry(quick, textvariable=allv, width=4).pack(side="left", padx=6)
            pending = [None]

            def apply():
                n = 0
                for c in self.pack.chunks:
                    if c.tag == WPK_SHADOW or (sel and c.tag not in sel):
                        continue
                    for b in c.batches:
                        key = (b.tex, b.flags & 3)
                        if key not in rows:
                            continue
                        lv, gv = rows[key]
                        name = lv.get()
                        try:
                            g = max(0, min(100, int(float(gv.get()))))
                        except ValueError:
                            g = 20
                        if name == KEEP:
                            if preset_of(b) == "paint" and b.power != (100 - g) * 100:
                                b.power = (100 - g) * 100
                                n += 1
                            continue
                        kind = LOOK_KINDS[looks.index(name)]
                        fl, prog, env, spec, pw = self.pack.preset(kind)
                        b.flags = (b.flags & ~3) | (fl & 3)
                        b.prog = prog
                        if prog:
                            b.env = b.env if b.env != NO_TEX else env
                            b.spec = b.spec if b.spec != NO_TEX else spec
                        else:
                            b.env = b.spec = NO_TEX
                        b.power = (100 - g) * 100 if kind == "paint" else pw
                        n += 1
                return "gloss changed on %d draw(s)" % n

            def refresh(*_):
                if pending[0]:
                    w.after_cancel(pending[0])
                pending[0] = w.after(250, lambda: self.live_apply(apply))

            for lv, gv in rows.values():
                lv.trace_add("write", refresh)
                gv.trace_add("write", refresh)

            def ok():
                msg, err = self.live_apply(apply)
                if err:
                    messagebox.showerror("Gloss", err, parent=w)
                    return
                self.live_commit(msg)
                w.destroy()

            def cancel():
                self.live_cancel()
                w.destroy()
            w.protocol("WM_DELETE_WINDOW", cancel)
            bt = ttk.Frame(f)
            bt.grid(row=30, column=0, columnspan=4, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=cancel).pack(side="right")
            ttk.Button(bt, text="Apply", command=ok).pack(side="right", padx=(0, 8))

        # ---------------------------------------------------------------- the five in-game paint colours
        def palette_dialog(self):
            if self.pack is None:
                messagebox.showinfo("Paint colours", "Open a car first.", parent=root)
                return
            try:
                import livery
            except ImportError:
                messagebox.showerror("Paint colours", "livery.py is missing next to the tool.", parent=root)
                return
            from tkinter import colorchooser
            w = tk.Toplevel(root)
            w.title("Paint colours")
            w.transient(root)
            f = ttk.Frame(w, padding=12)
            f.pack()
            ttk.Label(f, text="The colours the game offers when you change the car's paint (paints 1 to 5).\n"
                              "The car's own colour stays the default. The baked shading, lamps, badges and trim are kept.",
                      foreground="#8f9eb2").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
            cols = []
            for i, c in enumerate(livery.DEFAULT_PALETTE):
                v = tk.StringVar(value=c)
                cols.append(v)
                ttk.Label(f, text="Paint %d" % (i + 1)).grid(row=i + 1, column=0, sticky="w")
                sw = tk.Label(f, width=6, background=c, relief="sunken")
                sw.grid(row=i + 1, column=1, padx=6, pady=2)

                def pick(v=v, sw=sw):
                    c = colorchooser.askcolor(v.get(), parent=w)[1]
                    if c:
                        v.set(c)
                        sw.configure(background=c)
                ttk.Button(f, text="Colour…", command=pick).grid(row=i + 1, column=2)
            bt = ttk.Frame(f)
            bt.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))

            def go():
                try:
                    self.snapshot()
                    r = livery.apply_palette(self.pack, [v.get() for v in cols], stock=getattr(self, "stock", None))
                except Exception as ex:                      # noqa: BLE001 - shown to the person
                    if self.undo:
                        self.undo.pop()
                    messagebox.showerror("Paint colours", str(ex), parent=w)
                    return
                w.destroy()
                self.edited("paint colours (%d textures)" % len(r["textures"]))
            ttk.Button(bt, text="Cancel", command=w.destroy).pack(side="right")
            ttk.Button(bt, text="Make the paints", command=go).pack(side="right", padx=(0, 8))

        # ---------------------------------------------------------------- whole packs from the arcade's own car data
        def arcade_rebuild_dialog(self):
            try:
                import arc2wpk
            except ImportError:
                messagebox.showerror("Rebuild from arcade", "arc2wpk.py is missing next to the tool.", parent=root)
                return
            w = tk.Toplevel(root)
            w.title("Rebuild from arcade")
            w.transient(root)
            f = ttk.Frame(w, padding=12)
            f.pack()
            v_car, v_game, v_donor, v_out, v_stock = (tk.StringVar() for _ in range(5))
            v_kits = tk.BooleanVar(value=True)
            q = queue.Queue()
            state = {"cancel": False}
            labels = [("Arcade car folder (PLC_xxx) or the playerCar folder", v_car, "dir"), ("Game data folder (has color, position, tire …)", v_game, "dir"),
                      ("Donor .wpk (any stock car)", v_donor, "file"), ("Stock packs folder (optional: each car's own stock pack is its donor)", v_stock, "dir"),
                      ("Output (file for one car, folder for many)", v_out, "any")]
            for r, (lab, var, kind) in enumerate(labels):
                ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w", pady=3)
                ttk.Entry(f, textvariable=var, width=52).grid(row=r, column=1, padx=6)

                def pick(var=var, kind=kind):
                    if kind == "dir":
                        d = filedialog.askdirectory(parent=w)
                    elif kind == "file":
                        d = filedialog.askopenfilename(parent=w, filetypes=[("Car pack", "*.wpk")])
                    else:
                        many = not os.path.isfile(os.path.join(v_car.get(), os.path.basename(v_car.get()) + ".mdl"))
                        d = filedialog.askdirectory(parent=w) if many else filedialog.asksaveasfilename(parent=w, defaultextension=".wpk", filetypes=[("Car pack", "*.wpk")])
                    if d:
                        var.set(d)
                ttk.Button(f, text="Browse…", command=pick).grid(row=r, column=2)
            ttk.Checkbutton(f, text="Also build aero kits A / B / C", variable=v_kits).grid(row=5, column=0, columnspan=3, sticky="w")
            info = tk.StringVar(value="Builds the body, mirrors, bonnets, wings, lamps, wheels and the five paints from the arcade car's own data.")
            ttk.Label(f, textvariable=info, wraplength=520, foreground="#8f9eb2").grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 2))
            bar = ttk.Progressbar(f, length=520, mode="determinate")
            bar.grid(row=7, column=0, columnspan=3, pady=(2, 6))
            log = tk.Text(f, width=76, height=7, state="disabled", background="#f4f4f4")
            log.grid(row=8, column=0, columnspan=3)
            bt = ttk.Frame(f)
            bt.grid(row=9, column=0, columnspan=3, sticky="e", pady=(8, 0))
            b_go = ttk.Button(bt, text="Rebuild")
            b_go.pack(side="left", padx=(0, 6))
            ttk.Button(bt, text="Close", command=lambda: (state.__setitem__("cancel", True), w.destroy())).pack(side="left")

            def say(t):
                log.configure(state="normal")
                log.insert("end", t + "\n")
                log.see("end")
                log.configure(state="disabled")

            def work(car, game, donor, out, stock, kits, many):
                try:
                    if many:
                        ok, bad, sk = arc2wpk.convert_all(car, donor, out, game, stock or None, kits, lambda t: q.put(("log", t)),
                                                          lambda a, b, c: q.put(("prog", a, b, c)), lambda: state["cancel"])
                        q.put(("done", "%d cars rebuilt, %d already there, %d failed." % (ok, sk, len(bad)), None))
                    else:
                        rs = arc2wpk.convert_with_kits(car, donor, out, game, kits, lambda t: q.put(("log", t)))
                        q.put(("done", "Done: %s" % ", ".join("kit " + k for k in rs), out))
                except Exception as ex:                      # noqa: BLE001
                    q.put(("done", "Error: %s" % ex, None))

            def poll():
                try:
                    while True:
                        m = q.get_nowait()
                        if m[0] == "log":
                            say(m[1])
                        elif m[0] == "prog":
                            bar.configure(maximum=m[2], value=m[1])
                            info.set("%d / %d   %s" % (m[1], m[2], m[3]))
                        else:
                            info.set(m[1])
                            bar.stop()
                            b_go.configure(text="Rebuild", command=go)
                            if m[2] and self.confirm_discard():
                                self.open_path(m[2])
                            return
                except queue.Empty:
                    pass
                if w.winfo_exists():
                    w.after(150, poll)

            def go():
                car, game, donor, out, stock = (v.get().strip() for v in (v_car, v_game, v_donor, v_out, v_stock))
                if not car or not os.path.isdir(car) or not game or not os.path.isdir(game):
                    messagebox.showerror("Rebuild from arcade", "Pick the arcade car folder and the game data folder.", parent=w)
                    return
                if not out or (not donor and not stock):
                    messagebox.showerror("Rebuild from arcade", "Pick a donor .wpk (or a stock packs folder) and the output.", parent=w)
                    return
                many = not os.path.isfile(os.path.join(car, os.path.basename(car) + ".mdl"))
                if not donor:
                    donor = next((os.path.join(stock, x) for x in sorted(os.listdir(stock)) if x.lower().endswith(".wpk") and "_aero" not in x.lower()), "")
                state["cancel"] = False
                if many:
                    bar.configure(mode="determinate", value=0)
                else:
                    bar.configure(mode="indeterminate")
                    bar.start(12)
                b_go.configure(text="Stop", command=lambda: (state.__setitem__("cancel", True), info.set("Stopping after this car…")))
                threading.Thread(target=work, args=(car, game, donor, out, stock, v_kits.get(), many), daemon=True).start()
                poll()
            b_go.configure(command=go)

        def plc(self):
            try:
                import plc_import
            except ImportError:
                messagebox.showerror("Import", "plc_import.py / arcade_mdl.py are missing next to the tool.", parent=root)
                return
            if not self.ensure_arc():
                return
            PlcDialog(self, plc_import)

        def plc_part(self):
            sel = self.sel_tags()
            if len(sel) != 1:
                return
            if sel[0] == 0:                      # the body needs windows, lights, plates too: the whole-kit import
                messagebox.showinfo("Body", "The body needs the whole kit (windows, headlights, tail lights, plates ...).\n"
                                    "Use Import arcade car… for it; this button is for bonnets, wings, mirrors and lamps.", parent=root)
                self.plc()
                return
            try:
                import plc_import
            except ImportError:
                messagebox.showerror("Import", "plc_import.py / arcade_mdl.py are missing next to the tool.", parent=root)
                return
            if not self.ensure_arc():
                return
            PartDialog(self, plc_import, sel[0])

        # ---------------------------------------------------------------- textures
        def textures(self, select=None):
            """open (or raise) the Textures and materials window; select = a texture name to show"""
            if not self.pack:
                return None
            d = self.tex_dlg
            if d is None or not d.alive():
                d = self.tex_dlg = TexDialog(self)
            else:
                d.win.lift()
            if select:
                d.select_name(select)
            return d

        def _tex_sync(self):
            """the pack changed (edit, undo, open, kit switch): bring an open Textures window up to date"""
            d = self.tex_dlg
            if d is not None and d.alive():
                d.refresh()

        def _props_tex_open(self, e):
            row = self.tex_tree.identify_row(e.y)
            if row:
                name = self.tex_tree.item(row)["values"][0]
                if name and not str(name).startswith("("):
                    self.textures(select=str(name))

        # ---------------------------------------------------------------- saving
        def save(self):
            self._save(self.path)

        def save_as(self):
            f = filedialog.asksaveasfilename(parent=root, defaultextension=".wpk", initialfile=os.path.basename(self.path),
                                             filetypes=[("Car pack", "*.wpk")])
            if f:
                self._save(f)

        def _save(self, f):
            empty = [c.tag for c in self.pack.chunks if not c.batches and c.tag in self.added]
            if empty:
                self.log("warning", "Save refused: empty part(s) %s" % ", ".join("0x%x" % t for t in empty))
                messagebox.showerror("Cannot save", "%s is still empty.\nSelect it and use Replace with OBJ… to give it a model, or remove it with Delete part."
                                     % ", ".join("%s (%d)" % (tag_name(t), t) for t in empty), parent=root)
                return
            try:
                st = self.pack.save(f)
            except (EditError, OSError) as ex:
                self.log("error", "Cannot save %s" % os.path.basename(f), str(ex))
                messagebox.showerror("Cannot save", str(ex), parent=root)
                return
            self.log("success", "Wrote %s%s" % (f, "  (previous file kept as %s.bak)" % os.path.basename(f) if st["bak"] else ""))
            self.dirty = False
            self.path = f
            self.find_kits()
            self.say("saved (%.2f MB)%s" % (st["bytes"] / 1e6, ", old file kept as .bak" if st["bak"] else ""))

        # ---------------------------------------------------------------- preview
        # ---------------------------------------------------------------- viewport (software rasteriser, drawn off the UI thread)
        # ---------------------------------------------------------------- GPU viewport (gl_viewport.py)
        def _build_gl(self, center):
            """make the OpenGL widget in the canvas's grid cell; the software canvas stays as the fallback"""
            self.gl_host = center
            want = (GLV is not None and not GLV_WHY and os.environ.get("VPK_VIEWPORT", "gl").lower() != "soft"
                    and self.pref("gpu_viewport") is not False)
            self.gpu_on.set(bool(want))
            if not want:
                self._set_gl_ui(False, GLV_WHY or ("switched off" if GLV else ""))
                return
            try:
                self.gl = GLV.create_viewport(center, self.cam, self._gl_failed, width=W_, height=H_, on_draw=self._gl_drawn)
            except Exception as ex:
                self.gl = None
                self._gl_failed("Cannot create the GPU viewport", traceback.format_exc())
                return
            w = self.gl.widget
            w.grid(row=1, column=0, sticky="nsew")
            self.canvas.grid_remove()
            self._gl_ok = True
            self._set_gl_ui(True)
            for ev, fn in (("<ButtonPress-1>", self._vp_press), ("<B1-Motion>", self._vp_motion), ("<ButtonRelease-1>", self._vp_release),
                           ("<Shift-ButtonPress-1>", self._vp_press_pan), ("<MouseWheel>", self._vp_wheel), ("<Button-4>", self._vp_wheel),
                           ("<Button-5>", self._vp_wheel), ("<Double-Button-1>", lambda _e: self.fit_view())):
                w.bind(ev, fn)
            for btn in (2, 3):
                w.bind("<ButtonPress-%d>" % btn, self._vp_press_pan)
                w.bind("<B%d-Motion>" % btn, self._vp_motion)
                w.bind("<ButtonRelease-%d>" % btn, self._vp_release)
            self._gl_look()

        def _set_gl_ui(self, on, why=""):
            try:
                if on:
                    self.gl_row.grid()
                    self.gl_info.configure(text="")
                else:
                    for ch in self.gl_row.winfo_children():
                        if ch.winfo_class() != "TCheckbutton" or ch.cget("text") != "GPU viewport":
                            ch.configure(state="disabled")
                    self.gl_info.configure(text=("Software viewport (%s)" % why) if why else "")
                    if not self._gl_ok:
                        self.gpu_on.set(False)
            except Exception:
                pass

        def _gl_drawn(self, gl):
            self._upp = gl.upp
            if gl.info and not self.gl_info.cget("text"):
                self.gl_info.configure(text=gl.info + ("  |  baked AO found" if gl.has_ao else ""))

        def _gl_look(self):
            if self.gl is not None:
                self.gl.set_look(self.shading.get(), self.refl_on.get(), self.ao_on.get(), self.persp_on.get())

        def _gl_failed(self, reason, detail=""):
            """the GPU path cannot work on this machine: go back to the software viewport, and say why"""
            gl, self.gl = self.gl, None
            self._gl_ok = False
            if gl is not None:
                try:
                    gl.widget.grid_remove()
                    gl.widget.destroy()
                except Exception:
                    pass
            try:
                self.canvas.grid()
            except Exception:
                pass
            self.gpu_on.set(False)
            first = (detail.strip().splitlines() or [reason])[-1]
            self.log("warning", "%s - using the software viewport" % reason, detail)
            self._set_gl_ui(False, "%s: %s" % (reason, first))
            if self.pack:
                self.render(reuse=True)
            else:
                self._draw_empty()

        def _gpu_toggled(self):
            if self.gpu_on.get() == (self.gl is not None):
                return
            if self.gpu_on.get():                        # back to the GPU: a new widget (the old one may be dead)
                if GLV is None or GLV_WHY:
                    self.gpu_on.set(False)
                    return
                self.setpref(gpu_viewport=True)
                self.canvas.grid_remove()
                try:
                    self.gl = None
                    self._build_gl(self.gl_host)
                except Exception:
                    self.gl = None
            else:
                self.setpref(gpu_viewport=False)
                gl, self.gl = self.gl, None
                if gl is not None:
                    gl.close()
                    gl.widget.grid_remove()
                    gl.widget.destroy()
                self._gl_ok = False
                self.canvas.grid()
                self.gl_info.configure(text="Software viewport (switched off)")
            if self.pack:
                self.render(reuse=True)
            else:
                self._draw_empty()

        def _shown_tags(self):
            """tags of the parts the viewport draws: visibility, Selected parts only, lighting mode, Game-visible only"""
            sel = set(self.sel_tags())
            show = {c.tag for c in self.pack.chunks if c.tag not in self.hidden}
            if self.only_sel.get() and sel:
                show = sel
            show = {t for t in show if light_shown(t, self.lights.get())}
            if self.engine_only.get():
                show = {t for t in show if engine_draws(t)}
            return show

        def _render_gl(self, reuse):
            if not (reuse and self._model is not None):
                try:
                    self._model = view_model(self.pack, self.stock)
                except Exception as ex:
                    self._model = None
                    self.warn.configure(text="Cannot draw: %s" % ex)
                    self.log("error", "Cannot build the preview: %s" % ex, traceback.format_exc())
                    return
                ao = os.path.splitext(self.path)[0] + ".ao" if self.path else None
                self.gl.set_model(self._model, ao)
            self.warn.configure(text="")
            self.gl.set_visible(self._shown_tags())
            self.gl.set_selection(self.sel_tags())

        def render(self, reuse=False, fast=False):
            """redraw the car.  reuse=True keeps the decoded model (orbit / pan / zoom / visibility / selection change nothing in it);
            any other call rebuilds it, so an edit is always drawn from the current pack.  fast=True draws at half size while
            the mouse is moving and schedules the full-size picture afterwards."""
            if not self.pack:
                self._draw_empty()
                return
            if self.gl is not None:
                if fast and reuse:                       # camera moves: the GPU just draws again
                    self.gl.request_draw()
                else:
                    self._render_gl(reuse)
                return
            if not wpk_view:
                return
            if self._final_job:
                parent.after_cancel(self._final_job)
                self._final_job = None
            if fast:
                self._final_job = parent.after(220, lambda: self.render(reuse=True))      # sharp picture once the mouse rests
                if self._busy and reuse and self._model is not None:
                    self._again = True               # a frame is being drawn: do NOT cancel it (that starved the screen while dragging);
                    return                           # the newest camera is drawn the moment it is done
            self._again = False
            self.gen += 1                            # (a new generation cancels the picture in flight and drops its result)
            gen = self.gen
            show = self._shown_tags()
            if not (reuse and self._model is not None):
                try:
                    self._model = view_model(self.pack, self.stock)
                except Exception as ex:
                    self._model = None
                    self.warn.configure(text="Cannot draw: %s" % ex)
                    self.log("error", "Cannot build the preview: %s" % ex, traceback.format_exc())
                    return
            model = self._model
            bmap = WB.batch_tags(model[1])
            yaw, pit = self.cam.yaw, self.cam.pitch
            w, h = max(self.canvas.winfo_width(), 300), max(self.canvas.winfo_height(), 200)
            if fast:                                 # keep a drag near 30 pictures a second: smaller while it is slow, sharper when fast
                if self._frame_ms > 60 and self._div < 5:
                    self._div += 1
                elif self._frame_ms < 25 and self._div > 1:
                    self._div -= 1
            div = self._div if fast else 1
            rw, rh = max(w // div, 150), max(h // div, 100)
            cx, cy, hw = WB.view_extent(model[1], yaw, pit, wpk_view.view_point, rw / rh)
            window, self._upp = self.cam.window(cx, cy, hw, w, h)
            self._busy = True
            self._wid += 1
            wid = self._wid

            def work():
                last = [-10]
                t0 = time.perf_counter()

                def step(done, total):
                    f = int(100 * done / max(total, 1))
                    if f - last[0] >= 4 or f >= 100:
                        last[0] = f
                        self.q.put((gen, "prog", f))
                try:
                    vis = [c for c in model[1] if c[8] in show]
                    g = wpk_view.rasterize((model[0], vis, model[2]), yaw, pit, rw, rh, step=step,
                                           cancelled=lambda: gen != self.gen, window=window)
                    if g is None:                                    # superseded by a newer picture
                        return
                    im = wpk_view.compose(g, model[0])
                    T = None if fast else WB.tag_map(g.M, bmap)       # picking / outline data only for the sharp picture
                    if im.size != (w, h):
                        im = im.resize((w, h), Image.NEAREST if fast else Image.BILINEAR)
                    if fast:
                        self._frame_ms = 0.5 * self._frame_ms + 0.5 * (time.perf_counter() - t0) * 1000.0
                except Exception:
                    self.q.put((gen, "err", traceback.format_exc()))
                    return
                self.q.put((gen, "img", (im, T)))

            def work_guard():
                try:
                    work()
                finally:
                    if wid == self._wid:
                        self._busy = False
            threading.Thread(target=work_guard, daemon=True).start()

        def pump(self):
            try:
                while True:
                    gen, kind, val = self.q.get_nowait()
                    if gen != self.gen:
                        continue
                    if kind == "prog":
                        self.progress["value"] = val
                    elif kind == "img":
                        im, T = val
                        self.photo = ImageTk.PhotoImage(im)
                        if T is not None:                                # a sharp picture: new pick map + selection outline
                            self._T, self._T_size = T, im.size
                            self.canvas.delete("all")
                            self._img_item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
                            self.draw_overlay()
                        else:                                            # a drag frame: swap the picture in place, nothing else
                            if self._img_item is None:
                                self.canvas.delete("all")
                                self._img_item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
                            else:
                                self.canvas.itemconfigure(self._img_item, image=self.photo)
                            self.canvas.delete("sel")
                        self.progress["value"] = 0
                        self.warn.configure(text="")
                    else:
                        self.progress["value"] = 0
                        lines = [ln for ln in str(val).strip().splitlines() if ln.strip()]
                        first = lines[-1] if lines else "unknown error"
                        self.warn.configure(text="Cannot draw the car: %s" % first)
                        self.log("error", "Cannot draw the car: %s" % first, str(val))
            except queue.Empty:
                pass
            if self._again and not self._busy and self.pack:
                self.render(reuse=True, fast=True)                      # the newest camera since the last frame started
            if parent.winfo_exists():
                parent.after(10 if (self._busy or self._again) else 80, self.pump)

        def _draw_empty(self):
            if self.gl is not None:
                self.gl.clear_model()
                self.gl.set_selection([])
                return
            self.canvas.delete("all")
            self._img_item = None
            self._T = None
            self.canvas.create_text(max(self.canvas.winfo_width(), 300) // 2, max(self.canvas.winfo_height(), 200) // 2,
                                    text="No car loaded.\nUse Open WPK… to see it here.", fill="#8f9eb2", justify="center")

        def _canvas_resized(self, _e=None):
            if self._cfg_job:
                parent.after_cancel(self._cfg_job)
            self._cfg_job = parent.after(200, lambda: self.render(reuse=True))

        def view_changed(self):
            vi = self.view.get()
            if vi < len(VIEWS):                                     # a fixed view: its angles, fitted
                self.cam.set_angles(*VIEWS[vi][1:])
                self.cam.fit()
            self.render(reuse=True)

        def fit_view(self):
            vi = self.view.get()
            self.cam.set_angles(*(VIEWS[vi][1:] if vi < len(VIEWS) else (-35.0, 14.0)))
            self.cam.fit()
            self.render(reuse=True)

        # ---- mouse: drag = orbit, right / Shift drag = pan, wheel = zoom, click = pick
        def _vp_press(self, e):
            self._drag = dict(x=e.x, y=e.y, x0=e.x, y0=e.y, moved=False, btn=1)

        def _vp_press_pan(self, e):
            self._drag = dict(x=e.x, y=e.y, x0=e.x, y0=e.y, moved=False, btn=2)

        def _vp_motion(self, e):
            d = self._drag
            if not d or not self.pack:
                return
            if d["btn"] == 1:
                if not d["moved"] and abs(e.x - d["x0"]) + abs(e.y - d["y0"]) < 4:        # a click, not yet a drag
                    return
                d["moved"] = True
                self.cam.orbit(e.x - d["x"], e.y - d["y"])
                self.view.set(FREE)
            else:
                d["moved"] = True
                self.cam.pan_px(e.x - d["x"], e.y - d["y"], self._upp)
            d["x"], d["y"] = e.x, e.y
            self.render(reuse=True, fast=True)

        def _vp_release(self, e):
            d, self._drag = self._drag, None
            if not d:
                return
            if d["moved"]:
                self.render(reuse=True)                                                  # full-size picture
            elif d["btn"] == 1:
                self.viewport_pick(e.x, e.y, additive=bool(e.state & 0x5))              # Ctrl or Shift = add / remove

        def _vp_wheel(self, e):
            if not self.pack:
                return "break"
            up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
            self.cam.zoom_by(1.2 if up else 1 / 1.2)
            self.render(reuse=True, fast=True)
            return "break"                         # the wheel is for the picture only: no other scroll area may follow it

        def viewport_pick(self, x, y, additive=False):
            """click in the picture -> select the part drawn under the cursor (the browser follows)"""
            if self.gl is not None:
                tag = self.gl.pick(x, y) if self.pack else None
            else:
                T = self._T
                if T is None or not self.pack:
                    return
                W, H = self._T_size
                tag = WB.pick_tag(T, x * T.shape[1] / max(W, 1), y * T.shape[0] / max(H, 1))
            if tag is None:
                if not additive:
                    self.tree.selection_remove(self.tree.selection())
                return
            self.select_tag(tag, additive)

        def draw_overlay(self):
            """outline + tint of the selected parts, built from the last picture's per-pixel part map (no re-render)"""
            if self.gl is not None:
                self.gl.set_selection(self.sel_tags() if self.pack else [])
                return
            self.canvas.delete("sel")
            self._ov = None
            if self._T is None or not self.pack:
                return
            arr = WB.selection_overlay(self._T, self.sel_tags())
            if arr is None:
                return
            im = Image.fromarray(arr)
            if im.size != self._T_size:
                im = im.resize(self._T_size, Image.NEAREST)
            self._ov = ImageTk.PhotoImage(im)
            self.canvas.create_image(0, 0, anchor="nw", image=self._ov, tags="sel")

        # ---------------------------------------------------------------- properties (read from the Pack; only visibility is writable)
        def update_props(self):
            tags = self.sel_tags() if self.pack else []
            info = WB.part_info(self.pack, tags, self.hidden, tag_name, engine_draws, self.stock) if self.pack else dict(count=0)
            if not info["count"]:
                self.p_frame.grid_remove()
                self.p_empty.configure(text="Select a part in the browser or click it in the viewport." if self.pack
                                       else "No WPK loaded.")
                self.p_empty.grid()
                return
            self.p_empty.grid_remove()
            self.p_frame.grid(row=1, column=0, sticky="ew")
            self.p_name.set(info["name"])
            if info["tag"] is not None:
                self.p_sub.set("Tag %d (0x%x)  ·  %s%s" % (info["tag"], info["tag"], info["category"],
                                                         "" if info["engine"] else "  ·  not drawn by the game engine"))
            else:
                self.p_sub.set("%s" % ", ".join("0x%x" % t for t in info["tags"][:8]) + (" …" if info["count"] > 8 else ""))
            self.p_stats.set("%d draw%s  ·  %s vertices  ·  %s triangles" % (
                info["draws"], "" if info["draws"] == 1 else "s", format(info["verts"], ","), format(info["tris"], ",")))
            if "size" in info:
                self.p_size.set("Size %.2f × %.2f × %.2f   Centre (%.2f, %.2f, %.2f)   [model units]" % (info["size"] + info["centre"]))
            else:
                self.p_size.set("No geometry.")
            self.p_vis.set(info["visible"])
            self.p_note.set(("Visibility is mixed across the selection.  " if info["mixed_visibility"] else "") +
                            "Read-only here. Use Part actions (Move tag, Replace OBJ, Delete) and Textures… to change data; "
                            "Visible only affects this preview.")
            self.mat_tree.delete(*self.mat_tree.get_children())
            multi = info["count"] > 1
            for m in info["materials"][:80]:
                self.mat_tree.insert("", "end", values=(("0x%x  " % m["tag"] if multi else "") + m["tex"], m["blend"], m["tris"]))
            self.tex_tree.delete(*self.tex_tree.get_children())
            fmts = {0: "565", 1: "4444", 2: "5551"}
            for t in info["textures"][:80]:
                size = "%d×%d %s" % (t["w"], t["h"], fmts.get(t["fmt"], "")) if t["w"] else "-"
                self.tex_tree.insert("", "end", values=(t["name"], size, t["status"]),
                                     tags=("bad",) if t["status"].startswith("MISSING") else ())

        def _props_visible(self):
            self.set_visible(self.sel_tags(), self.p_vis.get())

        # ---------------------------------------------------------------- operation log
        def log(self, level, msg, detail=""):
            e = self.oplog.add(level, msg, detail)
            try:
                t = self.log_text
                t.configure(state="normal")
                t.insert("end", WB.OpLog.format(e) + "\n", e["level"])
                t.configure(state="disabled")
                t.see("end")
                c = self.oplog.counts()
                self.log_counts.set("%d error%s   %d warning%s" % (c["error"], "" if c["error"] == 1 else "s",
                                                                   c["warning"], "" if c["warning"] == 1 else "s"))
            except (tk.TclError, AttributeError):
                pass

        def log_clear(self):
            self.oplog.clear()
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
            self.log_counts.set("")

        def log_copy(self):
            parent.clipboard_clear()
            parent.clipboard_append(self.oplog.text())
            self.warn.configure(text="")
            self.status.set("Operation log copied to the clipboard.")

        def _toggle_log(self):
            if self.log_body.winfo_ismapped():
                self.log_body.grid_remove()
                self.log_toggle.configure(text="Show")
            else:
                self.log_body.grid()
                self.log_toggle.configure(text="Hide")
            if self._sashes_set:
                self._save_layout()

        # ---------------------------------------------------------------- panel sizes
        def _layout_now(self):
            """(left width, right width) of the side panels as they are now, or None"""
            try:
                w = self._body.winfo_width()
                return self._body.sashpos(0), w - self._body.sashpos(1), w
            except tk.TclError:
                return None

        def _save_layout(self):
            now = self._layout_now()
            if now:
                self.setpref(models_layout=dict(left=int(now[0]), right=int(now[1]), log=bool(self.log_body.winfo_ismapped())))

        def _place_sashes(self, _e=None):
            """first time: the saved panel widths (clamped); later: keep the minimum sizes when the window is resized"""
            w = self._body.winfo_width()
            if w < 700:
                return
            if self._sashes_set:
                self._clamp_sashes(save=False)
                return
            left, right, log_open = UI.parse_layout(self.pref("models_layout"), w)
            try:
                self._body.sashpos(0, left)
                self._body.sashpos(1, w - right)
                self._sashes_set = True
            except tk.TclError:
                pass
            self._body.bind("<ButtonRelease-1>", self._clamp_sashes)
            if not log_open and self.log_body.winfo_ismapped():
                self._toggle_log()

        def _clamp_sashes(self, _e=None, save=True):
            """sensible minimum sizes: browser 220, viewport 360, inspector 240 (vpk_ui.MIN_PANES); the layout is remembered"""
            now = self._layout_now()
            if not now:
                return
            left, right, w = now
            l2, r2 = UI.clamp_panes(w, left, right)
            try:
                if (l2, r2) != (left, right):
                    self._body.sashpos(0, l2)
                    self._body.sashpos(1, w - r2)
            except tk.TclError:
                return
            if save:
                self._save_layout()

    class ReplaceDialog:
        """pick what the OBJ replaces, scale / rotation, and which texture each material uses"""

        def __init__(self, tab, objpath):
            self.t, self.objpath = tab, objpath
            self.w = w = tk.Toplevel(root)
            w.title("Replace with " + os.path.basename(objpath))
            w.transient(root)
            w.grab_set()
            self.f = ttk.Frame(w, padding=12)
            self.f.pack(fill="both", expand=True)
            sel = tab.sel_tags()
            self.target = tk.StringVar(value="sel" if sel else "names")
            self.scale = tk.StringVar(value="10")
            self.rot = tk.StringVar(value="0")
            self.flipv = tk.BooleanVar(value=True)
            self.flipf = tk.BooleanVar(value=False)
            self.shadow = tk.BooleanVar(value=False)
            f = self.f
            ttk.Label(f, text="Which part does it replace?", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=4, sticky="w")
            seltxt = ", ".join(tag_name(t) for t in sel[:3]) + ("…" if len(sel) > 3 else "") if sel else "(select a part in the list first)"
            r1 = ttk.Radiobutton(f, text="The selected part: " + seltxt, variable=self.target, value="sel", command=self.reparse)
            r1.grid(row=1, column=0, columnspan=4, sticky="w")
            if len(sel) != 1:
                r1.configure(state="disabled")
                self.target.set("names")
            ttk.Radiobutton(f, text="The parts named in the OBJ (t<part>_… from an Export)", variable=self.target, value="names",
                            command=self.reparse).grid(row=2, column=0, columnspan=4, sticky="w")
            ttk.Label(f, text="Scale").grid(row=3, column=0, sticky="w", pady=(10, 0))
            e = ttk.Entry(f, textvariable=self.scale, width=7)
            e.grid(row=3, column=1, sticky="w", pady=(10, 0))
            ttk.Label(f, text="10 = your OBJ is in metres, 1 = already in car units", foreground="#8f9eb2").grid(row=3, column=2, columnspan=2, sticky="w", pady=(10, 0))
            ttk.Label(f, text="Rotate").grid(row=4, column=0, sticky="w")
            ttk.Combobox(f, textvariable=self.rot, values=("0", "90", "180", "270"), width=5, state="readonly").grid(row=4, column=1, sticky="w")
            ttk.Label(f, text="degrees, if the model faces the wrong way (front must be +Z)", foreground="#8f9eb2").grid(row=4, column=2, columnspan=2, sticky="w")
            ttk.Checkbutton(f, text="Flip texture V (normal for OBJ from Blender)", variable=self.flipv).grid(row=5, column=0, columnspan=4, sticky="w")
            ttk.Checkbutton(f, text="Flip faces (the model looks inside out)", variable=self.flipf).grid(row=6, column=0, columnspan=4, sticky="w")
            ttk.Checkbutton(f, text="Rebuild the shadow from the body afterwards", variable=self.shadow).grid(row=7, column=0, columnspan=4, sticky="w")
            ttk.Button(f, text="Read the model", command=self.reparse).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Label(f, text="Look: Glossy paint for anything painted (also chrome-looking parts, at a high Gloss %).  Metal / Glass use the car's glass mask "
                              "and only suit glass, lamp and chrome-trim pieces mapped like the stock ones; on a body they look broken.",
                      wraplength=640, justify="left", foreground="#8f9eb2").grid(row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))
            self.mat_outer = ttk.Frame(f)
            self.mat_outer.grid(row=10, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
            f.rowconfigure(10, weight=1)
            self.mat_canvas = tk.Canvas(self.mat_outer, highlightthickness=0, height=250)
            self.mat_sb = ttk.Scrollbar(self.mat_outer, orient="vertical", command=self.mat_canvas.yview)
            self.mat_canvas.configure(yscrollcommand=self.mat_sb.set)
            self.mat_sb.pack(side="right", fill="y")
            self.mat_canvas.pack(side="left", fill="both", expand=True)
            self.mat_frame = ttk.Frame(self.mat_canvas)
            self.mat_win = self.mat_canvas.create_window((0, 0), window=self.mat_frame, anchor="nw")
            self.mat_frame.bind("<Configure>", lambda e: self.mat_canvas.configure(scrollregion=self.mat_canvas.bbox("all"),
                                                                                    width=self.mat_frame.winfo_reqwidth()))      # the table's full width, so no column is cut off

            def _wheel(e):
                under = w.winfo_containing(e.x_root, e.y_root)           # only while the pointer is over this table, not over a picture
                if under is None or not str(under).startswith(str(self.mat_canvas)):
                    return
                if e.num == 4:
                    d = -1
                elif e.num == 5:
                    d = 1
                else:
                    d = -1 if e.delta > 0 else 1
                self.mat_canvas.yview_scroll(d * 2, "units")
            w.bind_all("<MouseWheel>", _wheel)
            w.bind_all("<Button-4>", _wheel)
            w.bind_all("<Button-5>", _wheel)
            w.bind("<Destroy>", lambda e: (w.unbind_all("<MouseWheel>"), w.unbind_all("<Button-4>"), w.unbind_all("<Button-5>")) if e.widget is w else None)
            self.info = ttk.Label(f, text="", wraplength=640, justify="left")
            self.info.grid(row=11, column=0, columnspan=4, sticky="w", pady=(6, 0))
            bt = ttk.Frame(f)
            bt.grid(row=12, column=0, columnspan=4, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=w.destroy).pack(side="right")
            self.ok = ttk.Button(bt, text="Replace", command=self.go, state="disabled")
            self.ok.pack(side="right", padx=(0, 8))
            self.meshes, self.boxes = [], {}
            self.reparse()

        def tex_names(self):
            return ["(automatic)", "(none: untextured)"] + ["%d  %s" % (i, t["name"]) for i, t in enumerate(self.t.pack.tex)]

        def reparse(self):
            try:
                self.meshes = parse_obj(self.objpath, float(self.scale.get()), int(self.rot.get()), self.flipv.get(), self.flipf.get())
            except (EditError, ValueError) as ex:
                self.meshes = []
                self.info.configure(text=str(ex), foreground="#f85149")
                self.ok.configure(state="disabled")
                return
            for c in self.mat_frame.winfo_children():
                c.destroy()
            self.boxes = {}
            self.looks = {}
            ttk.Label(self.mat_frame, text="Materials in the model", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(self.mat_frame, text="Look", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=2, sticky="w")
            ttk.Label(self.mat_frame, text="Gloss %", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, sticky="w")
            mats = sorted({m.material for m in self.meshes})
            prev = {}
            for r, mt in enumerate(mats, start=1):
                n = sum(len(m.tris) for m in self.meshes if m.material == mt)
                ttk.Label(self.mat_frame, text="%s  (%d tris)" % (mt, n)).grid(row=r, column=0, sticky="w")
                v = tk.StringVar(value=self.tex_names()[0])
                cb = ttk.Combobox(self.mat_frame, textvariable=v, values=self.tex_names(), width=34, state="readonly")
                cb.grid(row=r, column=1, sticky="w", padx=8)
                self.boxes[mt] = v
                lv = tk.StringVar(value=LOOK_NAMES[0])
                gv = tk.StringVar(value="20")
                lc = ttk.Combobox(self.mat_frame, textvariable=lv, values=LOOK_NAMES, width=16, state="readonly")
                lc.grid(row=r, column=2, sticky="w", padx=(0, 8))
                ge = ttk.Entry(self.mat_frame, textvariable=gv, width=4, state="disabled")
                ge.grid(row=r, column=3, sticky="w", padx=(0, 8))
                lc.bind("<<ComboboxSelected>>", lambda _e, lv=lv, ge=ge: ge.configure(state="normal" if lv.get() == LOOK_NAMES[1] else "disabled"))
                self.looks[mt] = (lv, gv)
                bad = resolve_material(self.t.pack, mt)[0] is None
                if bad:
                    ttk.Label(self.mat_frame, text="not in the car: pick one", foreground="#f85149").grid(row=r, column=4, sticky="w")
            self.mat_canvas.yview_moveto(0)
            tris = sum(len(m.tris) for m in self.meshes)
            tags = sorted({m.tag for m in self.meshes if m.tag is not None})
            self.info.configure(text="%d triangles in %d pieces%s." % (tris, len(self.meshes), ("; named parts: " + ", ".join(tag_name(t) for t in tags)) if tags else ""),
                                foreground="#c9d4e3")
            self.ok.configure(state="normal")

        def texmap(self):
            names = self.tex_names()
            tm = {}
            for mt, v in self.boxes.items():
                s = v.get()
                if s == names[1]:
                    tm[mt] = NO_TEX
                elif s != names[0]:
                    tm[mt] = int(s.split()[0])
            return tm

        def apply_looks(self, tm):
            """rename the materials the person gave a Look: <name>__paint80 etc. (the car's own naming for a shader preset);
            returns the texture map re-keyed to the new names"""
            rename = {}
            for mt, (lv, gv) in self.looks.items():
                kind = LOOK_KINDS[LOOK_NAMES.index(lv.get())] if lv.get() in LOOK_NAMES else None
                if not kind:
                    continue
                base = split_material(mt)[0]
                if kind == "paint":
                    try:
                        g = max(0, min(100, int(float(gv.get()))))
                    except ValueError:
                        g = 20
                    rename[mt] = "%s__paint%d" % (base, 100 - g)      # the shader's env weight is 1 - power
                else:
                    rename[mt] = "%s__%s" % (base, kind)
            for m in self.meshes:
                if m.material in rename:
                    m.material = rename[m.material]
            return {rename.get(k, k): v for k, v in tm.items()}

        def go(self):
            t = self.t
            sel = t.sel_tags()
            unwrapped = False
            try:
                t.snapshot()
                try:
                    import uvgen
                    if uvgen.needs_unwrap(self.meshes):           # the model has no UVs: make some (box projection charts, packed)
                        self.meshes = uvgen.unwrap(self.meshes)
                        unwrapped = True
                except ImportError:
                    pass
                if self.target.get() == "sel":                    # every piece goes into the chosen part, whatever its name says
                    for m in self.meshes:
                        m.tag = sel[0]
                res = replace_parts(t.pack, self.meshes, texmap=self.apply_looks(self.texmap()))
                if self.shadow.get():
                    rebuild_shadow(t.pack)
            except EditError as ex:
                t.undo.pop()
                messagebox.showerror("Replace", str(ex), parent=self.w)
                return
            miss = sorted({x for _n, ms in res.values() for x in ms})
            self.w.destroy()
            t.edited("replaced %s" % ", ".join(tag_name(k) for k in res))
            if unwrapped:
                messagebox.showinfo("UVs", "The model had no UVs, so the tool made them (box-projection pieces packed into the texture).\n"
                                           "Export the UV template to paint on it.", parent=root)
            if miss:
                messagebox.showwarning("Materials", "The car does not know the material(s): %s\nThey were given the part's usual look (paint on a body part). "
                                       "To use another texture, pick it in the list of the Replace dialog, or name the material after it / <texture>__paint80." % ", ".join(miss), parent=root)

    class FbxReplaceDialog:
        """an FBX car: inspect detected parts, then choose the target and aero kit"""

        def __init__(self, tab, fbx):
            import fbx2wpk
            self.t, self.fbx, self.F = tab, fbx, fbx2wpk
            try:
                self.kits, self.parts = fbx2wpk.fbx_overview(fbx)
            except Exception as ex:                      # noqa: BLE001 - shown to the person
                messagebox.showerror("Replace", "Cannot read %s: %s" % (os.path.basename(fbx), ex), parent=root)
                return
            if not self.parts.get(0):
                messagebox.showerror("Replace", "No car body found in %s." % os.path.basename(fbx), parent=root)
                return
            self.w = w = tk.Toplevel(root)
            w.title("FBX import · " + os.path.basename(fbx))
            w.transient(root)
            w.grab_set()
            f = ttk.Frame(w, padding=14)
            f.pack(fill="both", expand=True)
            sel = tab.sel_tags()
            can_sel = len(sel) == 1 and sel[0] in self.parts
            self.target = tk.StringVar(value="sel" if can_sel else "car")
            self.kit = tk.StringVar(value="Stock")
            self.prune = tk.BooleanVar(value=True)
            self.flipf = tk.BooleanVar(value=False)
            self.clear = tk.BooleanVar(value=False)

            ttk.Label(f, text="Import from FBX", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
            ttk.Label(f, text=os.path.basename(fbx), foreground="#8f9eb2").grid(row=1, column=0, columnspan=3, sticky="w", pady=(1, 10))

            detected = ttk.LabelFrame(f, text="FBX → WPK part mapping (preview)", padding=8)
            detected.grid(row=2, column=0, columnspan=3, sticky="nsew")
            detected.columnconfigure(0, weight=1)
            detected.rowconfigure(0, weight=1)
            detected.rowconfigure(2, weight=1)
            self.map_tree = ttk.Treeview(detected, columns=("part", "mesh"), show="headings", height=6)
            self.map_tree.heading("part", text="WPK destination")
            self.map_tree.heading("mesh", text="FBX mesh / hierarchy path")
            self.map_tree.column("part", width=150, minwidth=125, stretch=False)
            self.map_tree.column("mesh", width=390, minwidth=240, stretch=True)
            map_y = ttk.Scrollbar(detected, orient="vertical", command=self.map_tree.yview)
            map_x = ttk.Scrollbar(detected, orient="horizontal", command=self.map_tree.xview)
            self.map_tree.configure(yscrollcommand=map_y.set, xscrollcommand=map_x.set)
            self.map_tree.grid(row=0, column=0, sticky="nsew")
            map_y.grid(row=0, column=1, sticky="ns")
            map_x.grid(row=1, column=0, sticky="ew")
            self.preview_canvas = tk.Canvas(detected, width=560, height=220, bg="#171b22", highlightthickness=1,
                                            highlightbackground="#343b46")
            self.preview_canvas.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
            self.preview_canvas.bind("<Configure>", lambda _e: self._draw_fbx_preview())
            self._preview_mesh_data = None
            self._refresh_mapping(0)

            target = ttk.LabelFrame(f, text="What should be replaced?", padding=9)
            target.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
            r1 = ttk.Radiobutton(target, text="Selected part only: " + (tag_name(sel[0]) if len(sel) == 1 else "select one part in the list first"),
                                 variable=self.target, value="sel")
            r1.grid(row=0, column=0, sticky="w")
            if not can_sel:
                r1.configure(state="disabled")
            ttk.Radiobutton(target, text="Whole car: body, wheels, wings and bonnets detected above",
                            variable=self.target, value="car").grid(row=1, column=0, sticky="w", pady=(4, 0))

            opts = ttk.Frame(f)
            opts.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
            ttk.Label(opts, text="Source variant").grid(row=0, column=0, sticky="w")
            kit_values = ["Stock"] + ["Aero " + "ABC"[k - 1] for k in self.kits]
            self.kit_box = ttk.Combobox(opts, textvariable=self.kit, values=kit_values, width=12, state="readonly")
            self.kit_box.grid(row=0, column=1, sticky="w", padx=(8, 16))
            self.kit_box.bind("<<ComboboxSelected>>", self._kit_changed)
            ttk.Label(opts, text="Preview updates when you change the source variant.", foreground="#8f9eb2").grid(row=0, column=2, sticky="w")

            ttk.Checkbutton(f, text="Remove unused textures after import (smaller WPK)", variable=self.prune).grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
            ttk.Checkbutton(f, text="Flip faces (only if the imported model appears inside out)", variable=self.flipf).grid(row=6, column=0, columnspan=3, sticky="w")
            ttk.Checkbutton(f, text="Also clear old lamps, mirrors, wing and bonnet options (whole-car mode only)", variable=self.clear).grid(row=7, column=0, columnspan=3, sticky="w")
            ttk.Label(f, text="PNG textures beside the FBX are imported when matched. Missing textures keep the destination part's existing look.",
                      wraplength=570, justify="left", foreground="#8f9eb2").grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 0))
            bt = ttk.Frame(f)
            bt.grid(row=9, column=0, columnspan=3, sticky="e", pady=(14, 0))
            ttk.Button(bt, text="Cancel", command=w.destroy).pack(side="right")
            ttk.Button(bt, text="Import", command=self.go).pack(side="right", padx=(0, 8))
            w.resizable(False, False)

        def _kit_changed(self, _event=None):
            kit = 0 if self.kit.get() == "Stock" else "ABC".index(self.kit.get()[-1]) + 1
            self._refresh_mapping(kit)

        def _refresh_mapping(self, kit):
            try:
                mapping = self.F.fbx_mapping(self.fbx, kit)
            except Exception as ex:  # noqa: BLE001 - shown in the preview rather than blocking the dialog
                for item in self.map_tree.get_children():
                    self.map_tree.delete(item)
                self.map_tree.insert("", "end", values=("Preview unavailable", str(ex)))
                return
            for item in self.map_tree.get_children():
                self.map_tree.delete(item)
            total = 0
            selected_paths = set()
            for tag, paths in sorted(mapping.items()):
                destination = tag_name(tag)
                for path in paths:
                    self.map_tree.insert("", "end", values=(destination, path))
                    selected_paths.add(path)
                    total += 1
            if not total:
                self.map_tree.insert("", "end", values=("No mapped parts", "Check the FBX hierarchy and naming"))
            try:
                import fbx_io
                self._preview_mesh_data = fbx_io.read_fbx(self.fbx, select=lambda path: path in selected_paths)
            except Exception as ex:  # preview must never block a valid import
                self._preview_mesh_data = None
                self.preview_canvas.delete("all")
                self.preview_canvas.create_text(12, 12, anchor="nw", fill="#d7a56b",
                                                text="3D preview unavailable\n" + str(ex), font=("TkDefaultFont", 10))
                return
            self._draw_fbx_preview()

        def _draw_fbx_preview(self):
            c = getattr(self, "preview_canvas", None)
            data = getattr(self, "_preview_mesh_data", None)
            if c is None:
                return
            c.delete("all")
            if not data:
                c.create_text(12, 12, anchor="nw", fill="#aeb8c6", text="3D model preview", font=("TkDefaultFont", 10))
                return
            import numpy as np
            P = np.asarray(data.get("P", []), dtype=float)
            groups = data.get("groups", {})
            if not len(P):
                c.create_text(12, 12, anchor="nw", fill="#aeb8c6", text="No mapped geometry to preview")
                return
            # Axonometric view; keep the renderer lightweight for large arcade FBXs.
            ang = np.radians(35.0)
            ca, sa = np.cos(ang), np.sin(ang)
            rot = np.array([[ca, 0, -sa], [sa * .45, .89, ca * .45]])
            Q = P @ rot.T
            lo, hi = Q.min(axis=0), Q.max(axis=0)
            span = np.maximum(hi - lo, 1e-6)
            width, height = max(c.winfo_width(), 240), max(c.winfo_height(), 140)
            scale = min((width - 36) / span[0], (height - 36) / span[1])
            xy = (Q - (lo + hi) / 2) * scale
            xy[:, 0] += width / 2
            xy[:, 1] = height / 2 - xy[:, 1]
            tris = []
            for faces in groups.values():
                for tri in faces:
                    ids = [int(v[0]) for v in tri]
                    pts = xy[ids]
                    depth = float(P[ids, 2].mean())
                    normal = np.cross(P[ids[1]] - P[ids[0]], P[ids[2]] - P[ids[0]])
                    light = abs(float(normal[1])) / (np.linalg.norm(normal) + 1e-9)
                    shade = int(82 + 105 * light)
                    color = "#%02x%02x%02x" % (shade, min(220, shade + 8), min(240, shade + 20))
                    tris.append((depth, pts, color))
            if len(tris) > 9000:
                step = int(np.ceil(len(tris) / 9000))
                tris = tris[::step]
            for _depth, pts, color in sorted(tris, key=lambda item: item[0]):
                c.create_polygon(*(float(v) for v in pts.reshape(-1)), fill=color, outline="#303946", width=0.35)
            c.create_text(10, height - 10, anchor="sw", fill="#aeb8c6",
                          text="Mapped FBX geometry · axonometric preview", font=("TkDefaultFont", 9))

        def go(self):
            t, F = self.t, self.F
            sel = t.sel_tags()
            only = {sel[0]} if self.target.get() == "sel" else None
            if only is not None and sel[0] not in self.parts:
                messagebox.showerror("FBX import", "The FBX does not contain the selected part.", parent=self.w)
                return
            kit = 0 if self.kit.get() == "Stock" else "ABC".index(self.kit.get()[-1]) + 1
            whole = only is None
            if whole:
                summary = "Replace the whole car using %s?\n\nDetected: %s\nSource: %s" % (
                    self.kit.get(), ", ".join(tag_name(k) for k in sorted(self.parts) if k) or "Body only", os.path.basename(self.fbx))
                if self.clear.get():
                    summary += "\n\nOld lamps, mirrors, wing and bonnet options will also be cleared."
                if not messagebox.askyesno("Confirm whole-car import", summary, icon="warning", parent=self.w):
                    return
            try:
                t.snapshot()
                r = F.fill_pack(t.pack, self.fbx, F.derive_name(os.path.dirname(self.fbx)), self.flipf.get(), lambda s: None, kit, only,
                                clear=(whole and self.clear.get()), prune=self.prune.get())
                if not r["tris"]:
                    raise EditError("The FBX has nothing for that part.")
            except Exception as ex:                      # noqa: BLE001 - shown to the person
                if t.undo:
                    t.undo.pop()
                messagebox.showerror("FBX import", str(ex), parent=self.w)
                return
            self.w.destroy()
            t.edited("replaced %s" % ", ".join(tag_name(k) for k in r["tris"]))
            if r["missing"]:
                messagebox.showwarning("Textures", "No PNG found for: %s\nThose pieces use the part's usual look." % ", ".join(r["missing"][:8]), parent=root)

    class PlcDialog:
        """which arcade car and aero kit becomes this .wpk's body; the preview follows the choice"""

        def __init__(self, tab, mod):
            self.t, self.mod, self.arc = tab, mod, None
            self.w = w = tk.Toplevel(root)
            w.title("Import arcade car")
            w.transient(root)
            w.protocol("WM_DELETE_WINDOW", self.cancel)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="Into the body of %s" % os.path.basename(tab.path), font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(f, text="Arcade car").grid(row=1, column=0, sticky="w", pady=(10, 0))
            names = list(tab.arcs)
            self.car = tk.StringVar(value=tab.guess_arc() or names[0])
            self.carbox = ttk.Combobox(f, textvariable=self.car, values=names, state="readonly", width=22)
            self.carbox.grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))
            self.carbox.bind("<<ComboboxSelected>>", lambda _e: self.load_car(reset_kit=True))
            ttk.Label(f, text="Arcade aero kit").grid(row=2, column=0, sticky="w", pady=(6, 0))
            self.kit = tk.StringVar()
            self.cb = ttk.Combobox(f, textvariable=self.kit, values=[], state="readonly", width=22)
            self.cb.grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
            self.cb.bind("<<ComboboxSelected>>", lambda _e: self.preview())
            self.inner = tk.BooleanVar(value=True)
            self.shadow = tk.BooleanVar(value=True)
            self.bonnet = tk.BooleanVar(value=True)
            ttk.Checkbutton(f, text="Include the interior (seats, dash: windows look dark, not see-through)", variable=self.inner, command=self.preview).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Checkbutton(f, text="Rebuild the shadow from the new body", variable=self.shadow, command=self.preview).grid(row=4, column=0, columnspan=2, sticky="w")
            ttk.Checkbutton(f, text="Fit the kit's own bonnet (replaces Bonnet A)", variable=self.bonnet, command=self.preview).grid(row=5, column=0, columnspan=2, sticky="w")
            ttk.Label(f, text="The picture on the right shows what you pick (Cancel puts the car back).\nBody, glass, headlights, boot, plates, exhaust replace this file's Body;\nwheels, lamps, mirrors, wings and bonnets stay as they are.",
                      foreground="#8f9eb2", justify="left").grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
            self.msg = ttk.Label(f, text="", foreground="#f85149", wraplength=360, justify="left")
            self.msg.grid(row=7, column=0, columnspan=2, sticky="w")
            bt = ttk.Frame(f)
            bt.grid(row=8, column=0, columnspan=2, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=self.cancel).pack(side="right")
            ttk.Button(bt, text="Import", command=self.go).pack(side="right", padx=(0, 8))
            tab.live_begin()
            self.last = ""
            self.load_car(reset_kit=True)

        def load_car(self, reset_kit):
            try:
                self.arc = self.t.get_arcade(self.car.get())
                kits = self.arc.kits()
                if not kits:
                    raise EditError("%s has no aero kits." % self.car.get())
            except (EditError, OSError, ValueError, struct.error, KeyError) as ex:
                self.arc = None
                self.cb.configure(values=[])
                self.kit.set("")
                self.t.live_apply(lambda: "")
                self.msg.configure(text="Cannot read that car: %s" % ex)
                return
            labels = ["%s%s" % (k, "  (standard body)" if k == "N" else "") for k in kits]
            self.cb.configure(values=labels)
            own = re.search(r"_aero([ABC])\.wpk$", self.t.path or "", re.I)
            first = own.group(1).upper() if own and own.group(1).upper() in kits else kits[0]
            cur = self.kitletter() if not reset_kit and self.kit.get() else None
            want = cur if cur in kits else first
            self.kit.set("%s%s" % (want, "  (standard body)" if want == "N" else ""))
            self.preview()

        def kitletter(self):
            return self.kit.get().split()[0] if self.kit.get() else ""

        def preview(self):
            if self.arc is None:
                return
            self.last, err = self.t.live_apply(lambda: self.mod.import_kit(self.t.pack, self.arc, self.kitletter(), inner=self.inner.get(), shadow=self.shadow.get(), bonnet=self.bonnet.get()))
            self.msg.configure(text=err or "")

        def cancel(self):
            self.t.live_cancel()
            self.w.destroy()

        def go(self):
            if self.arc is None:
                return
            if self.arc.foreign and not messagebox.askyesno(
                    "Import", "This folder holds the model of %s, not of %s (the game data has a stand-in there).\nImport it anyway?"
                    % (self.arc.prefix[4:].upper(), self.arc.name[4:]), parent=self.w):
                return
            if not self.last:
                self.preview()
                if not self.last:
                    return
            self.w.destroy()
            self.t.live_commit(self.last)

    class AddPartDialog:
        """give the car a new part: type its name (bonnet_B ...), it takes the free slot that name stands for"""

        EMPTY = "(empty: give it a model with Replace with OBJ…)"

        def __init__(self, tab):
            self.t = tab
            self.w = w = tk.Toplevel(root)
            w.title("Add part")
            w.transient(root)
            w.grab_set()
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            sel = tab.sel_tags()
            name, self.note = suggest_name(tab.pack, sel[0]) if len(sel) == 1 else ("", "")
            self.name = tk.StringVar(value=name)
            ttk.Label(f, text="Name of the new part", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
            e = ttk.Entry(f, textvariable=self.name, width=24)
            e.grid(row=1, column=0, sticky="w", pady=(4, 0))
            ttk.Label(f, text="e.g. bonnet_B  wing_C  mirror_B  boot_A  exhaust_B  rollcage_A  roofitem_3\nor a tag number: 0x127 or 295", foreground="#8f9eb2",
                      justify="left").grid(row=1, column=1, sticky="w", padx=10, pady=(4, 0))
            self.info = ttk.Label(f, text="", wraplength=440, justify="left")
            self.info.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Label(f, text="Start from", font=("TkDefaultFont", 10, "bold")).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
            self.src_tags = [c.tag for c in tab.pack.chunks if c.tag != WPK_SHADOW]
            names = [self.EMPTY] + ["a copy of %s (%d)" % (tag_name(t), t) for t in self.src_tags]
            self.src = tk.StringVar(value=names[1 + self.src_tags.index(sel[0])] if len(sel) == 1 and sel[0] in self.src_tags else names[0])
            ttk.Combobox(f, textvariable=self.src, values=names, state="readonly", width=44).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
            self.names = names
            ttk.Label(f, text="The name only picks the slot: the .wpk stores a number per part, not a name (A-D = 0x126-0x129 for bonnets, as the\n"
                              "list shows).  A copy sits exactly on its original (it starts hidden in the preview); replace its model afterwards.",
                      foreground="#8f9eb2", wraplength=440,
                      justify="left").grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Label(f, text="Your model (optional)", font=("TkDefaultFont", 10, "bold")).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))
            self.model = tk.StringVar()
            mr = ttk.Frame(f)
            mr.grid(row=7, column=0, columnspan=2, sticky="we", pady=(4, 0))
            ttk.Entry(mr, textvariable=self.model, width=46).pack(side="left")
            ttk.Button(mr, text="Browse…", command=self.pick_model).pack(side="left", padx=(6, 0))
            ttk.Label(f, text="An OBJ or FBX: after Add its Replace window opens for the new part (scale, rotation, textures), so you load it in one go.\n"
                              "Leave it empty to add only the slot (a copy or empty) and load a model later with Replace with OBJ….",
                      foreground="#8f9eb2", wraplength=440, justify="left").grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))
            bt = ttk.Frame(f)
            bt.grid(row=9, column=0, columnspan=2, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=w.destroy).pack(side="right")
            self.ok = ttk.Button(bt, text="Add", command=self.go, state="disabled")
            self.ok.pack(side="right", padx=(0, 8))
            self.name.trace_add("write", lambda *_a: self.check())
            e.focus_set()
            w.bind("<Return>", lambda _e: self.go())
            self.check()

        def pick_model(self):
            f = filedialog.askopenfilename(parent=self.w, title="Your model (OBJ or FBX)", filetypes=[("OBJ or FBX", "*.obj *.fbx"), ("Wavefront OBJ", "*.obj"), ("Autodesk FBX", "*.fbx"), ("All files", "*.*")])
            if f:
                self.model.set(f)
                if self.src.get() != self.EMPTY and self.src_tags:   # the model replaces the copy anyway: start empty unless asked
                    self.src.set(self.EMPTY)

        def check(self):
            if not self.name.get().strip() and self.note:                 # nothing typed yet: say why there is no suggestion
                self.tag = None
                self.info.configure(text=self.note, foreground="#8f9eb2")
                self.ok.configure(state="disabled")
                return
            try:
                self.tag, label = resolve_part_name(self.t.pack, self.name.get())
            except EditError as ex:
                self.tag = None
                self.info.configure(text=str(ex), foreground="#f85149")
                self.ok.configure(state="disabled")
                return
            self.info.configure(text="Becomes %s  -  tag 0x%x (%d)" % (label, self.tag, self.tag), foreground="#3fb950")
            self.ok.configure(state="normal")

        def go(self):
            self.check()
            if self.tag is None:
                return
            t, tag = self.t, self.tag
            i = self.names.index(self.src.get()) if self.src.get() in self.names else 0
            source = self.src_tags[i - 1] if i > 0 else None
            try:
                t.snapshot()
                add_part(t.pack, tag, source)
            except EditError as ex:
                t.undo.pop()
                messagebox.showerror("Add part", str(ex), parent=self.w)
                return
            if source is None:
                t.added.add(tag)
                t.hidden.discard(tag)
            else:
                t.hidden.add(tag)
            model = self.model.get().strip()
            self.w.destroy()
            t.edited("added %s (%d)%s" % (tag_name(tag), tag, "" if source is not None or model else " - empty: use Replace with OBJ… to give it a model"))
            t.select_tag(tag)
            t.set_buttons()
            if model:                                   # load the model into the new part through the normal Replace window
                if not os.path.isfile(model):
                    messagebox.showerror("Add part", "Model file not found: %s\nThe part was added; use Replace with OBJ… to load it." % model, parent=root)
                elif model.lower().endswith(".fbx"):
                    FbxReplaceDialog(t, model)
                else:
                    ReplaceDialog(t, model)

    class HeadlightsDialog:
        """Clear headlights: the dark lens cover over the bulbs becomes glass; Darkness / Tone / Reach / Lens black, live in the preview"""

        def __init__(self, tab):
            self.t = tab
            try:
                rep = fix_headlights(tab.pack, dry=True)
            except EditError as ex:
                messagebox.showerror("Headlights", str(ex), parent=root)
                return
            if not rep["lamps"]:
                messagebox.showinfo("Headlights", "\n".join(rep["notes"]), parent=root)
                return
            tab.live_begin()
            self.w = w = tk.Toplevel(root)
            w.title("Clear headlights")
            w.transient(root)
            w.protocol("WM_DELETE_WINDOW", self.cancel)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            self.head = ttk.Label(f, text="", font=("TkDefaultFont", 10, "bold"))
            self.head.grid(row=0, column=0, columnspan=3, sticky="w")
            self.dark = tk.DoubleVar(value=20)
            self.tone = tk.DoubleVar(value=86)
            self.reach = tk.DoubleVar(value=100)
            self.black = tk.DoubleVar(value=round(50 * 100 / 255.0))
            rows = (("Darkness", self.dark, 0, 100, "0 % = clear, 100 % = solid.  The stock lens is almost clear (about 20 %)."),
                    ("Tone", self.tone, 0, 100, "0 = black glass, 100 = white."),
                    ("Reach", self.reach, 20, 200, "How far around a lamp the cover is searched.  Lower it if body paint or trim next to a lamp turns to glass, raise it if part of the lens stays black."),
                    ("Lens black", self.black, 0, 60, "How dark a paint pixel must be to count as lens.  Raise it if a grey lens stays solid, lower it if dark paint is caught."))
            self.labels = []
            r = 1
            for name, var, lo, hi, hint in rows:
                ttk.Label(f, text=name).grid(row=r, column=0, sticky="w", pady=(8, 0))
                ttk.Scale(f, from_=lo, to=hi, variable=var, length=260, command=lambda _v: self.soon()).grid(row=r, column=1, padx=8, pady=(8, 0))
                lb = ttk.Label(f, width=6)
                lb.grid(row=r, column=2, pady=(8, 0))
                self.labels.append((lb, var))
                ttk.Label(f, text=hint, foreground="#8f9eb2", wraplength=420, justify="left").grid(row=r + 1, column=0, columnspan=3, sticky="w")
                r += 2
            self.msg = ttk.Label(f, text="", foreground="#f85149", wraplength=420, justify="left")
            self.msg.grid(row=r, column=0, columnspan=3, sticky="w")
            bt = ttk.Frame(f)
            bt.grid(row=r + 1, column=0, columnspan=3, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=self.cancel).pack(side="right")
            ttk.Button(bt, text="OK", command=self.ok).pack(side="right", padx=(0, 8))
            self.job = None
            self.rep = None
            self.apply()

        def tint(self):
            g = int(round(self.tone.get() * 2.55))
            return (g, min(255, g + 4), min(255, g + 8), int(round(self.dark.get() * 2.55)))

        def soon(self):
            if self.job is not None:
                self.w.after_cancel(self.job)
            self.job = self.w.after(150, self.apply)

        def run(self):
            tint = self.tint()
            reach, black = self.reach.get() / 100.0, int(round(self.black.get() * 2.55))
            out = {}

            def go():
                out["rep"] = fix_headlights(self.t.pack, dark=black, reach=reach, tint=tint)
            msg, err = self.t.live_apply(go)
            self.rep = out.get("rep")
            return err

        def apply(self):
            self.job = None
            for lb, var in self.labels:
                lb.configure(text="%d" % round(var.get()))
            err = self.run()
            if err:
                self.msg.configure(text=err)
            else:
                self.msg.configure(text="")
                n = self.rep["covers"] if self.rep else 0
                self.head.configure(text="%d lens triangles become glass." % n if n else "No solid lens cover found: try a higher Reach or Lens black.")

        def ok(self):
            if self.job is not None:
                self.w.after_cancel(self.job)
            err = self.run()
            if err:
                messagebox.showerror("Headlights", err, parent=self.w)
                return
            rep = self.rep
            if not (rep["covers"] or rep["bulbs"]):
                self.t.live_cancel()
                self.w.destroy()
                messagebox.showinfo("Headlights", "\n".join(rep["notes"]), parent=root)
                return
            self.t.live_commit("headlights: %d lens triangles are now glass, %d bulb triangles in the garage view" % (rep["covers"], rep["bulbs"]))
            self.w.destroy()

        def cancel(self):
            if self.job is not None:
                self.w.after_cancel(self.job)
            self.t.live_cancel()
            self.w.destroy()

    class WindowsDialog:
        """Clear windows: the window panes become glass; Darkness and Tone set how dark the glass looks, live in the preview"""

        def __init__(self, tab):
            self.t = tab
            tab.live_begin()
            root.configure(cursor="watch")
            root.update_idletasks()
            try:
                self.panes = find_window_panes(tab.pack, tick=root.update)
            except EditError as ex:
                root.configure(cursor="")
                tab.live_cancel()
                messagebox.showerror("Windows", str(ex), parent=root)
                return
            root.configure(cursor="")
            n = sum(len(v_) for v_ in self.panes[0].values())
            if not n:
                tab.live_cancel()
                messagebox.showinfo("Windows", "No window panes found: nothing on the outside of the body is a flat dark colour above the belt line.", parent=root)
                return
            self.n = n
            self.w = w = tk.Toplevel(root)
            w.title("Clear windows")
            w.transient(root)
            w.protocol("WM_DELETE_WINDOW", self.cancel)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="%d window triangles become see-through glass." % n, font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
            self.dark = tk.DoubleVar(value=round(GLASS_TINT[3] * 100 / 255.0))
            self.tone = tk.DoubleVar(value=round(GLASS_TINT[0] * 100 / 255.0))
            self.own = tk.BooleanVar(value=False)
            self.inner = tk.DoubleVar(value=round(WINDOW_INNER * 100))
            self.inner_done = self.inner.get()
            ttk.Label(f, text="Darkness").grid(row=1, column=0, sticky="w", pady=(10, 0))
            ttk.Scale(f, from_=0, to=100, variable=self.dark, length=260, command=lambda _v: self.soon()).grid(row=1, column=1, padx=8, pady=(10, 0))
            self.l1 = ttk.Label(f, width=6)
            self.l1.grid(row=1, column=2, pady=(10, 0))
            ttk.Label(f, text="Tone").grid(row=2, column=0, sticky="w")
            ttk.Scale(f, from_=0, to=100, variable=self.tone, length=260, command=lambda _v: self.soon()).grid(row=2, column=1, padx=8)
            self.l2 = ttk.Label(f, width=6)
            self.l2.grid(row=2, column=2)
            ttk.Label(f, text="Darkness 0 % = clear, 100 % = solid.  Tone 0 = black glass, 100 = light grey.\nA stock car's glass is about 44 % dark, mid grey.",
                      foreground="#8f9eb2", justify="left").grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
            ttk.Label(f, text="Mirror cut-off").grid(row=4, column=0, sticky="w", pady=(10, 0))
            sc = ttk.Scale(f, from_=40, to=100, variable=self.inner, length=260, command=lambda _v: self.l3.configure(text="%d %%" % round(self.inner.get())))
            sc.grid(row=4, column=1, padx=8, pady=(10, 0))
            sc.bind("<ButtonRelease-1>", lambda _e: self.refind())
            self.l3 = ttk.Label(f, width=6, text="%d %%" % round(self.inner.get()))
            self.l3.grid(row=4, column=2, pady=(10, 0))
            ttk.Label(f, text="Nothing wider than this share of the body's half width becomes glass, so the side mirrors stay black.\n"
                              "Lower it if a mirror or its mount still turns see-through, raise it if the edge of a side window stays black.\n"
                              "(Applied when you let go of the slider.)",
                      foreground="#8f9eb2", justify="left").grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
            ttk.Checkbutton(f, text="Use the car's own glass texture instead (the tint sliders are ignored)", variable=self.own,
                            command=self.soon).grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
            bt = ttk.Frame(f)
            bt.grid(row=7, column=0, columnspan=3, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=self.cancel).pack(side="right")
            ttk.Button(bt, text="OK", command=self.ok).pack(side="right", padx=(0, 8))
            self.job = None
            self.apply()

        def refind(self):
            v_ = round(self.inner.get())
            if v_ == self.inner_done:
                return
            try:
                self.panes = find_window_panes(self.t.pack, inner=v_ / 100.0, cands=self.panes[2])     # the slow part is kept: instant
                self.inner_done = v_
                self.n = sum(len(r_) for r_ in self.panes[0].values())
            except EditError as ex:
                messagebox.showerror("Windows", str(ex), parent=self.w)
            self.apply()

        def tint(self):
            if self.own.get():
                return None
            g = int(round(self.tone.get() * 2.55))
            return (g, min(255, g + 4), min(255, g + 8), int(round(self.dark.get() * 2.55)))

        def soon(self):
            if self.job is not None:
                self.w.after_cancel(self.job)
            self.job = self.w.after(120, self.apply)

        def apply(self):
            self.job = None
            self.l1.configure(text="%d %%" % round(self.dark.get()))
            self.l2.configure(text="%d" % round(self.tone.get()))
            tint = self.tint()
            msg, err = self.t.live_apply(lambda: make_windows_glass(self.t.pack, tint=tint, panes=self.panes))
            if err:
                messagebox.showerror("Windows", err, parent=self.w)

        def ok(self):
            if self.job is not None:
                self.w.after_cancel(self.job)
            tint = self.tint()
            msg, err = self.t.live_apply(lambda: make_windows_glass(self.t.pack, tint=tint, panes=self.panes))
            if err:
                messagebox.showerror("Windows", err, parent=self.w)
                return
            self.t.live_commit("windows: %d triangles are now glass (Undo puts them back)" % self.n)
            self.w.destroy()

        def cancel(self):
            if self.job is not None:
                self.w.after_cancel(self.job)
            self.t.live_cancel()
            self.w.destroy()

    class PartDialog:
        """one arcade part (bonnet, wing, mirror, lamp ...) becomes the selected part group; the preview follows the choice"""

        def __init__(self, tab, mod, tag):
            self.t, self.mod, self.tag, self.arc, self.choices = tab, mod, tag, None, []
            self.w = w = tk.Toplevel(root)
            w.title("Part from arcade")
            w.transient(root)
            w.protocol("WM_DELETE_WINDOW", self.cancel)
            f = ttk.Frame(w, padding=12)
            f.pack(fill="both", expand=True)
            ttk.Label(f, text="Into %s (%d)" % (tag_name(tag), tag), font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(f, text="Arcade car").grid(row=1, column=0, sticky="w", pady=(8, 0))
            names = list(tab.arcs)
            self.car = tk.StringVar(value=tab.guess_arc() or names[0])
            cbc = ttk.Combobox(f, textvariable=self.car, values=names, state="readonly", width=22)
            cbc.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
            cbc.bind("<<ComboboxSelected>>", lambda _e: self.load_car())
            ttk.Label(f, text="Part").grid(row=2, column=0, sticky="w", pady=(6, 0))
            self.pick = tk.StringVar()
            self.cb = ttk.Combobox(f, textvariable=self.pick, values=[], state="readonly", width=34)
            self.cb.grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
            self.cb.bind("<<ComboboxSelected>>", lambda _e: self.preview())
            ttk.Label(f, text="The part's old geometry is replaced (Cancel puts it back). Painted surfaces use the body texture; lamps are drawn as glow.",
                      foreground="#8f9eb2", wraplength=380, justify="left").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
            self.msg = ttk.Label(f, text="", foreground="#f85149", wraplength=380, justify="left")
            self.msg.grid(row=4, column=0, columnspan=2, sticky="w")
            bt = ttk.Frame(f)
            bt.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
            ttk.Button(bt, text="Cancel", command=self.cancel).pack(side="right")
            ttk.Button(bt, text="Replace", command=self.go).pack(side="right", padx=(0, 8))
            tab.live_begin()
            self.last = ""
            tab.hidden.discard(tag)
            self.load_car()

        def load_car(self):
            try:
                self.arc = self.t.get_arcade(self.car.get())
                self.choices = self.mod.part_choices(self.arc, self.tag)
            except (EditError, OSError, ValueError, struct.error, KeyError) as ex:
                self.arc, self.choices = None, []
            self.cb.configure(values=[c[0] for c in self.choices])
            if not self.choices:
                self.pick.set("")
                self.t.live_apply(lambda: "")
                self.msg.configure(text="That car has no matching parts.")
                return
            self.pick.set(self.choices[0][0])
            self.preview()

        def preview(self):
            if self.arc is None:
                return
            item = next((i for lab, i in self.choices if lab == self.pick.get()), None)
            self.last, err = self.t.live_apply(lambda: self.mod.import_part(self.t.pack, self.arc, self.tag, [item]))
            self.t.hidden.discard(self.tag)
            self.msg.configure(text=err or "")

        def cancel(self):
            self.t.live_cancel()
            self.w.destroy()

        def go(self):
            if self.arc is None:
                return
            if not self.last:
                self.preview()
                if not self.last:
                    return
            self.w.destroy()
            self.t.live_commit(self.last)

    return Tab()


# ------------------------------------------------------------------------------------------------
# command line
# ------------------------------------------------------------------------------------------------
def info_text(path):
    p = Pack.load(path)
    out = ["%s: %d textures, %d parts, %d triangles" % (os.path.basename(path), len(p.tex), len(p.chunks),
                                                         sum(c.counts()[2] for c in p.chunks))]
    for i, t in enumerate(p.tex):
        out.append("  texture %2d  %-32s %4d x %-4d %s" % (i, t["name"], t["w"], t["h"], "" if t["raw"] else "(from the stock pack)"))
    for tag, name, nb, nv, nt, texs in p.summary():
        out.append("  part %5d  %-18s %2d batches %6d verts %6d tris  textures %s" % (tag, name, nb, nv, nt, texs))
    return "\n".join(out)


def _opts(argv):
    pos, opt, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if a in ("--tags", "--scale", "--rot", "--texture", "--from", "--model", "--dark", "--tone", "--inner", "--out", "--to"):
                opt.setdefault(a, []).append(argv[i + 1])
                i += 1
            else:
                opt[a] = True
        else:
            pos.append(a)
        i += 1
    return pos, opt


def cli(argv):
    try:
        if not argv:
            print(__doc__)
            return 2
        cmd, (pos, opt) = argv[0], _opts(argv[1:])
        if cmd == "info" and len(pos) == 1:
            print(info_text(pos[0]))
        elif cmd == "export" and len(pos) == 2:
            p = Pack.load(pos[0])
            tags = {int(x, 0) for x in opt["--tags"][0].split(",")} if "--tags" in opt else None
            r = export_model(p, pos[1], tags, float(opt["--scale"][0]) if "--scale" in opt else 0.1, stock_pack_of(pos[0]))
            print("wrote %s: %d objects, %d triangles" % (pos[1], r["objects"], r["triangles"]))
        elif cmd == "replace" and len(pos) in (3, 4):
            p = Pack.load(pos[0])
            tag = int(pos[1], 0)
            meshes = parse_obj(pos[2], float(opt["--scale"][0]) if "--scale" in opt else 10.0,
                               int(opt["--rot"][0]) if "--rot" in opt else 0, "--no-flip-v" not in opt, "--flip-faces" in opt)
            for spec in opt.get("--texture", []):
                nm, fn = spec.split("=", 1)
                p.add_texture(nm, Image.open(fn))
            res = replace_parts(p, meshes, default_tag=tag, only_tags=None if any(m.tag is not None for m in meshes) else {tag})
            if "--shadow" in opt:
                rebuild_shadow(p)
            out = pos[3] if len(pos) == 4 else pos[0]
            st = p.save(out)
            for t, (n, miss) in res.items():
                print("part %d: %d triangles%s" % (t, n, ("  (material %s unknown: given the part's usual look)" % ", ".join(miss)) if miss else ""))
            print("wrote %s (%.2f MB)%s" % (out, st["bytes"] / 1e6, ", old file kept as .bak" if st["bak"] else ""))
        elif cmd == "deltex" and len(pos) >= 2:
            # deltex CAR.wpk NAME|NUMBER ... [--out OUT.wpk]
            p = Pack.load(pos[0])
            idxs = []
            for a_ in pos[1:]:
                i = int(a_) if a_.isdigit() else p.tex_index(a_)
                if i is None or not 0 <= i < len(p.tex):
                    raise EditError("There is no texture %s." % a_)
                idxs.append(i)
            names = [p.tex[i]["name"] for i in idxs]
            n, lost = p.delete_textures(idxs)
            print("deleted %d texture(s): %s%s" % (n, ", ".join(names[:10]), ("; %d batch(es) lost their picture" % lost) if lost else ""))
            out = opt["--out"][0] if "--out" in opt else pos[0]
            st = p.save(out)
            print("wrote %s" % out)
        elif cmd == "addpart" and len(pos) in (2, 3) and ("--from" in opt or "--model" in opt):
            p = Pack.load(pos[0])
            tag, label = resolve_part_name(p, pos[1])
            src = int(opt["--from"][0], 0) if "--from" in opt else None
            add_part(p, tag, src)
            if "--model" in opt:                        # the new part gets your OBJ / FBX straight away
                meshes = parse_obj(opt["--model"][0], float(opt["--scale"][0]) if "--scale" in opt else 10.0,
                                   int(opt["--rot"][0]) if "--rot" in opt else 0, "--no-flip-v" not in opt, "--flip-faces" in opt)
                for spec in opt.get("--texture", []):
                    nm, fn = spec.split("=", 1)
                    p.add_texture(nm, Image.open(fn))
                res = replace_parts(p, meshes, default_tag=tag, only_tags=None if any(m.tag is not None for m in meshes) else {tag})
                for t, (n, miss) in res.items():
                    print("part %d: %d triangles%s" % (t, n, ("  (material %s unknown: given the part's usual look)" % ", ".join(miss)) if miss else ""))
            out = pos[2] if len(pos) == 3 else pos[0]
            st = p.save(out)
            print("added %s as tag %d (0x%x)%s" % (label, tag, tag, ", a copy of part %d" % src if src is not None else ", empty" if "--model" not in opt else ""))
            print("wrote %s (%.2f MB)%s" % (out, st["bytes"] / 1e6, ", old file kept as .bak" if st["bak"] else ""))
        elif cmd == "kitcopy" and len(pos) == 2:
            src = Pack.load(pos[0])
            tags = [int(x, 0) for x in pos[1].split(",")]
            want = {x.lower() for x in opt["--to"][0].split(",")} if "--to" in opt else None
            for lab, f in kit_files(pos[0]):
                key = lab.lower().replace("aero ", "").replace("normal", "base")
                if want is not None and key not in want and lab.lower() not in want:
                    continue
                d = Pack.load(f)
                notes = copy_parts(src, d, tags)
                d.save(f)
                print("%s (%s): %s" % (lab, os.path.basename(f), "; ".join(notes)))
        elif cmd == "movepart" and len(pos) in (3, 4):
            p = Pack.load(pos[0])
            src = int(pos[1], 0)
            dst = dest_tag(p, pos[2])
            move_part(p, src, dst)
            out = pos[3] if len(pos) == 4 else pos[0]
            st = p.save(out)
            print("moved 0x%x -> 0x%x (%s%s)" % (src, dst, tag_name(dst), "" if engine_draws(dst) else "; the engine does NOT draw this number"))
            print("wrote %s (%.2f MB)%s" % (out, st["bytes"] / 1e6, ", old file kept as .bak" if st["bak"] else ""))
        elif cmd == "windows" and len(pos) in (1, 2):
            p = Pack.load(pos[0])
            tint = GLASS_TINT
            if "--clear" in opt:
                tint = None
            elif "--dark" in opt or "--tone" in opt:
                g = int(round(float(opt["--tone"][0]) * 2.55)) if "--tone" in opt else GLASS_TINT[0]
                tint = (g, min(255, g + 4), min(255, g + 8), int(round(float(opt["--dark"][0]) * 2.55)) if "--dark" in opt else GLASS_TINT[3])
            kw = {"inner": float(opt["--inner"][0]) / 100.0} if "--inner" in opt else {}
            rep = make_windows_glass(p, tint=tint, **kw)
            for n in rep["notes"]:
                print(n)
            if rep["tris"]:
                out = pos[1] if len(pos) == 2 else pos[0]
                st = p.save(out)
                print("wrote %s%s" % (out, ", old file kept as .bak" if st["bak"] else ""))
        elif cmd == "headlights" and len(pos) in (1, 2):
            p = Pack.load(pos[0])
            rep = fix_headlights(p)
            for n in rep["notes"]:
                print(n)
            if rep["covers"] or rep["bulbs"]:
                out = pos[1] if len(pos) == 2 else pos[0]
                st = p.save(out)
                print("wrote %s%s" % (out, ", old file kept as .bak" if st["bak"] else ""))
        elif cmd == "selftest" and len(pos) == 1:
            return selftest(pos[0])
        else:
            print(__doc__)
            return 2
    except (EditError, OSError, ValueError, KeyError) as ex:
        print("error:", ex)
        return 1
    return 0


def selftest(folder):
    """read every .wpk, write it back, compare (byte for byte, else geometry for geometry)"""
    import glob
    bad = same = 0
    files = sorted(glob.glob(os.path.join(folder, "**", "*.wpk"), recursive=True))
    for f in files:
        p = Pack.load(f)
        data = p.to_bytes()
        if data == open(f, "rb").read():
            same += 1
            continue
        tmp = f + ".selftest.tmp"
        open(tmp, "wb").write(data)
        try:
            q = Pack.load(tmp)
            ok = len(p.chunks) == len(q.chunks) and all(
                a.tag == b.tag and len(a.batches) == len(b.batches) and all(
                    np.array_equal(x.V, y.V) and np.array_equal(x.I, y.I) and x.material() == y.material()
                    for x, y in zip(a.batches, b.batches)) for a, b in zip(p.chunks, q.chunks))
        finally:
            os.remove(tmp)
        if not ok:
            bad += 1
            print("MISMATCH", f)
    print("%d files: %d byte-identical, %d rewritten differently but same content, %d wrong" % (len(files), same, len(files) - same - bad, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
