"""
meters_edit.py  -  edit the gauge (meter) images of a plain "WMS2" meters file (such as meters-v2.bin).

Two plain formats (the game's own copies, WMDC, are encrypted: see wmdc.py).  WMT1 (meters.bin) is the same idea with a
12-byte header ("WMT1", cars, tiles), 2 zones (day, night) per car, then the tile table and pixels.  WMS2, worked out from
meters-v2.bin and checked byte for byte (a file read and written back is identical):

    "WMS2"  u32 version  u32 layouts  u32 rows  u32 cols  u32 tiles
    layouts x rows x cols  u16   tile numbers of each layout (0xFFFF = empty cell)
    tiles x ( u32 width, u32 height, u32 file offset )
    the tiles' pixels, one after the other: width x height x 4 bytes, R G B A, no compression

Every layout is one car's meters: 6 rows (zones) x 9 cells (parts).  A row is a strip of small pictures left to right (dial faces, needles, small dials ...);
the tiles are shared between layouts (layout 0 and 1 use the same needles, for example).

Not here: the game's own files (meters.bin, mt6meter.bin ...) are WMDC containers, encrypted; see wmdc.py.
This tool edits the plain WMS2 form and cannot pack it back into WMDC.

    python meters_edit.py info FILE.bin                 what is in it
    python meters_edit.py sheet FILE.bin N OUT.png      layout N as one picture
    python meters_edit.py tile FILE.bin N OUT.png       tile N as a picture
    python meters_edit.py check FILE.bin                what the engine's reader would say (it ignores a file it dislikes)
    python meters_edit.py preview FILE.bin CAR ZONE OUT.png [KMH [REV 0-1]]   the meters as the game draws them
    python meters_edit.py selftest FILE.bin             read, write back, compare; edit a copy
"""
import os
import struct
import sys

import numpy as np
from PIL import Image

import wmdc

try:
    import meter_tables as T            # the engine's part counts / positions (made by gen_meter_tables.py)
except ImportError:
    T = None

MAGIC = b"WMS2"
MAGIC1 = b"WMT1"                        # the older, smaller meters.bin: 2 zones (day, night) per car
EMPTY = 0xFFFF
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LAMP_POS = (491, 283)                   # where the engine draws the shift lamp (second style, zones 2 and 3, part 4)
LAMP_SLOT = 4


import vpk_ui as UI                       # shared confirmations / look (Milestone 6)
import vpk_layout as VL                   # Blender-style list | viewport | panels layout


class MetersError(Exception):
    pass


class Meters:
    def __init__(self):
        self.kind = "WMS2"
        self.version = 1
        self.layouts = np.zeros((0, 6, 9), np.uint16)
        self.tiles = []                     # numpy uint8 arrays (h, w, 4); replaced, never changed in place
        self.path = None

    # ------------------------------------------------------------------ reading / writing
    @classmethod
    def from_bytes(cls, d):
        info = wmdc.inspect(d)
        if info:
            raise MetersError("This is an encrypted game file (WMDC).\n%s\nThis tool cannot read it without the key. Open the plain "
                              "WMS2 form of the file (like meters-v2.bin) instead." % wmdc.describe(info))
        if d[:4] == MAGIC:
            if len(d) < 24:
                raise MetersError("The file is too short.")
            _m, ver, n, rows, cols, nt = struct.unpack("<4s5I", d[:24])
            head, kind = 24, "WMS2"
        elif d[:4] == MAGIC1:
            if len(d) < 12:
                raise MetersError("The file is too short.")
            _m, n, nt = struct.unpack("<4sII", d[:12])
            ver, rows, cols, head, kind = 0, 2, 9, 12, "WMT1"
        else:
            raise MetersError("Not a meters file (it starts with %r; expected WMS2 or WMT1)." % bytes(d[:4]))
        p = head + n * rows * cols * 2
        q = p + nt * 12
        if rows == 0 or cols == 0 or q > len(d):
            raise MetersError("The tables do not fit in the file: damaged, or a different version.")
        m = cls()
        m.kind, m.version = kind, ver
        m.layouts = np.frombuffer(d[head:p], "<u2").reshape(n, rows, cols).copy()
        rec = np.frombuffer(d[p:q], "<u4").reshape(nt, 3)
        pos = q
        for i, (w, h, off) in enumerate(rec):
            if int(off) != pos:
                raise MetersError("Tile %d is not where expected (offset %d, expected %d): this tool only reads files whose tiles "
                                  "follow each other." % (i, off, pos))
            end = pos + int(w) * int(h) * 4
            if end > len(d):
                raise MetersError("Tile %d runs past the end of the file." % i)
            m.tiles.append(np.frombuffer(d, np.uint8, int(w) * int(h) * 4, pos).reshape(int(h), int(w), 4).copy())
            pos = end
        if pos != len(d):
            raise MetersError("%d bytes follow the last tile: this tool does not know what they are." % (len(d) - pos))
        used = m.layouts[m.layouts != EMPTY]
        if len(used) and int(used.max()) >= nt:
            raise MetersError("A layout points at tile %d but there are only %d tiles." % (int(used.max()), nt))
        return m

    @classmethod
    def load(cls, path):
        with open(path, "rb") as fh:
            m = cls.from_bytes(fh.read())
        m.path = path
        return m

    def to_bytes(self):
        n, rows, cols = self.layouts.shape
        if self.kind == "WMT1":
            head = struct.pack("<4sII", MAGIC1, n, len(self.tiles))
        else:
            head = struct.pack("<4s5I", MAGIC, self.version, n, rows, cols, len(self.tiles))
        out = [head, self.layouts.astype("<u2").tobytes()]
        pos = len(head) + n * rows * cols * 2 + len(self.tiles) * 12
        rec = []
        for t in self.tiles:
            rec.append(struct.pack("<3I", t.shape[1], t.shape[0], pos))
            pos += t.size
        out += rec
        out += [np.ascontiguousarray(t).tobytes() for t in self.tiles]
        return b"".join(out)

    def save(self, path, backup=True):
        data = self.to_bytes()
        bak = False
        if backup and os.path.exists(path) and not os.path.exists(path + ".bak"):
            os.replace(path, path + ".bak")
            bak = True
        with open(path, "wb") as fh:
            fh.write(data)
        self.path = path
        return bak

    # ------------------------------------------------------------------ looking
    @property
    def n_layouts(self):
        return self.layouts.shape[0]

    def usage(self):
        """{tile: set of layouts using it}"""
        u = {}
        for L in range(self.n_layouts):
            for t in np.unique(self.layouts[L]):
                if t != EMPTY:
                    u.setdefault(int(t), set()).add(L)
        return u

    def cells(self, L):
        """the filled cells of a layout with their place in the sheet: [(row, col, tile, x, y, w, h)]
        (each row is a strip left to right, rows one under the other, empty rows leave no gap)"""
        out, y = [], 0
        for r in range(self.layouts.shape[1]):
            x, rh = 0, 0
            row = []
            for c in range(self.layouts.shape[2]):
                t = int(self.layouts[L, r, c])
                if t == EMPTY:
                    continue
                h, w = self.tiles[t].shape[:2]
                row.append((r, c, t, x, y, w, h))
                x += w
                rh = max(rh, h)
            out += row
            y += rh
        return out

    def sheet_size(self, L):
        cs = self.cells(L)
        return (max((x + w for _r, _c, _t, x, _y, w, _h in cs), default=0), max((y + h for _r, _c, _t, _x, y, _w, h in cs), default=0))

    def sheet(self, L):
        """layout L as one RGBA picture"""
        w, h = self.sheet_size(L)
        im = np.zeros((max(h, 1), max(w, 1), 4), np.uint8)
        for _r, _c, t, x, y, tw, th in self.cells(L):
            im[y:y + th, x:x + tw] = self.tiles[t]
        return Image.fromarray(im, "RGBA")

    def tile_image(self, t):
        return Image.fromarray(self.tiles[t], "RGBA")

    # ------------------------------------------------------------------ editing
    def snapshot(self):
        return (self.layouts.copy(), list(self.tiles))

    def restore(self, snap):
        self.layouts, self.tiles = snap[0].copy(), list(snap[1])

    @staticmethod
    def _rgba(img, size=None, resize=False):
        if not isinstance(img, Image.Image):
            img = Image.open(img)
        img = img.convert("RGBA")
        if size is not None and img.size != size:
            if not resize:
                raise MetersError("The picture is %d x %d but it must be %d x %d." % (*img.size, *size))
            img = img.resize(size, Image.LANCZOS)
        return np.array(img, np.uint8)

    def replace_tile(self, t, img, resize=False):
        """new pixels for tile t (every layout that uses it shows them)"""
        h, w = self.tiles[t].shape[:2]
        self.tiles[t] = self._rgba(img, (w, h), resize)

    def new_tile_for(self, L, r, c, img, resize=False):
        """give one cell its own copy of a tile, with new pixels; -> the new tile's number"""
        old = int(self.layouts[L, r, c])
        h, w = self.tiles[old].shape[:2]
        self.tiles.append(self._rgba(img, (w, h), resize))
        self.layouts[L, r, c] = len(self.tiles) - 1
        return len(self.tiles) - 1

    def set_cell(self, L, r, c, t):
        if t is not None and not (0 <= t < len(self.tiles)):
            raise MetersError("There is no tile %s (0 - %d)." % (t, len(self.tiles) - 1))
        self.layouts[L, r, c] = EMPTY if t is None else t

    def set_sheet(self, L, img, everywhere=False, resize=False):
        """new picture for a whole layout, cut into its cells.  A tile other layouts use gets a copy of its own for this
        layout (the others stay as they are) unless everywhere=True.  -> (tiles changed, tiles added)"""
        sw, sh = self.sheet_size(L)
        a = self._rgba(img, (sw, sh), resize)
        use = self.usage()
        changed = added = 0
        for r, c, t, x, y, w, h in self.cells(L):
            piece = a[y:y + h, x:x + w]
            if np.array_equal(piece, self.tiles[t]):
                continue
            if everywhere or use.get(t, set()) <= {L} and sum(1 for q in self.cells(L) if q[2] == t) == 1:
                self.tiles[t] = piece.copy()
            else:
                self.tiles.append(piece.copy())
                self.layouts[L, r, c] = len(self.tiles) - 1
                added += 1
            changed += 1
        return changed, added

    def remove_unused(self):
        """drop tiles no layout uses and renumber; -> how many were dropped"""
        used = sorted({int(t) for t in np.unique(self.layouts) if t != EMPTY})
        if len(used) == len(self.tiles):
            return 0
        mp = {t: i for i, t in enumerate(used)}
        new = np.full_like(self.layouts, EMPTY)
        for t, i in mp.items():
            new[self.layouts == t] = i
        dropped = len(self.tiles) - len(used)
        self.tiles = [self.tiles[t] for t in used]
        self.layouts = new
        return dropped


def describe(m):
    use = m.usage()
    return "%s: %d cars, %d zones of %d parts each, %d tiles (%d shared by more than one car)" % (
        m.kind, m.n_layouts, m.layouts.shape[1], m.layouts.shape[2], len(m.tiles), sum(1 for s in use.values() if len(s) > 1))


# ================================================================================================ the engine's rules
def car_name(L):
    return T.NAMES[L] if T and 0 <= L < len(T.NAMES) else "car %d" % L


def part_counts(kind):
    """[[parts the engine expects in each zone] per car], or None without the tables"""
    if not T:
        return None
    return [list(c[3]) for c in T.LEGACY] if kind == "WMT1" else [list(c[0]) for c in T.V2]


def lamp_images():
    """the shift lamp sprites the engine uses: {'off_m', 'on_m', 'off_n', 'on_n'} as RGBA arrays (None if the files are missing)"""
    if not hasattr(lamp_images, "cache"):
        try:
            lamp_images.cache = {k: np.array(Image.open(os.path.join(ASSETS, "racing_rev_%s.png" % k)).convert("RGBA"), np.uint8)
                                 for k in ("off_m", "on_m", "off_n", "on_n")}
        except OSError:
            lamp_images.cache = None
    return lamp_images.cache


def lamp_slot(m, L, zone):
    """the part number of the shift lamp in this car's zone, or None (only the second style's zones 2 and 3 of a WMS2 file have one)"""
    if m.kind != "WMS2" or zone not in (2, 3) or not T or not (0 <= L < len(T.V2)):
        return None
    c = T.V2[L]
    if c[0][zone] > LAMP_SLOT:
        p = c[1][zone][LAMP_SLOT]
        if (p[0], p[1]) == LAMP_POS and p[4] == 0 and not p[5]:
            return LAMP_SLOT
    return None


def lamp_ok(m, L, zone):
    """True when the engine will light the lamp for this car/zone (its tile is still the stock blank one); None when there is no lamp"""
    if lamp_slot(m, L, zone) is None:
        return None
    li, t = lamp_images(), int(m.layouts[L, zone, LAMP_SLOT])
    if not li:
        return None if lamp_images.cache is None and lamp_missing() else False
    if t == EMPTY or t >= len(m.tiles):
        return False
    a, b = m.tiles[t], li["off_m" if zone == 2 else "off_n"]
    if a.shape != b.shape:
        return False
    clear = (a[..., 3] == 0) & (b[..., 3] == 0)      # fully transparent pixels: colour is irrelevant
    return bool(np.array_equal(a[~clear], b[~clear]))


def lamp_missing():
    return True


def validate(m, limit=40):
    """what the engine's reader (meter_pack.h) checks, plus what it would not tell you: -> [(level, text)], level 'error'
    (the engine ignores the whole file) or 'warning' (it loads but something will not work)"""
    out = []
    n, rows, cols = m.layouts.shape
    if n != 55:
        out.append(("error", "The file has %d cars; the engine needs exactly 55." % n))
    if not 1 <= len(m.tiles) <= 8192:
        out.append(("error", "The file has %d tiles; the engine allows 1 to 8192." % len(m.tiles)))
    if m.kind == "WMS2" and (m.version != 1 or rows != 6 or cols != 9):
        out.append(("error", "A WMS2 file must be version 1 with 6 zones of 9 parts."))
    if m.kind == "WMT1" and (rows != 2 or cols != 9):
        out.append(("error", "A WMT1 file must have 2 zones of 9 parts."))
    for i, t in enumerate(m.tiles):
        h, w = t.shape[:2]
        if not (1 <= w <= 4096 and 1 <= h <= 4096):
            out.append(("error", "Tile %d is %d x %d; the engine allows 1 to 4096 in each direction." % (i, w, h)))
    if (24 if m.kind == "WMS2" else 12) + n * rows * cols * 2 + len(m.tiles) * 12 + sum(t.nbytes for t in m.tiles) > 256 * 1024 * 1024:
        out.append(("error", "The file is bigger than the engine's 256 MB limit."))
    counts = part_counts(m.kind)
    if counts is None:
        out.append(("warning", "The part-count tables (meter_tables.py) are missing, so the per-car checks were skipped."))
    elif n == 55 and rows == len(counts[0]):
        for c in range(n):
            for z in range(rows):
                k = counts[c][z]
                for i in range(cols):
                    v = int(m.layouts[c, z, i])
                    if i < k and (v == EMPTY or v >= len(m.tiles)):
                        out.append(("error", "%s, zone %d, part %d must be a tile (the engine draws %d parts there)." % (car_name(c), z, i, k)))
                    elif i >= k and v != EMPTY:
                        out.append(("error", "%s, zone %d, part %d must be empty (the engine draws only %d parts there)." % (car_name(c), z, i, k)))
    if m.kind == "WMS2" and lamp_images() is None:
        out.append(("warning", "The stock lamp images (assets/racing_rev_off_m.png, off_n.png, on_m.png, on_n.png) were not found in %s, so the shift lamp check was skipped. Put the assets folder next to meters_edit.py." % ASSETS))
    elif m.kind == "WMS2":
        for c in range(n):
            for z in (2, 3):
                if lamp_ok(m, c, z) is False:
                    out.append(("warning", "%s, zone %d: the lamp tile is not the stock blank one, so the shift lamp will never light." % (car_name(c), z)))
    if len(out) > limit:
        more = len(out) - limit
        out = out[:limit] + [("error" if any(l == "error" for l, _ in out[limit:]) else "warning", "... and %d more." % more)]
    return out


def sprite_names(m, folder):
    """which PNG files of a sprite folder (the game's data/sprite) each tile is: {tile number: [file names without .png]},
    matched by pixels, so only exact copies are named"""
    import hashlib
    sizes = {(t.shape[1], t.shape[0]) for t in m.tiles}
    index = {}
    for f in os.listdir(folder):
        if not f.lower().endswith(".png"):
            continue
        try:
            im = Image.open(os.path.join(folder, f))
            if im.size not in sizes:
                continue
            index.setdefault(hashlib.md5(np.array(im.convert("RGBA"), np.uint8).tobytes()).hexdigest(), []).append(os.path.splitext(f)[0])
        except OSError:
            continue
    out = {}
    for i, t in enumerate(m.tiles):
        h = hashlib.md5(np.ascontiguousarray(t).tobytes()).hexdigest()
        if h in index:
            out[i] = sorted(index[h])
    return out


def rev_input(r):
    """what the engine feeds a rev needle (hud.c meter_input)"""
    return 0.96 * r + 0.04 if r > 0.1 else (5.0 * r * r + 0.05) * 0.96 + 0.04


def _blend(dst, src, px, py, add):
    h, w = src.shape[:2]
    x0, y0, x1, y1 = max(px, 0), max(py, 0), min(px + w, dst.shape[1]), min(py + h, dst.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    s = src[y0 - py:y1 - py, x0 - px:x1 - px].astype(np.float32) / 255.0
    a = s[..., 3:4]
    d = dst[y0:y1, x0:x1]
    if add:
        d[...] = np.minimum(d + s[..., :3] * a * 255.0, 255.0)
    else:
        d[...] = s[..., :3] * 255.0 * a + d * (1.0 - a)


def screen_preview(m, L, zone, kmh=0.0, rev=0.0, boost=0.0, lamp_lit=False, guides=False, bg=(24, 26, 32), margin=14):
    """the meters of car L, zone `zone`, drawn the way the engine does (hud.c draw_meters): every part centred on its (x, y)
    of the arcade's 640 x 480 screen, turned clockwise by a0 + a1 * input, glow parts added on.
    -> (picture cropped around the meters, [(part, x0, y0, x1, y1) in picture pixels], notes)"""
    if not T:
        raise MetersError("The part tables (meter_tables.py) are missing.")
    parts = T.LEGACY[L][4][zone] if m.kind == "WMT1" else T.V2[L][1][zone]
    count = T.LEGACY[L][3][zone] if m.kind == "WMT1" else T.V2[L][0][zone]
    canvas = np.zeros((480, 640, 3), np.float32)
    canvas[...] = bg
    notes, boxes = [], []
    slot, li = lamp_slot(m, L, zone), lamp_images()
    for i in range(count):
        x, y, a0, a1, inp, add = parts[i]
        t = int(m.layouts[L, zone, i])
        if t == EMPTY or t >= len(m.tiles):
            continue
        v = {0: 0.0, 1: kmh / 320.0, 2: rev_input(rev), 3: (boost + 0.5) * 0.4}[inp]
        deg = a0 + a1 * v
        px = m.tiles[t]
        if i == slot and lamp_lit and li:
            if lamp_ok(m, L, zone):
                px = li["on_m" if zone == 2 else "on_n"]
            else:
                notes.append("The lamp tile was changed, so the engine would not light the lamp.")
        im = Image.fromarray(px, "RGBA")
        if abs(deg) > 1e-6:
            im = im.rotate(-deg, resample=Image.BICUBIC, expand=True)
        rx, ry = int(round(x - im.width / 2)), int(round(y - im.height / 2))
        _blend(canvas, np.array(im, np.uint8), rx, ry, bool(add))
        boxes.append((i, rx, ry, rx + im.width, ry + im.height))
    pic = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    if guides:
        from PIL import ImageDraw
        d = ImageDraw.Draw(pic)
        for i in range(count):
            x, y = parts[i][0], parts[i][1]
            d.line((x - 5, y, x + 5, y), fill=(255, 255, 0, 255))
            d.line((x, y - 5, x, y + 5), fill=(255, 255, 0, 255))
        if slot is not None:
            d.rectangle((LAMP_POS[0] - 32, LAMP_POS[1] - 32, LAMP_POS[0] + 31, LAMP_POS[1] + 31), outline=(0, 255, 255, 255))
    if boxes:
        x0 = max(0, min(b[1] for b in boxes) - margin)
        y0 = max(0, min(b[2] for b in boxes) - margin)
        x1 = min(640, max(b[3] for b in boxes) + margin)
        y1 = min(480, max(b[4] for b in boxes) + margin)
    else:
        x0, y0, x1, y1 = 0, 0, 640, 480
    pic = pic.crop((x0, y0, x1, y1))
    return pic, [(i, a - x0, b - y0, c - x0, d_ - y0) for i, a, b, c, d_ in boxes], notes


# ================================================================================================ cli
def cli(argv):
    if len(argv) < 2 or argv[0] not in ("info", "sheet", "tile", "selftest", "check", "preview"):
        print(__doc__)
        return 2
    info = wmdc.inspect(argv[1])
    if info:
        print(argv[1], "-", wmdc.describe(info))
        print("This is encrypted; this tool cannot open it (see wmdc.py).")
        return 1
    m = Meters.load(argv[1])
    if argv[0] == "info":
        print(describe(m))
        for L in range(m.n_layouts):
            print("  layout %2d  %4d x %-4d  %2d cells" % (L, *m.sheet_size(L), len(m.cells(L))))
    elif argv[0] == "sheet":
        m.sheet(int(argv[2])).save(argv[3])
        print("wrote", argv[3])
    elif argv[0] == "tile":
        m.tile_image(int(argv[2])).save(argv[3])
        print("wrote", argv[3])
    elif argv[0] == "check":
        res = validate(m)
        for lv, tx in res:
            print("%-8s %s" % (lv, tx))
        print("engine check: %s" % ("OK" if not any(l == "error" for l, _ in res) else "the engine would ignore this file"))
        return 1 if any(l == "error" for l, _ in res) else 0
    elif argv[0] == "preview":
        pic, _b, notes = screen_preview(m, int(argv[2]), int(argv[3]), kmh=float(argv[5]) if len(argv) > 5 else 0.0,
                                        rev=float(argv[6]) if len(argv) > 6 else 0.0, lamp_lit=True, guides=True)
        pic.save(argv[4])
        print("wrote", argv[4], *notes)
    else:
        return selftest(argv[1], m)
    return 0


def selftest(path, m=None):
    raw = open(path, "rb").read()
    m = m or Meters.from_bytes(raw)
    assert m.to_bytes() == raw, "writing the file back changed it"
    snap = m.snapshot()
    m.replace_tile(0, Image.new("RGBA", (m.tiles[0].shape[1], m.tiles[0].shape[0]), (255, 0, 0, 255)))
    m.set_sheet(0, m.sheet(0).transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.FLIP_LEFT_RIGHT))
    m.remove_unused()
    r = Meters.from_bytes(m.to_bytes())
    assert r.n_layouts == m.n_layouts and len(r.tiles) == len(m.tiles)
    m.restore(snap)
    assert m.to_bytes() == raw, "undo did not restore the file"
    print("selftest ok: %d layouts, %d tiles; read -> write is byte-identical; edit, reload and undo work" % (m.n_layouts, len(m.tiles)))
    return 0


# ================================================================================================ the tab
HELP = """Meters  -  the gauge pictures of a plain WMS2 meters file (meters-v2.bin and the like)

1. Open meters file...  Pick a layout on the left.  Each layout is up to 6 rows of small pictures (dial faces, needles,
   small dials ...), shown here as one sheet; click a picture to select it (tile number and size are shown).
2. Export sheet / Export tile  save PNGs (with transparency) you can paint on in any editor.
3. Replace tile from PNG  puts new pixels into the selected picture (same size, or it is resized).  Tiles are shared:
   the needles of layout 0 are the needles of layout 1.  If other layouts use the tile you are asked whether to change it
   for all of them or to give only this layout its own copy (the file then holds one more tile).
4. Replace whole sheet from PNG  needs a PNG exactly the size of the exported sheet; every picture whose pixels differ is
   replaced (the same question about shared tiles applies).
5. Use tile number...  points the selected cell at another existing tile; Empty cell clears it.
6. Remove unused tiles  drops tiles no layout uses (renumbers the rest).  Undo goes back one step.
7. Save writes the plain WMS2 file (the old one is kept once as <file>.bak).

"As in game" draws the meters the way the engine does: every part at its fixed position, needles turned by the speed /
rev / boost sliders, "Shift lamp lit" shows the lamp (yellow crosses = part centres, cyan box = the lamp's 64 x 64 sprite).
The engine check (under the file name) applies the engine's own rules: each car and zone must have exactly the parts the
game's table expects, tiles 1 to 4096 pixels, nothing after the last tile.  Files with problems are ignored by the game.
WMT1 files (the older meters.bin, 2 zones per car) open and save too.

Not possible here: the game's own files (meters.bin, mt6meter.bin ... and every .txt next to them) are WMDC containers,
encrypted with a key this tool does not have, so it cannot open them or pack a WMDC file.  Not tried in the game.
"""


def build_tab(tab_parent, root, app=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from PIL import ImageDraw, ImageTk

    try:
        parent = tab_parent                     # no page scrolling: the panels scroll themselves (vpk_layout)
    except Exception:
        parent = tab_parent
    CW, CH = 560, 540

    class Tab:
        def __init__(self):
            self.m = None
            self.path = None
            self.undo = []
            self.dirty = False
            self.L = 0
            self.sel = None                 # (row, col)
            self.photo = None
            self.geom = []                  # cells of the layout on show, in sheet pixels
            sv = tk.StringVar
            self.status = UI.StatusLine("Open a plain meters file (WMS2, like meters-v2.bin).")
            self.name = sv(value="")
            self.info = sv(value="")
            self.cellinfo = sv(value="Click a picture in the sheet.")
            self.zoom = sv(value="1x")
            self.bg = sv(value="Checker")
            self.grid_on = tk.BooleanVar(value=True)
            self.tile_no = sv(value="")
            self.view = sv(value="Sheet")
            self.kmh, self.rev, self.boost = tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0)
            self.lamp_lit = tk.BooleanVar(value=False)
            self.guides = tk.BooleanVar(value=True)
            self.engine = sv(value="")
            self.hit = []
            self.pending = None
            self.names = {}
            self.build()

        # ------------------------------------------------------------ layout
        def build(self):
            # Blender-style: toolbar on top, status at the bottom, between them list | sheet viewer | panels (vpk_layout)
            top = ttk.Frame(parent, padding=(12, 8, 12, 2))
            top.pack(fill="x")
            ttk.Button(top, text="Open meters file…", command=self.open_dialog).pack(side="left")
            self.b_undo = ttk.Button(top, text="Undo", command=self.do_undo, state="disabled")
            self.b_undo.pack(side="left", padx=6)
            ttk.Button(top, text="Save", command=self.save).pack(side="left")
            ttk.Button(top, text="Save as…", command=self.save_as).pack(side="left", padx=6)
            ttk.Button(top, text="Help", command=lambda: messagebox.showinfo("Meters", HELP, parent=root)).pack(side="left")
            ttk.Label(parent, textvariable=self.name, foreground="#8f9eb2", padding=(14, 0)).pack(fill="x")
            ttk.Label(parent, textvariable=self.info, foreground="#8f9eb2", padding=(14, 0), wraplength=900, justify="left").pack(fill="x")
            ef = ttk.Frame(parent, padding=(12, 2, 12, 4))
            ef.pack(fill="x")
            self.engine_lbl = ttk.Label(ef, textvariable=self.engine, foreground="#8f9eb2")
            self.engine_lbl.pack(side="left")
            ttk.Button(ef, text="Engine check…", command=lambda: self.check(True)).pack(side="left", padx=8)
            self.status.bar(parent, ttk, tk, wraplength=900, fill="x", side="bottom", padx=12, pady=6)
            ttk.Separator(parent).pack(fill="x", side="bottom")
            ws = self.ws = VL.Workspace(parent, left_w=330, right_w=400)

            left = ws.left
            cols = ("n", "car", "size", "cells", "own")
            lf = ttk.Frame(left)
            lf.pack(fill="both", expand=True)
            self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=22, selectmode="browse")
            for c, t, w, a in (("n", "#", 30, "center"), ("car", "Car", 120, "w"), ("size", "Sheet", 70, "e"), ("cells", "Cells", 38, "e"), ("own", "Own", 36, "e")):
                self.tree.heading(c, text=t)
                self.tree.column(c, width=w, anchor=a, stretch=(c == "car"))
            sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.tree.bind("<<TreeviewSelect>>", lambda _e: self.pick_layout())

            right = ws.center
            bar = ttk.Frame(right)
            bar.pack(fill="x", side="bottom", pady=(4, 0))
            gv = ttk.Frame(right)
            gv.pack(fill="x", side="bottom", pady=(0, 2))
            cf = ttk.Frame(right)
            cf.pack(fill="both", expand=True)
            self.canvas = tk.Canvas(cf, width=420, height=380, background="#202a38", highlightthickness=0)
            vs = ttk.Scrollbar(cf, orient="vertical", command=self.canvas.yview)
            hs = ttk.Scrollbar(cf, orient="horizontal", command=self.canvas.xview)
            self.canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
            hs.pack(side="bottom", fill="x")
            vs.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)
            self.canvas.bind("<Button-1>", self.click)
            for lab, var, vals, w in (("Zoom", self.zoom, ["1x", "2x"], 4), ("Background", self.bg, ["Checker", "Dark", "Light"], 8)):
                ttk.Label(bar, text=lab).pack(side="left", padx=(0, 2))
                c = ttk.Combobox(bar, textvariable=var, values=vals, state="readonly", width=w)
                c.pack(side="left", padx=(0, 10))
                c.bind("<<ComboboxSelected>>", lambda _e: self.draw())
            ttk.Checkbutton(bar, text="Show picture borders", variable=self.grid_on, command=self.draw).pack(side="left")
            ws.flow(bar, right)
            ttk.Label(gv, text="View").pack(side="left", padx=(0, 2))
            vc = ttk.Combobox(gv, textvariable=self.view, values=["Sheet", "As in game"], state="readonly", width=10)
            vc.pack(side="left", padx=(0, 8))
            vc.bind("<<ComboboxSelected>>", lambda _e: self.draw())
            self.zone_cb = ttk.Combobox(gv, state="readonly", width=26)
            self.zone_cb.pack(side="left", padx=(0, 8))
            self.zone_cb.bind("<<ComboboxSelected>>", lambda _e: (setattr(self, "sel", None), self.draw()))
            for lab, var, hi in (("km/h", self.kmh, 320), ("Rev", self.rev, 1), ("Boost", self.boost, 2)):
                ttk.Label(gv, text=lab).pack(side="left")
                ttk.Scale(gv, from_=-0.5 if lab == "Boost" else 0, to=hi, variable=var, length=80,
                          command=lambda _v: self.redraw_soon()).pack(side="left", padx=(2, 8))
            ttk.Checkbutton(gv, text="Shift lamp lit", variable=self.lamp_lit, command=self.draw).pack(side="left")
            ttk.Checkbutton(gv, text="Guides", variable=self.guides, command=self.draw).pack(side="left", padx=6)
            ws.flow(gv, right)

            ed = ws.section("Selected picture").body
            ttk.Label(ed, textvariable=self.cellinfo, foreground="#c9d4e3", wraplength=340, justify="left").pack(anchor="w")
            r1 = ttk.Frame(ed)
            r1.pack(fill="x", pady=3)
            for t, f in (("Export tile PNG…", self.export_tile), ("Replace tile from PNG…", self.replace_tile)):
                ttk.Button(r1, text=t, command=f).pack(side="left", padx=(0, 4))
            ws.flow(r1)
            r1b = ttk.Frame(ed)
            r1b.pack(fill="x", pady=3)
            ttk.Label(r1b, text="Use tile number").pack(side="left")
            ttk.Entry(r1b, textvariable=self.tile_no, width=6).pack(side="left", padx=3)
            ttk.Button(r1b, text="Set", command=self.use_tile).pack(side="left")
            ttk.Button(r1b, text="Empty cell", command=self.empty_cell).pack(side="left", padx=6)
            ws.flow(r1b)
            sh = ws.section("Whole layout").body
            r2 = ttk.Frame(sh)
            r2.pack(fill="x")
            for t, f in (("Export sheet PNG…", self.export_sheet), ("Replace whole sheet from PNG…", self.replace_sheet),
                         ("Remove unused tiles", self.remove_unused), ("Name tiles from sprite folder…", self.name_tiles)):
                ttk.Button(r2, text=t, command=f).pack(side="left", padx=(0, 4), pady=2)
            ws.flow(r2)

        # ------------------------------------------------------------ opening / saving
        def confirm_discard(self):
            return UI.discard_changes(messagebox, root, "this file", self.dirty)

        def open_dialog(self):
            if not self.confirm_discard():
                return
            d = None
            try:
                import wpk_view
                d = wpk_view.load_prefs().get("meters_dir")
            except Exception:
                pass
            f = filedialog.askopenfilename(parent=root, title="Open a meters file", initialdir=d,
                                           filetypes=[("Meters", "*.bin"), ("All files", "*.*")])
            if f:
                self.open_path(f)

        def open_path(self, f):
            try:
                m = Meters.load(f)
            except (MetersError, OSError) as ex:
                messagebox.showerror("Meters", "%s:\n\n%s" % (os.path.basename(f), ex), parent=root)
                self.status.set("Could not open %s." % os.path.basename(f))
                return
            self.m, self.path, self.undo, self.dirty, self.sel, self.L = m, f, [], False, None, 0
            self.b_undo.configure(state="disabled")
            self.name.set(os.path.basename(f))
            try:
                import wpk_view
                wpk_view.save_prefs(meters_dir=os.path.dirname(os.path.abspath(f)))
            except Exception:
                pass
            self.fill()
            self.status.set("Opened %s." % os.path.basename(f))

        def save(self):
            if self.m and self.path:
                self.write(self.path)

        def save_as(self):
            if not self.m:
                return
            f = filedialog.asksaveasfilename(parent=root, defaultextension=".bin", initialfile=os.path.basename(self.path or "meters.bin"),
                                             filetypes=[("Meters", "*.bin")])
            if f:
                self.write(f)

        def write(self, f):
            bad = [t for l, t in validate(self.m) if l == "error"]
            if bad and not messagebox.askyesno("Engine check", "The game would ignore this file:\n\n  " + "\n  ".join(bad[:8]) +
                                               "\n\nSave it anyway?", icon="warning", parent=root):
                return
            try:
                bak = self.m.save(f)
            except OSError as ex:
                messagebox.showerror("Meters", "Cannot save:\n%s" % ex, parent=root)
                return
            self.path, self.dirty = f, False
            self.name.set(os.path.basename(f))
            self.status.set("Saved %s%s (plain WMS2, not a game WMDC file)." % (os.path.basename(f), "; the old file is kept as .bak" if bak else ""))

        # ------------------------------------------------------------ list + sheet
        def fill(self):
            m = self.m
            self.info.set(describe(m))
            use = m.usage()
            self.tree.delete(*self.tree.get_children())
            for L in range(m.n_layouts):
                cs = m.cells(L)
                own = len({t for _r, _c, t, *_ in cs if use.get(t, set()) == {L}})
                self.tree.insert("", "end", iid=str(L), values=(L, car_name(L), "%d x %d" % m.sheet_size(L), len(cs), own))
            names = (["Zone 0: style 0, day", "Zone 1: style 0, night", "Zone 2: racing style, day", "Zone 3: racing style, night",
                      "Zone 4: style 2, day", "Zone 5: style 2, night"] if m.kind == "WMS2" else ["Zone 0: day", "Zone 1: night"])[:m.layouts.shape[1]]
            self.zone_cb.configure(values=names)
            if self.zone_cb.current() < 0 or self.zone_cb.current() >= len(names):
                self.zone_cb.current(0)
            self.check()
            L = min(self.L, m.n_layouts - 1)
            self.tree.selection_set(str(L))
            self.pick_layout()

        def pick_layout(self):
            s = self.tree.selection()
            if not s or not self.m:
                return
            self.L = int(s[0])
            self.sel = None
            self.draw()

        def background(self, w, h):
            b = self.bg.get()
            if b == "Dark":
                return Image.new("RGBA", (w, h), (24, 26, 32, 255))
            if b == "Light":
                return Image.new("RGBA", (w, h), (235, 235, 235, 255))
            a = np.zeros((h, w, 4), np.uint8)
            yy, xx = np.mgrid[0:h, 0:w]
            a[...] = np.where(((xx // 8 + yy // 8) % 2 == 0)[..., None], (150, 150, 150, 255), (110, 110, 110, 255))
            return Image.fromarray(a, "RGBA")

        def zone_no(self):
            return max(self.zone_cb.current(), 0)

        def redraw_soon(self):
            if self.pending:
                parent.after_cancel(self.pending)
            self.pending = parent.after(60, self.draw)

        def draw(self):
            self.pending = None
            if not self.m:
                return
            L, z = self.L, (2 if self.zoom.get() == "2x" else 1)
            if self.view.get() == "As in game":
                zone = self.zone_no()
                try:
                    bg, boxes, notes = screen_preview(self.m, L, zone, kmh=self.kmh.get(), rev=self.rev.get(), boost=self.boost.get(),
                                                      lamp_lit=self.lamp_lit.get(), guides=self.guides.get())
                except MetersError as ex:
                    self.status.set(str(ex), "error")
                    return
                self.hit, self.geom = boxes, []
                if self.sel and self.sel[0] == zone:
                    for i, x0, y0, x1, y1 in boxes:
                        if i == self.sel[1]:
                            ImageDraw.Draw(bg).rectangle((x0, y0, x1 - 1, y1 - 1), outline=(0, 200, 255, 255), width=2)
                if notes:
                    self.status.set(" ".join(notes), "warning")
            else:
                im = self.m.sheet(L)
                bg = self.background(*im.size)
                bg.alpha_composite(im)
                self.geom, self.hit = self.m.cells(L), []
                if self.grid_on.get():
                    d = ImageDraw.Draw(bg)
                    for _r, _c, _t, x, y, w, h in self.geom:
                        d.rectangle((x, y, x + w - 1, y + h - 1), outline=(255, 255, 255, 90))
                if self.sel:
                    for r, c, _t, x, y, w, h in self.geom:
                        if (r, c) == self.sel:
                            ImageDraw.Draw(bg).rectangle((x, y, x + w - 1, y + h - 1), outline=(0, 200, 255, 255), width=2)
            if z != 1:
                bg = bg.resize((bg.width * z, bg.height * z), Image.NEAREST)
            self.photo = ImageTk.PhotoImage(bg)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
            self.canvas.configure(scrollregion=(0, 0, bg.width, bg.height))
            self.show_cell()

        def click(self, ev):
            if not self.m:
                return
            z = 2 if self.zoom.get() == "2x" else 1
            x, y = self.canvas.canvasx(ev.x) / z, self.canvas.canvasy(ev.y) / z
            if self.view.get() == "As in game":
                for i, x0, y0, x1, y1 in reversed(self.hit):
                    if x0 <= x < x1 and y0 <= y < y1:
                        self.sel = (self.zone_no(), i)
                        self.draw()
                        return
            else:
                for r, c, _t, cx, cy, w, h in self.geom:
                    if cx <= x < cx + w and cy <= y < cy + h:
                        self.sel = (r, c)
                        self.draw()
                        return
            self.sel = None
            self.draw()

        def cell(self):
            if not self.m or not self.sel:
                return None
            r, c = self.sel
            if r >= self.m.layouts.shape[1] or c >= self.m.layouts.shape[2]:
                return None
            t = int(self.m.layouts[self.L, r, c])
            if t == EMPTY or t >= len(self.m.tiles):
                return None
            h, w = self.m.tiles[t].shape[:2]
            return (r, c, t, 0, 0, w, h)

        def show_cell(self):
            q = self.cell()
            if not q:
                self.cellinfo.set("Click a picture in the sheet.")
                return
            r, c, t, _x, _y, w, h = q
            use = self.m.usage().get(t, set())
            self.cellinfo.set("Layout %d, row %d, cell %d: tile %d, %d x %d pixels, used by %d layout(s)%s." % (
                self.L, r, c, t, w, h, len(use), "" if len(use) < 2 else " (shared: " + ", ".join(str(x) for x in sorted(use)[:8]) + (" ..." if len(use) > 8 else "") + ")"))
            self.tile_no.set(str(t))
            if self.names.get(t):
                self.cellinfo.set(self.cellinfo.get() + "  Sprite: " + ", ".join(self.names[t][:3]) + (" ..." if len(self.names[t]) > 3 else ""))

        # ------------------------------------------------------------ editing
        def push(self):
            self.undo.append(self.m.snapshot())
            self.b_undo.configure(state="normal")
            self.dirty = True

        def check(self, show=False):
            """the engine's rules (meter_pack.h) on the file as it is now"""
            if not self.m:
                return []
            res = validate(self.m)
            er = [t for l, t in res if l == "error"]
            wa = [t for l, t in res if l == "warning"]
            if er:
                self.engine.set("Engine check: %d problem(s), the game would ignore this file." % len(er))
                self.engine_lbl.configure(foreground="#f85149")
            elif wa:
                self.engine.set("Engine check: loads, but %d warning(s)." % len(wa))
                self.engine_lbl.configure(foreground="#e8710a")
            else:
                self.engine.set("Engine check: OK, the game accepts this file.")
                self.engine_lbl.configure(foreground="#3fb950")
            if show:
                messagebox.showinfo("Engine check", "\n".join("%s: %s" % (l.upper(), t) for l, t in res[:25]) or
                                    "The file passes every check the engine makes (part counts per car and zone, tile sizes, file layout).", parent=root)
            return res

        def name_tiles(self):
            if not self.m:
                return
            d = filedialog.askdirectory(parent=root, title="The game's sprite folder (data/sprite)")
            if not d:
                return
            self.status.set("Looking for the tiles' source pictures in %s ..." % d)
            root.update_idletasks()
            try:
                self.names = sprite_names(self.m, d)
            except OSError as ex:
                messagebox.showerror("Meters", str(ex), parent=root)
                return
            self.status.set("%d of %d tiles matched a sprite file; click a picture to see its name." % (len(self.names), len(self.m.tiles)))
            self.show_cell()

        def lamp_cell(self, r, c, L=None):
            """True when (r, c) of the car is a shift lamp tile the engine is lighting"""
            L = self.L if L is None else L
            return lamp_slot(self.m, L, r) == c and bool(lamp_ok(self.m, L, r))

        def lamp_warn(self):
            return messagebox.askyesno("Shift lamp tile", "This is the shift lamp's tile. The engine only lights the lamp while the tile is "
                                       "the original blank one. Change it anyway?", icon="warning", parent=root)

        def need_cell(self):
            q = self.cell()
            if not q:
                self.status.set("Click a picture in the sheet first.")
            return q

        def pick_png(self, title):
            return filedialog.askopenfilename(parent=root, title=title, filetypes=[("PNG / image", "*.png *.tga *.bmp *.jpg"), ("All files", "*.*")])

        def export_tile(self):
            q = self.need_cell()
            if not q:
                return
            f = filedialog.asksaveasfilename(parent=root, defaultextension=".png", initialfile="tile_%d.png" % q[2], filetypes=[("PNG", "*.png")])
            if f:
                self.m.tile_image(q[2]).save(f)
                self.status.set("Wrote %s." % os.path.basename(f))

        def export_sheet(self):
            if not self.m:
                return
            f = filedialog.asksaveasfilename(parent=root, defaultextension=".png", initialfile="layout_%d.png" % self.L, filetypes=[("PNG", "*.png")])
            if f:
                self.m.sheet(self.L).save(f)
                self.status.set("Wrote %s (%d x %d)." % ((os.path.basename(f),) + self.m.sheet_size(self.L)))

        def shared_choice(self, tiles):
            """None = cancel, True = change for every layout, False = a copy for this layout only"""
            use = self.m.usage()
            sh = [t for t in tiles if len(use.get(t, set()) - {self.L}) > 0]
            if not sh:
                return True
            n = len(set().union(*[use[t] for t in sh]) - {self.L})
            return messagebox.askyesnocancel("Shared tile", "%d of these tile(s) are also used by %d other layout(s).\n\nYes = change them for every layout.\n"
                                             "No = give only this layout its own copy (the file gets %d more tile(s)).\nCancel = stop." % (len(sh), n, len(sh)), parent=root)

        def replace_tile(self):
            q = self.need_cell()
            if not q:
                return
            r, c, t, _x, _y, w, h = q
            if self.lamp_cell(r, c) and not self.lamp_warn():
                return
            f = self.pick_png("New picture for tile %d (%d x %d)" % (t, w, h))
            if not f:
                return
            try:
                im = Image.open(f).convert("RGBA")
            except Exception as ex:
                messagebox.showerror("Meters", "Cannot read the picture:\n%s" % ex, parent=root)
                return
            resize = False
            if im.size != (w, h):
                if not messagebox.askyesno("Size", "The picture is %d x %d, the tile is %d x %d.\nResize the picture?" % (*im.size, w, h), parent=root):
                    return
                resize = True
            every = self.shared_choice([t])
            if every is None:
                return
            self.push()
            try:
                if every:
                    self.m.replace_tile(t, im, resize)
                else:
                    self.m.new_tile_for(self.L, r, c, im, resize)
            except MetersError as ex:
                self.do_undo()
                messagebox.showwarning("Meters", str(ex), parent=root)
                return
            self.fill()
            self.status.set("Tile %d replaced%s." % (t, "" if every else " (a copy for layout %d only)" % self.L))

        def replace_sheet(self):
            if not self.m:
                return
            w, h = self.m.sheet_size(self.L)
            f = self.pick_png("New sheet for layout %d (%d x %d)" % (self.L, w, h))
            if not f:
                return
            try:
                im = Image.open(f).convert("RGBA")
            except Exception as ex:
                messagebox.showerror("Meters", "Cannot read the picture:\n%s" % ex, parent=root)
                return
            resize = False
            if im.size != (w, h):
                if not messagebox.askyesno("Size", "The picture is %d x %d, the sheet is %d x %d.\nResize the picture?" % (*im.size, w, h), parent=root):
                    return
                resize = True
            a = np.array(im.resize((w, h), Image.LANCZOS) if resize else im, np.uint8)
            diff = [t for _r, _c, t, x, y, tw, th in self.m.cells(self.L) if not np.array_equal(a[y:y + th, x:x + tw], self.m.tiles[t])]
            if not diff:
                self.status.set("The picture is the same as the sheet: nothing to change.")
                return
            if any(self.lamp_cell(z, LAMP_SLOT) and int(self.m.layouts[self.L, z, LAMP_SLOT]) in diff for z in (2, 3) if self.m.kind == "WMS2") and not self.lamp_warn():
                return
            every = self.shared_choice(diff)
            if every is None:
                return
            self.push()
            try:
                ch, add = self.m.set_sheet(self.L, im, everywhere=every, resize=resize)
            except MetersError as ex:
                self.do_undo()
                messagebox.showwarning("Meters", str(ex), parent=root)
                return
            self.fill()
            self.status.set("Sheet replaced: %d picture(s) changed%s." % (ch, ", %d tile(s) added" % add if add else ""))

        def use_tile(self):
            q = self.need_cell()
            if not q:
                return
            if self.lamp_cell(q[0], q[1]) and not self.lamp_warn():
                return
            try:
                t = int(self.tile_no.get())
                self.push()
                self.m.set_cell(self.L, q[0], q[1], t)
            except (ValueError, MetersError) as ex:
                if self.undo and isinstance(ex, MetersError):
                    self.do_undo()
                self.status.set("Not a tile number: %s" % ex)
                return
            self.fill()
            self.status.set("Cell now shows tile %d." % t)

        def empty_cell(self):
            q = self.need_cell()
            if not q:
                return
            if self.lamp_cell(q[0], q[1]) and not self.lamp_warn():
                return
            self.push()
            self.m.set_cell(self.L, q[0], q[1], None)
            self.sel = None
            self.fill()
            self.status.set("Cell emptied.")

        def remove_unused(self):
            if not self.m:
                return
            self.push()
            n = self.m.remove_unused()
            if not n:
                self.undo.pop()
                self.b_undo.configure(state="normal" if self.undo else "disabled")
                self.status.set("Every tile is used by some layout.")
                return
            self.sel = None
            self.fill()
            self.status.set("%d unused tile(s) removed." % n)

        def do_undo(self):
            if not self.undo or not self.m:
                return
            self.m.restore(self.undo.pop())
            self.b_undo.configure(state="normal" if self.undo else "disabled")
            self.dirty = True
            self.sel = None
            self.fill()
            self.status.set("Undone.")

    return Tab()


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
