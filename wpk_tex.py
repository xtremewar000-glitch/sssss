#!/usr/bin/env python3
"""wpk_tex: look at, export and replace the textures of a car pack (.wpk, WPK3).

Why: the window glass, the lamps and the plate are not on the page the stickers (.vpk) are blended onto; they have
their own textures in the car's .wpk (the glass is <car>_body02_cola01, 256 x 256 with its own transparency).
Painting a decal into one of those textures puts it on the car for good, for everybody - the practical way to get a
windscreen banner or a sun strip.  export -> edit in any image editor -> import back.

    python vpk_tool.py textures CAR.wpk
    python vpk_tool.py texexport CAR.wpk NAME OUT.png
    python vpk_tool.py texguide  CAR.wpk NAME OUT.png [SIZE]       the texture with the UV borders over it
    python vpk_tool.py teximport CAR.wpk NAME IMAGE OUT [--size N] [--dither] [--car]

What it keeps: every other byte of the pack is unchanged.  The pixels are rewritten in the texture's own format
(RGB565, RGBA4444 or RGBA5551, as render.c tex_upload reads them; ASTC textures are refused), a pixel you did not
touch comes back exactly as it was (the export expands n-bit channels the way the GPU does, the import rounds back),
and when the picture's size changes the vertex / index data that follows is moved and the batch table's offsets are
shifted.  Every file is read back and compared before it is written (_verify).

File layout (WPK3): header {"WPK3", n_tex, n_chunk, n_batch, f32 chunk_size, tex_off, chunk_off, batch_off},
texture table (64 bytes each: char name[48], u16 w, u16 h, u32 fmt, u32 off, u32 size; size 0 = borrowed from the
stock pack by name, as the aero kits do), chunk table (36 bytes), batch table (28 bytes: u16 tex, u16 flags,
u32 vtx_off, u32 vtx_count, u32 idx_off, u32 idx_count, ...), then the texture data, then the vertex (32 bytes
each) and index (u16) data the batch table points to.  Rows of a texture run bottom-up (row 0 = V 0); the exported
PNG is upright, the way an image viewer and the UV templates show it.
"""
import glob
import os
import re
import shutil
import struct

import numpy as np
from PIL import Image

HEAD = struct.Struct("<4sIIIfIII")
TEX = struct.Struct("<48sHHIII")
CHUNK_REC, BATCH_REC, TEX_REC, STRIDE = 36, 28, 64, 32
WPK_ASTC = 3                                   # render.h: fmt >= WPK_ASTC is a compressed texture
FMT_NAMES = {0: "RGB565", 1: "RGBA4444", 2: "RGBA5551"}
FMT_BITS = {0: (5, 6, 5, 0), 1: (4, 4, 4, 4), 2: (5, 5, 5, 1)}     # render.c TEX_BITS: from the top, red first
MAX_SIDE = 2048
PAINT = re.compile(r"^p(\d+):(.*)$", re.I)


class TexError(Exception):
    pass


def _stem(n):
    return os.path.splitext(n)[0].lower()


# ----------------------------------------------------------------------------------------------- pixels
def _expand(v, bits):
    """an n-bit channel to 8 bits the way the GPU does it (v / max): the top bits repeated"""
    v = v.astype(np.uint16)
    if bits == 1:
        return (v * 255).astype(np.uint8)
    if bits == 4:
        return (v * 17).astype(np.uint8)
    if bits == 5:
        return ((v << 3) | (v >> 2)).astype(np.uint8)
    return ((v << 2) | (v >> 4)).astype(np.uint8)


def decode16(raw, w, h, fmt):
    """texture bytes -> RGBA uint8, h x w x 4, rows as stored (bottom-up)"""
    if fmt not in FMT_BITS:
        raise TexError("This texture is in format %d (compressed); only RGB565, RGBA4444 and RGBA5551 can be edited." % fmt)
    px = np.frombuffer(raw, "<u2", count=w * h).reshape(h, w)
    if fmt == 0:
        r, g, b, a = px >> 11, (px >> 5) & 63, px & 31, None
    elif fmt == 1:
        r, g, b, a = px >> 12, (px >> 8) & 15, (px >> 4) & 15, px & 15
    else:
        r, g, b, a = px >> 11, (px >> 6) & 31, (px >> 1) & 31, px & 1
    bits = FMT_BITS[fmt]
    out = np.empty((h, w, 4), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = _expand(r, bits[0]), _expand(g, bits[1]), _expand(b, bits[2])
    out[..., 3] = 255 if a is None else _expand(a, bits[3])
    return out


_BAYER = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16.0


def _quant(v8, bits, dither):
    """8-bit values -> n-bit: rounded; with dither (ordered), only where the value is not already (nearly) an n-bit
    value, so what the export gave comes back exactly"""
    mx = (1 << bits) - 1
    x = v8.astype(np.float32) * (mx / 255.0)
    q = np.rint(x)
    if dither is not None:
        q = np.where(np.abs(x - q) < 0.1, q, np.floor(x + dither))
    return np.clip(q, 0, mx).astype(np.uint16)


def encode16(rgba, fmt, dither=False):
    """RGBA uint8 h x w x 4 (rows bottom-up) -> texture bytes in format fmt"""
    if fmt not in FMT_BITS:
        raise TexError("This texture is in format %d (compressed); only RGB565, RGBA4444 and RGBA5551 can be edited." % fmt)
    h, w = rgba.shape[:2]
    dd = np.tile(_BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w] if dither else None
    bits = FMT_BITS[fmt]
    r, g, b = (_quant(rgba[..., i], bits[i], dd) for i in range(3))
    if fmt == 0:
        px = (r << 11) | (g << 5) | b
    elif fmt == 1:
        px = (r << 12) | (g << 8) | (b << 4) | _quant(rgba[..., 3], 4, dd)
    else:
        px = (r << 11) | (g << 6) | (b << 1) | (rgba[..., 3] >= 128).astype(np.uint16)
    return px.astype("<u2").tobytes()


# ----------------------------------------------------------------------------------------------- the file
def _parse(b, path="file"):
    if len(b) < HEAD.size:
        raise TexError("%s is too short to be a car pack." % path)
    magic, nt, nc, nb, cs, to, co, bo = HEAD.unpack_from(b, 0)
    if magic != b"WPK3":
        raise TexError("%s is not a WPK3 car pack." % path)
    if to + TEX_REC * nt > len(b) or co + CHUNK_REC * nc > len(b) or bo + BATCH_REC * nb > len(b):
        raise TexError("%s: the tables run past the end of the file." % path)
    tex = []
    for i in range(nt):
        nm, w, h, fmt, off, sz = TEX.unpack_from(b, to + TEX_REC * i)
        tex.append(dict(raw=nm, name=nm.split(b"\0")[0].decode("latin1"), w=w, h=h, fmt=fmt, off=off, sz=sz))
    bat = []
    for i in range(nb):
        t, fl, vo, vn, io, inn = struct.unpack_from("<HHIIII", b, bo + BATCH_REC * i)
        bat.append(dict(vo=vo, vn=vn, io=io, inn=inn))
    return dict(head=(magic, nt, nc, nb, cs, to, co, bo), tex=tex, bat=bat)


def list_textures(b, path="file"):
    """-> [dict(index, name, w, h, fmt, fmt_name, size, borrowed, editable)]"""
    out = []
    for i, t in enumerate(_parse(b, path)["tex"]):
        out.append(dict(index=i, name=t["name"], w=t["w"], h=t["h"], fmt=t["fmt"],
                        fmt_name=FMT_NAMES.get(t["fmt"], "ASTC" if t["fmt"] >= WPK_ASTC else "?"), size=t["sz"],
                        borrowed=t["sz"] == 0, editable=t["sz"] > 0 and t["fmt"] in FMT_BITS))
    return out


def find_texture(textures, key):
    """index of a texture by number, exact name or name without extension (a stem that is the stock texture's and its
    p1: p2: ... copies means the stock one); ambiguous or unknown -> TexError listing what there is"""
    names = [t["name"] for t in textures]
    k = str(key).strip()
    if k.isdigit() and int(k) < len(names):
        return int(k)
    hits = [i for i, n in enumerate(names) if n.lower() == k.lower()]
    if not hits:
        hits = [i for i, n in enumerate(names) if _stem(n) == _stem(k) and not PAINT.match(n)]
    if not hits:
        hits = [i for i, n in enumerate(names) if _stem(n) == _stem(k)]
    if len(hits) != 1:
        raise TexError("%s texture '%s'. The pack has: %s" % ("No" if not hits else "More than one", k,
                                                              ", ".join("%d %s" % (i, n) for i, n in enumerate(names))))
    return hits[0]


def export_image(b, idx, path="file", size=None):
    """the texture as an upright RGBA picture (what an image viewer shows, what the UV templates line up with).
    size: the longest side to scale it to (to paint on a small texture in detail); default as it is."""
    p = _parse(b, path)
    t = p["tex"][idx]
    if t["sz"] == 0:
        raise TexError("'%s' is borrowed from the stock pack: open the stock car's .wpk." % t["name"])
    if t["off"] + t["sz"] > len(b) or t["sz"] < t["w"] * t["h"] * 2:
        raise TexError("'%s' is damaged." % t["name"])
    px = decode16(b[t["off"]:t["off"] + t["sz"]], t["w"], t["h"], t["fmt"])
    im = Image.fromarray(np.ascontiguousarray(px[::-1]), "RGBA")
    if size and size != max(t["w"], t["h"]):
        k = size / max(t["w"], t["h"])
        im = im.resize((max(1, int(round(t["w"] * k))), max(1, int(round(t["h"] * k)))),
                       Image.LANCZOS if k > 1 else Image.BOX)
    return im


def _is_pot(n):
    return n > 0 and n & (n - 1) == 0


def import_image(b, idx, im, size=None, dither=False, path="file"):
    """replace texture idx by the picture im (upright, any mode) -> (new file bytes, notes)
    size: the longest side of the new texture (a power of two up to 2048). Default: the picture's own size when it is a
    power of two up to 2048 with the texture's proportions (a picture exported at 1024 comes back at 1024), else the
    texture's size now."""
    p = _parse(b, path)
    t = p["tex"][idx]
    if t["sz"] == 0:
        raise TexError("'%s' is borrowed from the stock pack: edit the stock car's .wpk." % t["name"])
    if t["fmt"] not in FMT_BITS:
        raise TexError("'%s' is compressed (format %d); only RGB565, RGBA4444 and RGBA5551 can be edited." % (t["name"], t["fmt"]))
    w, h = t["w"], t["h"]
    notes = []
    if not size and (im.width, im.height) != (w, h) and _is_pot(im.width) and _is_pot(im.height) \
            and max(im.size) <= MAX_SIDE and abs(im.width / im.height - w / h) < 0.02:
        size = max(im.size)
    if size and size != max(w, h):
        if not _is_pot(size) or not 4 <= size <= MAX_SIDE:
            raise TexError("The size must be a power of two from 4 to %d, not %s." % (MAX_SIDE, size))
        k = size / max(w, h)
        w, h = max(1, int(round(w * k))), max(1, int(round(h * k)))
        notes.append("size %d x %d -> %d x %d" % (t["w"], t["h"], w, h))
    im = im.convert("RGBA")
    if im.size != (w, h):
        if abs(im.width / im.height - w / h) > 0.02:
            notes.append("the picture is %d x %d, the texture %d x %d: stretched" % (im.width, im.height, w, h))
        res = Image.BOX if (im.width >= w and im.height >= h and im.width % w == 0 and im.height % h == 0) else Image.LANCZOS
        im = im.resize((w, h), res)
    rgba = np.ascontiguousarray(np.asarray(im)[::-1])                       # upright -> rows bottom-up
    if t["fmt"] == 0 and rgba[..., 3].min() < 255:
        notes.append("this texture has no alpha (RGB565): the picture's transparency is ignored")
        rgba = rgba.copy()
        rgba[..., 3] = 255
    if t["fmt"] == 2:
        notes.append("this texture has 1-bit alpha (RGBA5551): see-through or not, nothing between")
    raw = encode16(rgba, t["fmt"], dither)
    return rebuild(b, {idx: (w, h, raw)}, path), notes


def rebuild(b, repl, path="file"):
    """repl: {texture index: (w, h, raw bytes in its own format)} -> the new file bytes (checked)"""
    p = _parse(b, path)
    magic, nt, nc, nb, cs, to, co, bo = p["head"]
    tex, bat = p["tex"], p["bat"]
    rng = {}
    for i, t in enumerate(tex):
        if t["sz"]:
            rng.setdefault((t["off"], t["sz"]), []).append(i)
    full = {}
    for i, (w, h, raw) in repl.items():
        t = tex[i]
        if not t["sz"] or t["fmt"] not in FMT_BITS or len(raw) != w * h * 2:
            raise TexError("'%s' cannot be replaced." % t["name"])
        for u in rng[(t["off"], t["sz"])]:                  # textures that share these pixels change together
            full[u] = (w, h, raw)
    keys = sorted(rng)
    for (o1, s1), (o2, s2) in zip(keys, keys[1:]):
        if o2 < o1 + s1:
            raise TexError("%s: texture data overlaps." % path)
    start, end = keys[0][0], max(o + s for o, s in keys)
    if end > len(b):
        raise TexError("%s: a texture runs past the end of the file." % path)
    if max(to + TEX_REC * nt, co + CHUNK_REC * nc, bo + BATCH_REC * nb) > start:
        raise TexError("%s: the tables are not in front of the texture data." % path)
    parts, newpos, cur, prev = [], {}, start, start
    for (o, s) in keys:
        if o > prev:
            gap = b[prev:o]
            parts.append(gap)
            cur += len(gap)
        u0 = rng[(o, s)][0]
        data = full[u0][2] if u0 in full else b[o:o + s]
        newpos[(o, s)] = (cur, len(data))
        parts.append(data)
        cur += len(data)
        prev = o + s
    delta = cur - end
    out = bytearray(b[:start])
    out += b"".join(parts)
    out += b[end:]
    for i, t in enumerate(tex):
        if not t["sz"]:
            continue
        noff, nsz = newpos[(t["off"], t["sz"])]
        w, h = (full[i][0], full[i][1]) if i in full else (t["w"], t["h"])
        TEX.pack_into(out, to + TEX_REC * i, t["raw"], w, h, t["fmt"], noff, nsz)
    for i, r in enumerate(bat):
        for k, (off, n, width) in enumerate(((r["vo"], r["vn"], STRIDE), (r["io"], r["inn"], 2))):
            if n == 0 and off < end:
                continue
            if off >= end:
                new = off + delta
            elif off + n * width <= start:
                new = off
            else:
                raise TexError("%s: batch %d overlaps the texture data." % (path, i))
            struct.pack_into("<I", out, bo + BATCH_REC * i + (4 if k == 0 else 12), new)
    out = bytes(out)
    _verify(b, out, p, full, delta, path)
    return out


def _range_ok(size, off, length):
    """render.c wpk_range_valid: half-open ranges, an empty one is valid through EOF"""
    return off <= size and length <= size - off


def _verify(old, new, p, full, delta, path):
    """read the new file back the way the game does and compare it with the old one, byte for byte"""
    q = _parse(new, path)
    if len(new) != len(old) + delta or q["head"] != p["head"]:
        raise TexError("%s: the rewritten file does not match (header or size)." % path)
    for i, (t, u) in enumerate(zip(p["tex"], q["tex"])):
        if u["name"] != t["name"] or u["fmt"] != t["fmt"]:
            raise TexError("%s: texture %d changed its name or format." % (path, i))
        if not t["sz"]:
            if u["sz"] or u["off"] != t["off"]:
                raise TexError("%s: a borrowed texture changed." % path)
            continue
        if not _range_ok(len(new), u["off"], u["sz"]):
            raise TexError("%s: texture %d runs past the end." % (path, i))
        if i in full:
            w, h, raw = full[i]
            if (u["w"], u["h"], u["sz"]) != (w, h, len(raw)) or new[u["off"]:u["off"] + u["sz"]] != raw:
                raise TexError("%s: texture %d is not what was written." % (path, i))
        elif (u["w"], u["h"], u["sz"]) != (t["w"], t["h"], t["sz"]) or \
                new[u["off"]:u["off"] + u["sz"]] != old[t["off"]:t["off"] + t["sz"]]:
            raise TexError("%s: texture %d was altered." % (path, i))
    for i, (r, s) in enumerate(zip(p["bat"], q["bat"])):
        if (r["vn"], r["inn"]) != (s["vn"], s["inn"]):
            raise TexError("%s: batch %d changed its counts." % (path, i))
        for (o1, o2, n, wd) in ((r["vo"], s["vo"], r["vn"], STRIDE), (r["io"], s["io"], r["inn"], 2)):
            if not _range_ok(len(new), o2, n * wd) or new[o2:o2 + n * wd] != old[o1:o1 + n * wd]:
                raise TexError("%s: batch %d lost its mesh data." % (path, i))
    _, nt, nc, nb, _cs, to, co, bo = p["head"]
    if new[co:co + CHUNK_REC * nc] != old[co:co + CHUNK_REC * nc]:
        raise TexError("%s: the chunk table changed." % path)
    # the batch table keeps everything but the two offsets (bytes 4-7 and 12-15)
    for i in range(nb):
        a, c = old[bo + BATCH_REC * i:bo + BATCH_REC * (i + 1)], new[bo + BATCH_REC * i:bo + BATCH_REC * (i + 1)]
        if a[0:4] != c[0:4] or a[8:12] != c[8:12] or a[16:] != c[16:]:
            raise TexError("%s: batch %d changed beyond its offsets." % (path, i))


# ----------------------------------------------------------------------------------------------- files
def write_with_backup(path, data):
    """write data to path through a temporary file; an existing file is first kept once as path.bak -> True if a .bak
    was made"""
    bak = False
    if os.path.exists(path) and not os.path.exists(path + ".bak"):
        shutil.copyfile(path, path + ".bak")
        bak = True
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return bak


def car_name(path):
    """maz_NCEC_aeroA.wpk -> maz_NCEC"""
    return re.sub(r"_aero[ABC]$", "", os.path.splitext(os.path.basename(path))[0], flags=re.I)


def family(path):
    """the car's stock pack and its aero kits (same folder)"""
    d = os.path.dirname(os.path.abspath(path))
    name = car_name(path)
    files = [os.path.join(d, name + ".wpk")] + sorted(glob.glob(os.path.join(d, name + "_aero[ABC].wpk")))
    return [f for f in files if os.path.isfile(f)]


def _same_texture(ta, ba, tb, bb):
    """two pack textures hold the same picture (size, format and every byte)"""
    return (ta["w"], ta["h"], ta["fmt"], ta["sz"]) == (tb["w"], tb["h"], tb["fmt"], tb["sz"]) and \
        ba[ta["off"]:ta["off"] + ta["sz"]] == bb[tb["off"]:tb["off"] + tb["sz"]]


def import_to_car(path, key, im, out_dir, size=None, dither=False):
    """the picture into texture `key` of the pack at `path` and of the car's other packs (stock + aero kits) that carry
    the SAME picture of it. A kit that borrows it from the stock pack needs nothing; a kit with its own, different
    version of it is left alone (its picture is not the one that was edited). Written to out_dir as a complete set (the
    packs that did not change are copied). Writing into the car's own folder keeps each replaced file once as .bak.
    -> dict(files=[(name, what happened)], notes=[...], bak=n)"""
    fam = family(path)
    if not fam:
        raise TexError("%s.wpk was not found next to %s." % (car_name(path), os.path.basename(path)))
    with open(path, "rb") as fh:
        b0 = fh.read()
    t0 = _parse(b0, os.path.basename(path))["tex"]
    idx0 = find_texture(list_textures(b0), key)
    ref = t0[idx0]
    if not ref["sz"]:
        raise TexError("'%s' is borrowed from the stock pack: edit the stock car's .wpk." % ref["name"])
    stem = _stem(ref["name"])
    same_dir = os.path.abspath(out_dir) == os.path.dirname(os.path.abspath(path))
    os.makedirs(out_dir, exist_ok=True)
    result, notes, baks, pending = [], [], 0, []
    for f in fam:
        with open(f, "rb") as fh:
            b = fh.read()
        name = os.path.basename(f)
        is_src = os.path.abspath(f) == os.path.abspath(path)
        hit = [t for t in _parse(b, name)["tex"] if _stem(t["name"]) == stem and not PAINT.match(t["name"])]
        if not hit:
            result.append((name, "has no such texture"))
            pending.append((f, None))
        elif not hit[0]["sz"]:
            result.append((name, "borrows it from the stock pack"))
            pending.append((f, None))
        elif not is_src and not _same_texture(ref, b0, hit[0], b):
            result.append((name, "has its own, different version of it: left unchanged"))
            pending.append((f, None))
        else:
            i = [k for k, t in enumerate(_parse(b, name)["tex"]) if t is not None and t["name"] == hit[0]["name"]][0]
            new, nt = import_image(b, i, im, size, dither, name)
            notes = notes or nt
            result.append((name, "replaced"))
            pending.append((f, new))
    for f, new in pending:                                  # nothing is written before every file is built and checked
        dst = os.path.join(out_dir, os.path.basename(f))
        if new is not None:
            baks += bool(write_with_backup(dst, new))
        elif not same_dir:                                  # a complete set in the new folder
            shutil.copyfile(f, dst)
    return dict(files=result, notes=notes, bak=baks)


if __name__ == "__main__":
    print(__doc__)
