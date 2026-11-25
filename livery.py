"""livery: recolor and decorate a car's body texture, using the car's 3D shape (not the flat texture) to place the patterns.

  apply_livery(pack, spec) -> info     paints the body texture (and its p1..p5 paint copies) in the pack, in place
  spec = dict(color="#d01818",              body colour (keeps the baked shading)
              stripes=dict(color="#ffffff", width=1.6, gap=0.5, pin=0.5),   centre stripes over bonnet / roof / boot (units: 10 = 1 m)
              side=dict(color="#ffffff", y0=1.0, y1=2.2),                   a band along both sides
              roof=dict(color="#101010"),                                    two-tone roof
              gradient=dict(color="#ffcc00", amount=0.6),                    colour fades towards the rear
              number=dict(text="86", color="#ffffff", bg="#101010", z=0.0, y=3.0, size=7.0))   roundel on both doors
  spec["repaint_all"]=True paints every body texel (for dark or already-liveried cars); it switches on by itself when under half the body is light paint
  python livery.py CAR.wpk OUT.wpk --palette #1f4aa8,#c4200f,#f2f2f0,#9aa0a2,#17181a    the five in-game paint choices (p1..p5)
  python livery.py CAR.wpk OUT.wpk --color #d01818 [--stripes #ffffff] [--side #ffffff] [--roof #101010] [--number 86] [--png tex.png]
"""
import re
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import wpk_edit as W


def _hex(c):
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def body_texture(pack):
    """slot of the main body paint texture: the body01 one the body really uses, else the texture the body uses most"""
    use = {}
    for c in pack.chunks:
        if c.tag == 0:
            for b in c.batches:
                if b.tex != W.NO_TEX and not (b.flags & 3):
                    use[b.tex] = use.get(b.tex, 0) + len(b.I) // 3
    if not use:
        raise W.EditError("This car has no textured body.")
    named = [i for i in use if "body01" in pack.tex[i]["name"].lower() and not re.match(r"p\d:", pack.tex[i]["name"])]
    return max(named or use, key=lambda i: use[i])


def texel_map(pack, slot, w, h, vflip=False):
    """per texel of texture SLOT: car-space position, normal, and whether any triangle covers it"""
    pos = np.zeros((h, w, 3), np.float32)
    nrm = np.zeros((h, w, 3), np.float32)
    cov = np.zeros((h, w), bool)
    for c in pack.chunks:
        for b in c.batches:
            if b.tex != slot or not len(b.I):
                continue
            uv = b.V["uv"].astype(np.float64)
            px, py = uv[:, 0] * w, ((1.0 - uv[:, 1]) if vflip else uv[:, 1]) * h
            P = b.V["pos"].astype(np.float32)
            N = b.V["normal"].astype(np.float32) / 32767.0
            for a, bb, cc in b.I.reshape(-1, 3):
                xs, ys = px[[a, bb, cc]], py[[a, bb, cc]]
                x0, x1 = int(max(np.floor(xs.min()) - 1, 0)), int(min(np.ceil(xs.max()) + 1, w))
                y0, y1 = int(max(np.floor(ys.min()) - 1, 0)), int(min(np.ceil(ys.max()) + 1, h))
                if x1 <= x0 or y1 <= y0:
                    continue
                gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
                d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
                if abs(d) < 1e-9:
                    continue
                l0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
                l1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
                l2 = 1 - l0 - l1
                eps = -0.04
                m = (l0 >= eps) & (l1 >= eps) & (l2 >= eps)
                if not m.any():
                    continue
                L = np.stack([l0[m], l1[m], l2[m]], 1).clip(0, 1)
                pos[y0:y1, x0:x1][m] = L @ P[[a, bb, cc]]
                nrm[y0:y1, x0:x1][m] = L @ N[[a, bb, cc]]
                cov[y0:y1, x0:x1] |= m
    return pos, nrm, cov


def _band(v, lo, hi, soft=0.12):
    """1 inside lo..hi with a little soft edge"""
    return np.clip((v - lo) / soft + 0.5, 0, 1) * np.clip((hi - v) / soft + 0.5, 0, 1)


def _blend(img, mask, color, amount=1.0):
    m = (mask * amount)[..., None]
    img[:] = img * (1 - m) + color[None, None, :] * m


def _grow(m, n):
    """the mask m grown by n texels in every direction"""
    out = m.copy()
    for _ in range(n):
        g = out.copy()
        g[1:] |= out[:-1]
        g[:-1] |= out[1:]
        g[:, 1:] |= out[:, :-1]
        g[:, :-1] |= out[:, 1:]
        out = g
    return out


def _paintlike(tex, cov):
    """True when enough of the used texels look like paint (light, not strongly coloured): a black roof or grille texture is not"""
    img = tex[..., :3].astype(np.float32)
    lum = img.mean(2) / 255.0
    sat = (img.max(2) - img.min(2)) / np.maximum(img.max(2), 1)
    n = max(int(cov.sum()), 1)
    return int((cov & (lum > 0.22) & (sat < 0.30)).sum()) >= 0.15 * n


def paint(tex, pos, nrm, cov, spec, bounds=None):
    """-> new RGBA uint8 texture (bounds = (lo, hi) of the whole car body, so every texture agrees on where front / roof / sides are)"""
    if bounds is None:
        bounds = (pos[cov].min(0), pos[cov].max(0)) if cov.any() else (np.zeros(3), np.ones(3))
    blo, bhi = bounds
    img = tex[..., :3].astype(np.float32)
    lum = img.mean(2) / 255.0
    sat = (img.max(2) - img.min(2)) / np.maximum(img.max(2), 1)
    paint_m = cov & (sat < 0.30) & (lum > 0.22)
    mode_all = bool(spec.get("repaint_all")) or paint_m.sum() < 0.08 * max(cov.sum(), 1)       # only a really dark paint (a black car) is repainted whole; a light bumper with a black grille keeps the grille
    fringe = _grow(cov, 3) & ~cov & (lum > 0.12)          # the light edge row just outside the triangles (it would stay white / grey as a thin line)
    if mode_all:                                              # the original is dark: paint every covered texel and fake the shading
        light = np.array([0.3, 0.8, 0.5], np.float32)
        light /= np.linalg.norm(light)
        shade = 0.55 + 0.45 * np.clip((nrm * light).sum(2), 0, 1)
        paint_m = cov | fringe
        shade_src = shade
    else:
        paint_m = paint_m | (fringe & (sat < 0.30))
        ref = np.percentile(lum[paint_m], 90) if paint_m.any() else 1.0
        shade_src = np.clip(lum / max(ref, 0.05), 0, 1.15)
    out = img.copy()
    pm = paint_m.astype(np.float32)
    if spec.get("color"):
        col = _hex(spec["color"])
        tinted = col[None, None, :] * shade_src[..., None]
        out = out * (1 - pm[..., None]) + tinted * pm[..., None]
    shade3 = shade_src[..., None]
    x, y, z = pos[..., 0], pos[..., 1], pos[..., 2]
    up = (nrm[..., 1] > 0.55)
    side = (np.abs(nrm[..., 0]) > 0.5)
    if spec.get("gradient"):
        g = spec["gradient"]
        t = np.clip((z - blo[2]) / max(bhi[2] - blo[2], 1e-6), 0, 1)           # 0 at the rear, 1 at the front
        t = (1 - t) if g.get("to_rear", True) else t
        o = out.copy()
        _blend(o, pm * np.clip((t - (1 - g.get("amount", 0.6))) / max(g.get("amount", 0.6), 0.05), 0, 1), _hex(g["color"]))
        out = o * 1.0
    if spec.get("roof"):
        r = spec["roof"]
        ymax = bhi[1]
        top = np.clip((y - r.get("y", ymax * 0.78)) / 0.8 + 0.5, 0, 1) * (nrm[..., 1] > -0.2)
        o = out.copy()
        _blend(o, pm * top, _hex(r["color"]) * 1.0)
        out = o
    if spec.get("side"):
        s = spec["side"]
        band = _band(y, s.get("y0", 1.0), s.get("y1", 2.2)) * side
        o = out.copy()
        _blend(o, pm * band, _hex(s["color"]))
        out = o * 1.0
        out = np.where((pm * band)[..., None] > 0.5, out * np.clip(shade3 * 1.0, 0, 1.15) / np.maximum(shade3, 1e-3) * shade3, out)
    if spec.get("stripes"):
        s = spec["stripes"]
        w, gap, pin = s.get("width", 1.6), s.get("gap", 0.5), s.get("pin", 0.5)
        ax = np.abs(x)
        m1 = _band(ax, 0, w / 2)
        m2 = _band(ax, w / 2 + gap, w / 2 + gap + pin) if pin > 0 else 0
        m = np.clip(m1 + m2, 0, 1) * up * (y > blo[1] + 0.45 * (bhi[1] - blo[1]))
        o = out.copy()
        _blend(o, pm * m, _hex(s["color"]))
        out = o * 1.0
    if spec.get("number"):
        n = spec["number"]
        size = n.get("size", 7.0)
        zc, yc = n.get("z", 0.0), n.get("y", 3.0)
        cv = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(cv)
        d.ellipse((6, 6, 250, 250), fill=tuple(int(v) for v in _hex(n.get("bg", "#101010"))) + (255,))
        try:
            font = ImageFont.load_default(size=170 if len(n["text"]) <= 2 else 110)
        except TypeError:
            font = ImageFont.load_default()
        d.text((128, 128), n["text"], fill=tuple(int(v) for v in _hex(n.get("color", "#ffffff"))) + (255,), font=font, anchor="mm")
        arr = np.asarray(cv).astype(np.float32)
        for sgn in (1, -1):
            sel = side & (np.sign(nrm[..., 0]) == sgn) & (np.sign(x) == sgn) & cov
            u = ((z - zc) * (-sgn) / size + 0.5) * 255.0                # read left to right as seen from outside
            vv = (0.5 - (y - yc) / size) * 255.0
            inside = sel & (u >= 0) & (u <= 255) & (vv >= 0) & (vv <= 255)
            ui, vi = u[inside].astype(int), vv[inside].astype(int)
            smp = arr[vi, ui]
            a = (smp[:, 3:4] / 255.0)
            idx = np.nonzero(inside)
            out[idx] = out[idx] * (1 - a) + smp[:, :3] * a * np.clip(shade_src[idx][:, None], 0.35, 1.1)
    return np.concatenate([np.clip(out, 0, 255), tex[..., 3:4].astype(np.float32)], 2).astype(np.uint8), bool(mode_all)


def paint_slots(pack, tags=None):
    """every texture that carries body paint: used by the body, mirrors, wings or bonnets (not wheels, glass, plate decals)"""
    out = []
    try:
        main = body_texture(pack)
        glossy = any(b.tex == main and b.prog == 1 for c in pack.chunks for b in c.batches)
    except W.EditError:
        glossy = False
    for c in pack.chunks:
        if (c.tag in tags) if tags is not None else (c.tag == 0 or 0x120 <= c.tag <= 0x129 or 0x180 <= c.tag <= 0x19F):
            for b in c.batches:
                if b.tex == W.NO_TEX or b.tex in out or b.flags & 3:
                    continue
                n = pack.tex[b.tex]["name"].lower()
                if re.match(r"p\d:", n) or any(x in n for x in ("_wh", "punch", "window", "_win", "glass", "alfa", "spec", "rubber", "tire", "tyre", "exhaust", "plate", "light", "lamp")):
                    continue
                if glossy and b.prog == 0 and not any(x in n for x in ("body", "paint")):
                    continue                 # the body is glossy paint: a flat (prog 0) texture like a roof, badges or lamp sheet is not paint
                out.append(b.tex)
    return out


def apply_livery(pack, spec, stock=None, vflip=False):
    main = body_texture(pack)
    slots = paint_slots(pack)
    if main not in slots:
        slots.insert(0, main)
    info = dict(textures=[], dark_original=False)
    allv = [b.V["pos"].astype(np.float32) for c in pack.chunks if c.tag == 0 or 0x120 <= c.tag <= 0x129 for b in c.batches if len(b.V)]
    allv = np.concatenate(allv)
    bounds = (allv.min(0), allv.max(0))
    for slot in slots:
        t = pack.tex[slot]
        base = pack.decode_tex(slot, stock)
        h, w = base.shape[:2]
        pos, nrm, cov = texel_map(pack, slot, w, h, vflip)
        if slot != main and not _paintlike(base, cov):
            continue
        new, dark = paint(base, pos, nrm, cov, spec, bounds)
        im = Image.fromarray(new, "RGBA")
        pack.add_texture(t["name"], im)
        if slot == main:
            info.update(texture=t["name"], size=(w, h), covered=float(cov.mean()), dark_original=dark)
            for k in range(1, 6):                              # the paint copies the game swaps in
                pn = "p%d:%s" % (k, t["name"])
                j = pack.tex_index(pn)
                if j is not None and pack.tex[j]["name"] == pn:
                    pack.add_texture(pn, im)
        info["textures"].append(t["name"])
    return info


DEFAULT_PALETTE = ["#1f4aa8", "#c4200f", "#f2f2f0", "#9aa0a2", "#17181a"]     # blue, red, white, silver, black (what the stock RX-7 offers as paints 1..5)


def apply_palette(pack, colors=None, stock=None):
    """the game's paint choices: the body texture (and every other paint texture) as p1:NAME .. p5:NAME, one copy per colour.
    The car's own texture stays the default colour.  -> dict(textures=[...], colors=[...])"""
    colors = list(colors or DEFAULT_PALETTE)[:5]
    main = body_texture(pack)
    slots = paint_slots(pack, tags=(0, 0x120, 0x121, 0x122, 0x126) + tuple(range(0x180, 0x1A0)))
    for i in paint_slots(pack, tags=tuple(range(0x122, 0x12A))):      # the other wings / bonnets: only a small texture (a wing sheet), a full body sheet would add 2.6 MB per paint
        if i not in slots and pack.tex[i].get("w", 0) * pack.tex[i].get("h", 0) <= 256 * 256:
            slots.append(i)   # what is on the car by default; other wing / bonnet options keep their texture (each extra paint texture costs 2.6 MB)
    if main not in slots:
        slots.insert(0, main)
    allv = np.concatenate([b.V["pos"].astype(np.float32) for c in pack.chunks if c.tag == 0 or 0x120 <= c.tag <= 0x129 or 0x180 <= c.tag <= 0x19F for b in c.batches if len(b.V)])
    bounds = (allv.min(0), allv.max(0))
    done = []
    for slot in slots:
        name = pack.tex[slot]["name"]
        base = pack.decode_tex(slot, stock)
        h, w = base.shape[:2]
        pos, nrm, cov = texel_map(pack, slot, w, h)
        if slot != main and not _paintlike(base, cov):                  # a dark soft top, a black grille texture ...: not body paint
            continue
        for k, col in enumerate(colors, 1):
            new, _dark = paint(base, pos, nrm, cov, {"color": col}, bounds)
            pack.add_texture("p%d:%s" % (k, name), Image.fromarray(new, "RGBA"))
        done.append(name)
    return dict(textures=done, colors=colors)


def main(a):
    if len(a) < 2:
        print(__doc__)
        return 2
    opt = {}
    i = 2
    while i < len(a):
        if a[i].startswith("--") and i + 1 < len(a):
            opt[a[i][2:]] = a[i + 1]
            i += 2
        else:
            i += 1
    spec = {}
    if "color" in opt:
        spec["color"] = opt["color"]
    if "stripes" in opt:
        spec["stripes"] = dict(color=opt["stripes"])
    if "side" in opt:
        spec["side"] = dict(color=opt["side"])
    if "roof" in opt:
        spec["roof"] = dict(color=opt["roof"])
    if "number" in opt:
        spec["number"] = dict(text=opt["number"])
    if "repaint-all" in opt:
        spec["repaint_all"] = True
    p = W.Pack.load(a[0])
    if "palette" in opt:
        cols = [c.strip() for c in opt["palette"].split(",") if c.strip()]
        r = apply_palette(p, cols)
        p.check()
        p.save(a[1], backup=False)
        print("paints 1..%d written for %d textures" % (len(cols[:5]), len(r["textures"])))
        return 0
    r = apply_livery(p, spec)
    p.check()
    p.save(a[1], backup=False)
    if "png" in opt:
        Image.fromarray(p.decode_tex(p.tex_index(r["texture"]))).save(opt["png"])
    print("painted %s (%dx%d); %.0f%% of the texture is body" % (r["texture"], r["size"][0], r["size"][1], 100 * r["covered"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
