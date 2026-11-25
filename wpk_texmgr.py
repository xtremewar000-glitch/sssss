"""Texture / material catalogue for the Models (WPK) tab (Milestone 5).

Pure Python + numpy + PIL, no Tk: everything here can be unit-tested without a display.
It only READS a wpk_edit.Pack.  The one thing that changes a pack is Pack.add_texture / Pack.delete_textures
in wpk_edit.py (existing code); this module analyses what such an edit would touch, so the UI can warn first.

What a WPK3 pack really contains (checked against real cars):
  * pack.tex[i]          name, w, h, fmt (0 RGB565, 1 RGBA4444, 2 RGBA5551), raw (b"" = borrowed from the stock pack)
  * a batch (draw call)  refers to slots: tex (picture), env (reflection map), spec (specular map); 0xFFFF = none
  * paint copies         "p1:<name>" .. "p5:<name>" are extra textures no batch refers to; the game picks them by NAME for the
                         paint colours.  They look "unused" to a plain reference count, so they are reported separately here.
"""
import re
import zlib

import numpy as np

NO_TEX = 0xFFFF
FMT_NAMES = {0: "RGB565", 1: "RGBA4444", 2: "RGBA5551"}
ROLE_NAMES = {"tex": "picture", "env": "reflection", "spec": "specular"}
PAINT_RE = re.compile(r"^p([1-5]):(.+)$", re.I)
FILTERS = ("All", "In use", "Unused", "Borrowed", "Missing", "Paint copies", "Problems")


def fmt_name(fmt):
    return FMT_NAMES.get(fmt, "fmt %s" % fmt)


# ------------------------------------------------------------------------------------------------
# catalogue
# ------------------------------------------------------------------------------------------------
class TexEntry:
    __slots__ = ("index", "name", "w", "h", "fmt", "nbytes", "borrowed", "state", "refs", "draws", "roles",
                 "paint_of", "paint_copies", "flags", "meta")

    def __init__(self, index, t):
        self.index, self.name, self.w, self.h, self.fmt = index, t["name"], t["w"], t["h"], t["fmt"]
        self.nbytes = len(t["raw"])
        self.borrowed = not t["raw"]
        self.state = "ok"             # ok | borrowed | MISSING | BAD DATA
        self.refs = {}                # chunk tag -> {"draws": n, "roles": set}
        self.draws = 0                # batches that use it (any role)
        self.roles = set()
        self.paint_of = None          # slot of the texture this is a paint copy of (None = not a paint copy / base missing)
        self.paint_copies = []        # slots of its p1..p5 copies
        self.flags = []               # "unused", "paint copy", "orphan paint copy", "duplicate name", "odd size"
        self.meta = ""

    @property
    def size_text(self):
        return "%d × %d" % (self.w, self.h) if self.w and self.h else "-"

    @property
    def in_use(self):
        return self.draws > 0

    @property
    def is_paint_copy(self):
        return PAINT_RE.match(self.name) is not None

    @property
    def problem(self):
        return self.state in ("MISSING", "BAD DATA") or "orphan paint copy" in self.flags or "duplicate name" in self.flags

    def status_text(self):
        bits = [self.state if self.state != "ok" else "ok"]
        bits += [f for f in self.flags if f not in bits]
        return ", ".join(bits)


class Catalog:
    def __init__(self):
        self.entries = []
        self.dangling = []            # (tag, draw, role, slot): a batch points at a slot that does not exist
        self.warnings = []

    def by_index(self, i):
        return self.entries[i] if 0 <= i < len(self.entries) else None

    def counts(self):
        c = dict.fromkeys(("total", "in_use", "unused", "borrowed", "missing", "paint", "problems"), 0)
        for e in self.entries:
            c["total"] += 1
            c["in_use"] += e.in_use
            c["unused"] += "unused" in e.flags
            c["borrowed"] += e.state == "borrowed"
            c["missing"] += e.state in ("MISSING", "BAD DATA")
            c["paint"] += e.is_paint_copy
            c["problems"] += e.problem
        c["dangling"] = len(self.dangling)
        return c


def _decodable(t):
    """can wpk_edit.decode16 read this texture's data?"""
    return bool(t["raw"]) and t["w"] > 0 and t["h"] > 0 and len(t["raw"]) >= t["w"] * t["h"] * 2


def catalog(pack, stock=None, name_fn=None):
    """Everything about the textures of `pack`: who uses them, what state they are in.  `stock` = the stock Pack of an aero kit
    (None when the pack is a stock pack or the stock file is not beside it).  Reads only."""
    cat = Catalog()
    n = len(pack.tex)
    by_name = {}
    for i, t in enumerate(pack.tex):
        e = TexEntry(i, t)
        cat.entries.append(e)
        by_name.setdefault(t["name"].lower(), []).append(i)
        if t["raw"]:
            if not _decodable(t):
                e.state = "BAD DATA"
        else:
            j = stock.tex_index(t["name"]) if stock is not None else None
            e.state = "borrowed" if (j is not None and stock.tex[j]["raw"]) else "MISSING"
    # references from the draws
    for c in pack.chunks:
        for bi, b in enumerate(c.batches):
            for role, slot in (("tex", b.tex), ("env", b.env), ("spec", b.spec)):
                if slot == NO_TEX:
                    continue
                if slot >= n:
                    cat.dangling.append((c.tag, bi, role, slot))
                    continue
                e = cat.entries[slot]
                r = e.refs.setdefault(c.tag, {"draws": 0, "roles": set()})
                r["draws"] += 1
                r["roles"].add(role)
                e.draws += 1
                e.roles.add(role)
    # paint copies and flags
    for e in cat.entries:
        m = PAINT_RE.match(e.name)
        if m:
            base = [j for j in by_name.get(m.group(2).lower(), []) if j != e.index]
            if base:
                e.paint_of = base[0]
                cat.entries[base[0]].paint_copies.append(e.index)
                e.flags.append("paint copy")
            else:
                e.flags.append("orphan paint copy")
    for e in cat.entries:
        if len(by_name[e.name.lower()]) > 1:
            e.flags.append("duplicate name")
        if e.w and e.h and (e.w & (e.w - 1) or e.h & (e.h - 1)):
            e.flags.append("odd size")
        if not e.in_use and not e.is_paint_copy:
            e.flags.append("unused")
        parts = " ".join(name_fn(t).lower() for t in e.refs) if name_fn else ""
        e.meta = "%s %s %dx%d %s %s %s" % (e.name.lower(), fmt_name(e.fmt).lower(), e.w, e.h,
                                        e.state.lower(), " ".join(f.lower() for f in e.flags), parts)
        e.meta += " " + " ".join("%d 0x%x" % (t, t) for t in e.refs)
    for tag, bi, role, slot in cat.dangling:
        cat.warnings.append("part 0x%x draw %d: %s slot %d does not exist (the pack has %d textures)" % (tag, bi, ROLE_NAMES[role], slot, n))
    for e in cat.entries:
        if e.state == "MISSING":
            cat.warnings.append("texture %s is empty in this pack and %s" % (
                e.name, "not found in the stock pack" if stock is not None else "there is no stock pack beside it to take it from"))
        elif e.state == "BAD DATA":
            cat.warnings.append("texture %s: data (%d bytes) is shorter than %d × %d × 2" % (e.name, e.nbytes, e.w, e.h))
        if "orphan paint copy" in e.flags:
            cat.warnings.append("%s is a paint copy but its base texture is not in the pack" % e.name)
        if "duplicate name" in e.flags and e.index == min(by_name[e.name.lower()]):
            cat.warnings.append("texture name %s is used by %d slots" % (e.name, len(by_name[e.name.lower()])))
    return cat


def filter_entries(entries, query="", flt="All"):
    """entries that pass the quick filter and match every word of the query (name, format, size, state, flag, part name / tag)"""
    words = [w for w in query.lower().split() if w]
    out = []
    for e in entries:
        if flt == "In use" and not e.in_use:
            continue
        if flt == "Unused" and "unused" not in e.flags:
            continue
        if flt == "Borrowed" and e.state != "borrowed":
            continue
        if flt == "Missing" and e.state not in ("MISSING", "BAD DATA"):
            continue
        if flt == "Paint copies" and not e.is_paint_copy:
            continue
        if flt == "Problems" and not e.problem:
            continue
        if all(w in e.meta for w in words):
            out.append(e)
    return out


def users(entry, name_fn):
    """[(tag, part name, draws, 'picture, reflection')] of the parts that draw with a texture, sorted by tag"""
    return [(t, name_fn(t), r["draws"], ", ".join(ROLE_NAMES[x] for x in ("tex", "env", "spec") if x in r["roles"]))
            for t, r in sorted(entry.refs.items())]


def materials(pack, stock=None, name_fn=None, tag=None):
    """one row per draw (material): [(tag, part name, draw, picture, reflection, specular, blend, program, shine)].
    tag = only that part.  Texture names come from the pack ('(untextured)' / '(missing #n)' where needed)."""
    blend = ("opaque", "alpha", "additive", "blend 3")

    def lab(i, none="-"):
        if i == NO_TEX:
            return none
        return pack.tex[i]["name"] if 0 <= i < len(pack.tex) else "(missing #%d)" % i
    rows = []
    for c in pack.chunks:
        if tag is not None and c.tag != tag:
            continue
        for bi, b in enumerate(c.batches):
            rows.append((c.tag, name_fn(c.tag) if name_fn else "", bi, lab(b.tex, "(untextured)"), lab(b.env), lab(b.spec),
                         blend[b.flags & 3], b.prog, b.power / 10000.0))
    return rows


# ------------------------------------------------------------------------------------------------
# preview (decoded on demand, cached)
# ------------------------------------------------------------------------------------------------
BACKGROUNDS = ("checker", "dark", "light")


def checkerboard(w, h, cell=8, c1=(205, 205, 205), c2=(150, 150, 150)):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // cell + yy // cell) & 1).astype(bool)
    out = np.empty((h, w, 3), np.uint8)
    out[~m], out[m] = c1, c2
    return out


def compose(rgba, bg="checker", box=256):
    """RGBA uint8 (h, w, 4) -> RGB uint8 on a checkerboard / dark / light background, scaled to fit `box` (small pictures are
    enlarged by whole numbers with nearest-neighbour so the texels stay crisp; big ones are reduced)."""
    from PIL import Image
    h, w = rgba.shape[:2]
    s = max(1, box // max(w, h))
    if s > 1:
        rgba = np.repeat(np.repeat(rgba, s, 0), s, 1)
    elif max(w, h) > box:
        im = Image.fromarray(rgba, "RGBA")
        f = box / max(w, h)
        rgba = np.asarray(im.resize((max(1, round(w * f)), max(1, round(h * f))), Image.BOX))
    h, w = rgba.shape[:2]
    if bg == "dark":
        base = np.full((h, w, 3), 32, np.uint8)
    elif bg == "light":
        base = np.full((h, w, 3), 235, np.uint8)
    else:
        base = checkerboard(w, h)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return (rgba[..., :3].astype(np.float32) * a + base.astype(np.float32) * (1 - a)).round().astype(np.uint8)


def has_alpha(rgba):
    return bool(rgba[..., 3].min() < 255)


class PreviewCache:
    """LRU caches of previews: the decoded RGBA of a texture (once, whatever the background) and the composed RGB picture.
    Keys contain a checksum of the texture's data, so an edit (replace, undo ...) can never show a stale picture."""

    def __init__(self, limit=24):
        self.limit = limit
        self._d = {}                 # composed pictures
        self._rgba = {}              # decoded textures
        self.decodes = 0

    @staticmethod
    def _source(pack, i, stock):
        """the texture dict that really holds the data for slot i (a borrowed one lives in the stock pack), or None"""
        t = pack.tex[i]
        if t["raw"]:
            return t
        j = stock.tex_index(t["name"]) if stock is not None else None
        if j is not None and stock.tex[j]["raw"]:
            return stock.tex[j]
        return None

    @staticmethod
    def _trim(d, limit):
        while len(d) > limit:
            d.pop(next(iter(d)))

    def get(self, pack, i, stock=None, bg="checker", box=256):
        """(RGB array or None, note).  Never raises: a texture that cannot be decoded gives (None, reason)."""
        if not (0 <= i < len(pack.tex)):
            return None, "no such texture"
        src = self._source(pack, i, stock)
        if src is None:
            return None, "Missing: empty in this pack and not found in the stock pack."
        if not _decodable(src):
            return None, "Bad data: %d bytes for %d x %d." % (len(src["raw"]), src["w"], src["h"])
        dk = (src["name"], src["w"], src["h"], src["fmt"], len(src["raw"]), zlib.crc32(src["raw"]))
        k = dk + (bg, box)
        if k in self._d:
            v = self._d.pop(k)
            self._d[k] = v
            return v
        try:
            rgba = self._rgba.pop(dk, None)
            if rgba is None:
                from wpk_edit import decode16
                rgba = decode16(src["w"], src["h"], src["fmt"], src["raw"])
                self.decodes += 1
            self._rgba[dk] = rgba
            self._trim(self._rgba, 8)
            v = (compose(rgba, bg, box), "%d x %d  %s%s" % (src["w"], src["h"], fmt_name(src["fmt"]),
                                                          "  -  has transparency" if has_alpha(rgba) else ""))
        except Exception as ex:            # the preview must never take the dialog down
            return None, "Cannot decode: %s" % ex
        self._d[k] = v
        self._trim(self._d, self.limit)
        return v

    def clear(self):
        self._d.clear()
        self._rgba.clear()


# ------------------------------------------------------------------------------------------------
# what an edit would touch (read-only analysis, shown before the user confirms)
# ------------------------------------------------------------------------------------------------
def plan_replace(pack, idx, im):
    """what Pack.add_texture(name, im) would write for slot idx: the size / format the picture is converted to"""
    import wpk_edit as E
    t = pack.tex[idx]
    w, h, fmt, raw = E.encode16(im, 512)
    src_alpha = has_alpha(np.asarray(im.convert("RGBA")))
    return dict(old=(t["w"], t["h"], t["fmt"], len(t["raw"])), new=(w, h, fmt, len(raw)), resized=(w, h) != tuple(im.size),
                source_size=tuple(im.size), alpha_lost=(t["fmt"] in (1, 2) and fmt == 0), source_alpha=src_alpha)


def is_stock_path(path):
    return bool(path) and not re.search(r"_aero[ABC]\.wpk$", path, re.I)


def kit_borrowers(path, name, loader=None):
    """the car's other packs that BORROW `name` from the pack at `path` (an aero kit holding it empty): [(label, path, draws)].
    Only meaningful for a stock pack.  Loads the sibling files (read-only); a file that cannot be read is skipped and
    reported as (label, path, None)."""
    import wpk_edit as E
    out = []
    if not is_stock_path(path):
        return out
    for lab, p in E.kit_files(path):
        try:
            k = (loader or E.Pack.load)(p)
        except E.EditError:
            out.append((lab, p, None))
            continue
        j = k.tex_index(name)
        if j is not None and not k.tex[j]["raw"]:
            n = sum(1 for c in k.chunks for b in c.batches if j in (b.tex, b.env, b.spec))
            out.append((lab, p, n))
    return out


def impact(pack, cat, idx, path=None, stock=None, action="replace", kits=None):
    """facts about touching texture idx: a dict with `lines` (human text, most important first) and `shared` (True when
    more than this one picture-on-one-part is affected, so the UI should ask first).  `kits` = result of kit_borrowers
    (None = not looked up)."""
    e = cat.entries[idx]
    lines = []
    parts = sorted(e.refs)
    shared = False
    if len(parts) > 1:
        shared = True
    if parts:
        lines.append("Used by %d part%s (%d draw%s): %s" % (
            len(parts), "" if len(parts) == 1 else "s", e.draws, "" if e.draws == 1 else "s",
            ", ".join("0x%x" % t for t in parts[:10]) + (" …" if len(parts) > 10 else "")))
    elif not e.is_paint_copy:
        lines.append("No part draws it.")
    if e.roles & {"env", "spec"}:
        shared = True
        lines.append("It is used as a %s map: it changes how the surface shines on every draw that uses it." %
                     " / ".join(ROLE_NAMES[r] for r in ("env", "spec") if r in e.roles))
    if e.paint_copies:
        shared = True
        lines.append("It has %d paint cop%s (%s). %s" % (
            len(e.paint_copies), "y" if len(e.paint_copies) == 1 else "ies",
            ", ".join(cat.entries[j].name.split(":")[0] for j in e.paint_copies),
            "They are separate textures and are NOT changed by the replacement." if action == "replace" else
            "They stay in the pack unless you select them too."))
    if e.is_paint_copy and e.paint_of is not None:
        lines.append("This is a paint copy of %s; changing it changes only that paint colour." % cat.entries[e.paint_of].name)
    if e.borrowed:
        if action == "replace":
            lines.append("It is borrowed from the stock pack. Replacing it here gives THIS kit its own copy; the stock car is unchanged.")
    elif kits:
        live = [k for k in kits if k[2] is not None and (k[2] > 0 or action == "delete")]
        if live:
            shared = True
            lines.append("Aero kits that borrow it from this pack (they will %s): %s" % (
                "show the new picture" if action == "replace" else "lose it / show a missing texture",
                ", ".join("%s (%d draw%s)" % (l, n, "" if n == 1 else "s") for l, _, n in live)))
        bad = [k for k in kits if k[2] is None]
        if bad:
            lines.append("Could not read: %s (their use of this texture is unknown)." % ", ".join(l for l, _, _ in bad))
    elif kits is None and is_stock_path(path or ""):
        lines.append("Aero kits beside this file were not checked.")
    return dict(lines=lines, shared=shared, parts=parts, paint_copies=list(e.paint_copies), kits=kits or [])
