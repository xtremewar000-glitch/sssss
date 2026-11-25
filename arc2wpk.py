"""arc2wpk: rebuild a Re3DX+ car pack (and its aero kit packs) from the arcade game's own car data.

  python arc2wpk.py PLC_FOLDER DONOR.wpk OUT.wpk [--colors NF_COLOR_DIR] [--game NF_DIR] [--no-kits]
  python arc2wpk.py --all PLAYERCAR_DIR DONOR.wpk OUT_DIR [--game NF_DIR] [--stock STOCK_WPK_DIR]

PLC_FOLDER is one car of the arcade's playerCar folder (PLC_xxx.mdl / .tex / _STD_WHEEL.mdl ...).  NF_DIR is the game's script data
(color/, position/, tire/ ...).  The donor is any stock .wpk (the car's own when there is one): it supplies the pack layout, the
paint/glass materials, the body texture strip and the shaders.  Everything else comes from the arcade car:
  body + windows + plates + interior + boot + exhaust (the tool's importer), mirrors, bonnets, rear wings, lamps (tail / brake / head),
  wheels at the game's own hub positions with the car's wheel and tyre size, aero kits A / B / C as separate packs,
  and the five in-game paints taken from the game's own colour list for that car.
"""
import os
import re
import sys
import numpy as np
from PIL import Image
import arcade_mdl as A
import plc_import as P
P._BuildOriginal = P._Build
import wpk_edit as W
from wpk_edit import NO_TEX, Batch, EditError

_cp = lambda p: open(p, encoding="cp932", errors="replace").read()


# ------------------------------------------------------------------------------------------------ the game's data
def tire_pos(game, name):
    """[(x, y, z)] front right, front left, rear right, rear left (arcade order)"""
    p = os.path.join(game, "position", "pos", "getTirePos_%s.lua" % name)
    t = _cp(p)
    i = t.find('name\t=\t"%s"' % name)
    i = i if i >= 0 else t.find('"%s"' % name)
    m = re.search(r"TirePos\s*=\s*\{(.*?)\},\s*\n\s*HeadLightPos", t[i:], re.S)
    rows = re.findall(r"\{\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,?\s*\}", m.group(1))
    if len(rows) < 4:
        raise EditError("no tyre positions for %s" % name)
    return [tuple(float(v) for v in r) for r in rows[:4]]


def tire_info(game, name):
    t = _cp(os.path.join(game, "tire", "model_type", "TireModelType_%s.lua" % name))
    d = _cp(os.path.join(game, "tire", "script", "TireDefine.lua"))
    const = {k: float(v) for k, v in re.findall(r"^\s*(\w+)\s*=\s*([\d.]+)", d, re.M)}
    blk = lambda key: re.search(r"inc_normal\s*=\s*\{(.*?)\}" if key == "tire" else r"%s\s*=\s*\{(.*?)\}" % key, t, re.S)
    names = re.findall(r'"(TIRE_\w+)"', blk("tire").group(1))
    ws = [const.get(x, 1.0) for x in re.findall(r"(WHEEL_SCALE_\w+)", blk("wheel_scale").group(1))]
    tw = [const.get(x, 1.0) for x in re.findall(r"(TIRE_WIDTH_SCALE_\w+)", blk("tire_width").group(1))]
    return dict(tires=names, wheel_scale=ws or [1.0] * 4, tire_width=tw or [1.0] * 4)


def default_colors(game, name):
    """the game's DefaultColor list for the car: [#rrggbb] (type 0 = the pack's own colour, 1..5 = the five paints)"""
    p = os.path.join(game, "color", "playerCar", "%s_DefaultColor.lua" % name)
    if not os.path.exists(p):
        return []
    t = _cp(p)
    v = re.findall(r"\{\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,?\s*\},\s*--\s*bodycolor\[ 0 \]", t)
    return ["#%02x%02x%02x" % tuple(int(min(float(x), 1.0) * 255 + 0.5) for x in c[:3]) for c in v]


def _hex(c):
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)


# ------------------------------------------------------------------------------------------------ paints
def apply_paints(pack, colors):
    """body01 = colors[0]; p1..p5 = colors[1..5] (the flat paint area of the texture is recoloured, the lamp / plate strip stays)"""
    slot = next((k for k, t in enumerate(pack.tex) if "body01" in t["name"].lower() and not re.match(r"p\d+:", t["name"])), None)
    if slot is None or not colors:
        return 0
    base = pack.decode_tex(slot)
    flat = base[..., :3].reshape(-1, 3)
    u, cnt = np.unique(flat, axis=0, return_counts=True)
    mode = u[cnt.argmax()]
    near = (np.abs(base[..., :3].astype(int) - mode.astype(int)).max(2) <= 6)
    name = pack.tex[slot]["name"]
    cols = list(colors)
    while len(cols) < 6:
        cols.append(cols[-1])
    for k, c in enumerate(cols[:6]):
        im = base.copy()
        im[near, :3] = _hex(c)
        img = Image.fromarray(im, "RGBA")
        if k == 0:
            pack.add_texture(name, img)
        else:
            pack.add_texture("p%d:%s" % (k, name), img)
    return len(cols[:6])


# ------------------------------------------------------------------------------------------------ parts
def _find_group(arc, prefix, kit):
    """arcade part named prefix + a letter group that contains the kit letter (lamp_stop_nijk, lamp_run_abcdefgh ...)"""
    best = None
    for nm, i in arc.by_name.items():
        s = nm[len(arc.prefix) + 1:] if nm.startswith(arc.prefix + "_") else nm
        if s.startswith(prefix):
            grp = s[len(prefix):].lstrip("_")
            if kit.lower() in grp.split("_")[0] and (best is None or len(grp) < len(best[0])):
                best = (grp, i)
    return best[1] if best else None


def _item(arc, short):
    return arc.find(short)


def trim_lamps(pack, margin=1.5):
    """the lamp parts' glow sprites that the arcade places away from the car (it offsets them with its own position data): drop what is outside the body"""
    body = pack.chunk(0)
    if body is None or not body.batches:
        return
    lo, hi = np.array(body.lo) - margin, np.array(body.hi) + margin
    for tag in (0x112, 0x113, 0x114):
        c = pack.chunk(tag)
        if c is None or not c.batches:
            continue
        keep = []
        for b in c.batches:
            tri = b.I.reshape(-1, 3)
            cen = b.V["pos"][tri].mean(1)
            ok = ((cen >= lo) & (cen <= hi)).all(1)
            if ok.all():
                keep.append(b)
            elif ok.any():
                b.I = tri[ok].reshape(-1).astype(b.I.dtype)
                keep.append(b)
        c.batches = keep
        c.update_bbox()


def import_parts(pack, arc, kit, log=print):
    """mirrors, bonnets, wings and lamps of the arcade car into their part groups; clears the donor's other lamp parts"""
    done = {}
    def put(tag, idxs):
        idxs = [i for i in idxs if i is not None]
        if not idxs:
            return
        try:
            P.import_part(pack, arc, tag, idxs)
            done[tag] = True
        except EditError as ex:
            log("  %s: %s" % (hex(tag), ex))
    put(0x120, [_item(arc, "mirror_N")])
    put(0x121, [_item(arc, "mirror_A")])
    if kit == "N":
        put(0x126, [_item(arc, "bonnet_N")])
    put(0x127, [_item(arc, "bonnet_A")])
    put(0x128, [_item(arc, "bonnet_B")])
    put(0x129, [_item(arc, "bonnet_C")])
    for k, ch in enumerate("abcd"):
        put(0x122 + k, [_item(arc, "rearspoiler_%s" % ch)])
    put(0x112, [_find_group(arc, "lamp_run", kit)])
    put(0x113, [_find_group(arc, "lamp_stop", kit)])
    put(0x114, [_item(arc, "lamp_light_%s" % kit)])
    return done


# ------------------------------------------------------------------------------------------------ headlamps
def front_lens_islands(arc, kit):
    """(lo, hi) boxes of the headlamp lens pieces of the body shell (the front lens / cover polygons with their own picture)"""
    import fbx2wpk as F
    pts, tris, off = [], [], 0
    for it in arc.shell_items(kit):
        for me in arc.part(it)["meshes"]:
            for po in me["polys"]:
                if (po["matw"][0] & 0xFF) in (5, 6):
                    pts.append(po["V"]["p"].astype(np.float32))
                    tris.append(po["tris"].astype(np.int64) + off)
                    off += len(po["V"]["p"])
    if not pts:
        return []
    P_ = np.concatenate(pts)
    T = np.concatenate(tris)
    V = np.zeros(len(P_), W.VERTEX_LIT)
    V["pos"] = P_
    lab = F._islands(W.Mesh("l", "l", None, V, T))
    zmax = float(P_[:, 2].max())
    out = []
    for k in range(int(lab.max()) + 1):
        v = P_[T[lab == k].reshape(-1)]
        lo, hi = v.min(0), v.max(0)
        d, c = hi - lo, (lo + hi) / 2
        if c[2] > 0.5 * zmax and abs(c[0]) > 2.0 and d[0] < 9 and d[1] < 5 and d[0] > 0.6:
            out.append((lo, hi))
    return out


def fix_headlamps(pack, arc, kit, log=print):
    """the arcade's lamp_light shapes are placed by the game's own light positions: keep only those that sit on a real headlamp lens
    and give every lens without one its own glow quad (the game's glow texture), so the glow is always on the lamp"""
    import fbx2wpk as F
    lens = front_lens_islands(arc, kit)
    c = pack.chunk(0x114)
    if not lens:
        return
    kept, covered = [], [False] * len(lens)
    if c is not None:
        for b in c.batches:
            tri = b.I.reshape(-1, 3).astype(np.int64)
            lab = F._islands(W.Mesh("x", "x", None, b.V, tri))
            keep = np.zeros(len(tri), bool)
            for k in range(int(lab.max()) + 1):
                sel = lab == k
                v = b.V["pos"][tri[sel].reshape(-1)]
                cen = (v.min(0) + v.max(0)) / 2
                for li, (llo, lhi) in enumerate(lens):
                    lc, half = (llo + lhi) / 2, (lhi - llo) / 2
                    if abs(cen[0] - lc[0]) <= half[0] * 0.8 + 0.6 and abs(cen[1] - lc[1]) <= half[1] * 0.8 + 0.6 and abs(cen[2] - lc[2]) <= half[2] + 2.0:
                        keep[sel] = True
                        covered[li] = True
            if keep.any():
                b.I = tri[keep].reshape(-1).astype(b.I.dtype)
                kept.append(b)
    need = [l for l, ok in zip(lens, covered) if not ok]
    flares = []
    if need:
        V = np.zeros(4 * len(need), W.VERTEX_LIT)
        T = []
        for i, (lo, hi) in enumerate(need):
            c0 = (lo + hi) / 2
            h = max(hi[0] - lo[0], hi[1] - lo[1], 1.2) * 0.85
            z = hi[2] + 0.2
            for j, (sx, sy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
                V["pos"][4 * i + j] = (c0[0] + sx * h, c0[1] + sy * h * 0.8, z)
                V["uv"][4 * i + j] = ((sx + 1) / 2, (sy + 1) / 2)
                V["normal"][4 * i + j] = (0, 0, 32767)
            T += [(4 * i, 4 * i + 1, 4 * i + 2), (4 * i, 4 * i + 2, 4 * i + 3)]
        V["rgba"] = 150
        flares = [W.Mesh("headflare", "car_brakelamp_glow__add", None, V, np.array(T, np.int64))]
    if flares:
        W.replace_parts(pack, flares, default_tag=0x114, only_tags=None)
    c2 = pack.chunk(0x114)
    if c2 is not None:
        c2.batches = list(c2.batches) + kept
        c2.update_bbox()
    log("  headlamps: %d lens, %d kept glow shapes, %d new glows" % (len(lens), len(kept), len(need)))


# ------------------------------------------------------------------------------------------------ wheels
def _tyre_donor(pack, tag):
    """(batches of the donor's tyre-only part, its centre) for corner tag 0x100..0x103"""
    c = pack.chunk(0x12a + tag - 0x100)
    return c


_TIRE_DIMS = {}


def tire_dims(game, model):
    """(radius, width) of a tyre model of the game's tire_data.mdl (arcade units)"""
    if not _TIRE_DIMS:
        items, ids = A.read_xmd(os.path.join(game, "tire", "tire_data.mdl"))
        for b in items:
            if b[:4] != b"NDWD":
                continue
            d = A.decode_ndwd(b)
            for me in d["meshes"]:
                pts = np.concatenate([po["V"]["p"] for po in me["polys"]])
                _TIRE_DIMS[me["name"].upper().split("___")[0]] = (float(max(np.abs(pts[:, 1]).max(), np.abs(pts[:, 2]).max())), float(pts[:, 0].max() - pts[:, 0].min()))
    return _TIRE_DIMS.get(model.upper())


def build_wheels(pack, arc, game, log=print):
    """the four wheels at the game's hub positions: the car's rim (scaled to its wheel size) + the donor's tyre fitted to the tyre size"""
    name = arc.name
    pos = tire_pos(game, name)
    info = tire_info(game, name)
    wf = os.path.join(arc.folder, "%s_STD_WHEEL.mdl" % name)
    wt = os.path.join(arc.folder, "%s_STD_WHEEL.tex" % name)
    items, _ids = A.read_xmd(wf)
    tex = A.read_tex(wt)
    img_by_hash = {t["hash"]: A.decode_dxt(t["w"], t["h"], t["fmt"], t["data"]) for t in tex}
    rim_img = img_by_hash.get(1) or img_by_hash.get(None)
    slot = pack.add_texture("%s_wheel.dds" % name[:36], rim_img.transpose(Image.FLIP_TOP_BOTTOM).resize((256, 256), Image.LANCZOS)) if False else None
    # arcade corner order: FR, FL, RR, RL -> wpk tags 0x101, 0x100, 0x103, 0x102
    order = [(0x101, 0, 0), (0x100, 1, 0), (0x103, 2, 1), (0x102, 3, 1)]
    rim_idx = {"R": 0, "L": 3}
    rim_dec = {}
    for side, it in rim_idx.items():
        b = items[it]
        rim_dec[side] = A.decode_ndwd(b)
    # donor wheel material (the rim batch of the donor's wheel part) and its tyre batch
    dw = pack.chunk(0x100)
    rim_mat = None
    tyre_mat = None
    if dw is not None:
        for b in dw.batches:
            if "tire" in pack.tex[b.tex]["name"].lower() if b.tex != NO_TEX else False:
                tyre_mat = b.material()
            else:
                rim_mat = b.material()
    rname = "%s_WH.dds" % name[:36]
    rim_slot = pack.add_texture(rname, rim_img.transpose(Image.FLIP_TOP_BOTTOM).resize((256, 256), Image.LANCZOS))   # the packs keep arcade pictures upside down, as the UVs are
    rm = rim_mat or (rim_slot, 0, 0, NO_TEX, NO_TEX, 0)
    rm = (rim_slot,) + tuple(rm[1:])
    out = {}
    for tag, pi, ri in order:
        side = "R" if pos[pi][0] < 0 else "L"
        sc = info["wheel_scale"][ri] if ri < len(info["wheel_scale"]) else 1.0
        tw = info["tire_width"][ri] if ri < len(info["tire_width"]) else 1.0
        # rim
        V_all, T_all = [], []
        base = 0
        for me in rim_dec[side]["meshes"]:
            for po in me["polys"]:
                V, T = P.poly_batch_pieces(po)
                V = V.copy()
                V["pos"] = V["pos"] * np.float32(sc) + np.array(pos[pi], np.float32)
                V_all.append(V); T_all.append(T + base); base += len(V)
        rim_b = Batch(rm[0], rm[1], rm[2], 0, rm[3], rm[4], rm[5], np.concatenate(V_all), np.concatenate(T_all).astype("<u2").reshape(-1))
        batches = [rim_b]
        # tyre: the donor's tyre of that corner, moved to the hub and fitted to the width / radius of the tyre model
        old = pack.chunk(0x12a + tag - 0x100)
        if old is not None and old.batches:
            tb = old.batches[0].copy()
            c = np.array([(old.lo[k] + old.hi[k]) / 2 for k in range(3)], np.float32)
            rad_d = max(old.hi[1] - old.lo[1], old.hi[2] - old.lo[2]) / 2
            wid_d = old.hi[0] - old.lo[0]
            tm = info["tires"][pi] if pi < len(info["tires"]) else None
            dims = tire_dims(game, tm) if tm else None
            sr = (dims[0] / rad_d) if dims else 1.0
            sw = (dims[1] * tw / wid_d) if dims else tw
            V = tb.V
            V["pos"] = (V["pos"] - c) * np.array([sw, sr, sr], np.float32) + np.array([pos[pi][0], pos[pi][1], pos[pi][2]], np.float32)
            batches = [tb] + batches
            out[0x12a + tag - 0x100] = [tb.copy()]
        out[tag] = batches
    for tag, bs in out.items():
        pack.set_chunk(tag, bs)
    return out



# ------------------------------------------------------------------------------------------------ detail from the arcade's kit atlas
def atlas(arc, kit):
    """(diffuse RGBA, mask RGBA or None) of the kit's atlas in AERO_SET.tex, else None"""
    path = arc.tex_paths.get("aero")
    if not path:
        return None
    ts = A.read_tex(path)
    items = sorted({t["item"] for t in ts})
    if not items:
        return None
    kits = arc.kits()
    idx = kits.index(kit) if kit in kits else 0
    it = items[min(idx, len(items) - 1)]
    img = lambda t: A.decode_dxt(t["w"], t["h"], t["fmt"], t["data"])
    main = [t for t in ts if t["item"] == it and t["hash"] is None]
    mask = [t for t in ts if t["item"] == it and t["hash"] == 1]
    if not main:
        return None
    return img(main[0]), (img(mask[0]) if mask else None)


def detail_image(at, color):
    """the atlas with the paint area (the mask) in `color`, flipped like the pack's pictures; 512 px"""
    dif, mask = at
    a = np.asarray(dif, np.float32).copy()
    if mask is not None:
        m = np.asarray(mask.convert("RGB"), np.float32)[..., 0] > 128
        c = _hex(color).astype(np.float32) / 255.0
        a[m, :3] = np.clip(a[m, :3] * c, 0, 255)
    im = Image.fromarray(a.astype(np.uint8), "RGBA").transpose(Image.FLIP_TOP_BOTTOM)
    return im.resize((512, 512), Image.LANCZOS)


class ArcBuild(P._Build):
    """the importer's builder, for a donor that is not the same car: detail polygons (grille, badges, lamp bodies) use the kit atlas,
    lens polygons their own picture"""

    def __init__(self, pack, arc):
        super().__init__(pack, arc)
        if getattr(arc, "_detail_slot", None) is not None:
            self.stock = {}                                      # the donor is another car: its surfaces are not ours to copy
            pts = np.concatenate([po["V"]["p"] for i in arc.shell_items("N") for me in arc.part(i)["meshes"] for po in me["polys"]])
            self.zmin, self.zmax = float(pts[:, 2].min()), float(pts[:, 2].max())
            self.xmax = float(np.abs(pts[:, 0]).max())

    def add_plain(self, po, kind):
        """the importer leaves the arcade's own uv on a surface it cannot match to a donor; a paint / plastic surface must sample the flat area"""
        if self.stock or getattr(self.arc, "_detail_slot", None) is None:
            return super().add_plain(po, kind)
        V, T = P.poly_batch_pieces(po)
        if not len(T):
            return
        p = self.paint
        if kind == "lens":
            mat, uv = (self.gtex,) + tuple(self.gl), None
        elif kind == "cover":
            mat, uv = (self.gtex,) + tuple(self.gl), (self.clear_uv if self.clear_uv is not None else self.glass_uv)
        elif kind in ("paint", "mixed"):
            mat, uv = (self.body_tex, p[0], p[1], p[2], p[3], p[4]), self.paint_uv
        else:
            mat, uv = (self.body_tex, 0, 0, NO_TEX, NO_TEX, 0), self.flat_uv
        if uv is not None:
            V = V.copy()
            V["uv"] = uv
        self.groups.setdefault(mat, []).append((V, T))

    def add_item(self, idx, role="shell", only=None):
        arc = self.arc
        det = getattr(arc, "_detail_slot", None)
        if det is None:
            return super().add_item(idx, role, only)
        d = arc.part(idx)
        pk = self.pack
        for me in d["meshes"]:
            if only and only not in me["name"].lower():
                continue
            for po in me["polys"]:
                typ = po["matw"][0] & 0xFF
                hs = po["hashes"]
                if typ == 7 and not hs and role != "plate":              # lamp glow whose picture lives in the kit atlas (the lamp strip)
                    self.add(po, (det, 2, 0, NO_TEX, NO_TEX, 0))
                    continue
                if role == "plate" or typ in (0, 7) or typ >= 10:
                    sub = ArcBuildOne(self, idx, me, po, role)
                    continue
                if typ in (5, 6) and hs:
                    t = P.tex_slot(pk, arc, hs[0], self.cache)
                    if t != NO_TEX:
                        self.add(po, (t, 0, 0, NO_TEX, NO_TEX, 0))
                        continue
                if typ in (8, 9) and not hs and po.get("uvset", True):
                    # the atlas picture only on the nose and tail (grille, lamps, plate, badges); the flanks keep the flat paint
                    c = po["V"]["p"][po["tris"]].mean(1)
                    zh = max(abs(self.zmax), abs(self.zmin), 1.0)
                    end = (np.abs(c[:, 2]) >= 0.72 * zh) & (np.abs(c[:, 0]) <= 0.88 * self.xmax)
                    if end.any():
                        self.add(dict(po, tris=po["tris"][end]), (det, 0, 0, NO_TEX, NO_TEX, 0))
                    if (~end).any():
                        po2 = dict(po, tris=po["tris"][~end], matw=[(po["matw"][0] & ~0xFF) | 0] + list(po["matw"][1:]))
                        ArcBuildOne(self, idx, dict(me), po2, role)
                    continue
                sub = ArcBuildOne(self, idx, me, po, role)

    # the cases that stay the importer's own
def ArcBuildOne(B, idx, me, po, role):
    one = {"meshes": [dict(me, polys=[po])]}
    orig = B.arc.part
    B.arc.part = lambda i, _o=orig, _d=one: _d if i == idx else _o(i)
    try:
        P._BuildOriginal.add_item(B, idx, role)
    finally:
        B.arc.part = orig

# ------------------------------------------------------------------------------------------------ one car
def convert_car(folder, donor, out, game, kit="N", log=print):
    arc = P.Arcade(folder)
    arc.folder = folder
    if sum(arc.tris(i) for i in arc.shell_items("N")) < 3000:       # older cars: the big paint shell is the "...epower" part, not aero_N
        _si = arc.shell_items
        extra = [i for n, i in arc.by_name.items() if re.search(r"(epower|body_n(_l0)?)$", n) and arc.tris(i) > 1000]
        arc.shell_items = lambda k, _si=_si, _e=extra: (_si(k) + [i for i in _e if i not in _si(k)]) if k == "N" else _si(k)
    _gm = arc.glass_meshes
    arc.glass_meshes = lambda: [(gi, me) for gi, me in _gm() if "sticker" not in me["name"].lower()]   # team / roof stickers are not windows
    pack = W.Pack.load(donor)
    colors0 = default_colors(game, arc.name)
    own = os.path.basename(donor).upper().endswith(("_" + arc.name.split("_", 1)[-1] + ".WPK").upper())
    at = atlas(arc, kit)
    P._Build = ArcBuild if at else P.__dict__["_BuildOriginal"]
    if at:
        arc._detail_slot = pack.add_texture(("%s_detail.dds" % arc.name)[:40], detail_image(at, (colors0 or ["#ffffff"])[0]))
        arc._detail_at = at
    log(P.import_kit(pack, arc, kit, log=log))
    import_parts(pack, arc, kit, log)
    trim_lamps(pack)
    try:
        fix_headlamps(pack, arc, kit, log)
    except Exception as ex:                                     # noqa: BLE001
        log("  headlamps: %s" % ex)
    try:
        build_wheels(pack, arc, game, log)
    except Exception as ex:                                     # noqa: BLE001
        log("  wheels: %s" % ex)
    if not own:                                                  # another car's lamp housings do not belong on this one
        for tag in (0x110, 0x111):
            c = pack.chunk(tag)
            if c is not None:
                c.batches = []
                c.update_bbox()
    cols = colors0
    if cols:
        apply_paints(pack, cols)
        if at:                                                   # the detail texture follows the paints too
            nm = pack.tex[arc._detail_slot]["name"]
            cc = list(cols)
            while len(cc) < 6:
                cc.append(cc[-1])
            for k, c in enumerate(cc[:6]):
                im = detail_image(at, c)
                pack.add_texture(nm if k == 0 else "p%d:%s" % (k, nm), im)
    W.rebuild_shadow(pack)
    pack.check()
    st = pack.save(out, backup=False)
    return dict(bytes=st["bytes"], colors=cols, kit=kit)


def convert_with_kits(folder, donor, out, game, kits=True, log=print):
    """OUT.wpk + OUT_aeroA/B/C.wpk (the kits the arcade car has) -> {kit: result}"""
    arc = P.Arcade(folder)
    res = {"N": convert_car(folder, donor, out, game, "N", log)}
    if kits:
        for k in ("A", "B", "C"):
            if k in arc.kits():
                res[k] = convert_car(folder, donor, os.path.splitext(out)[0] + "_aero%s.wpk" % k, game, k, log)
    return res


def convert_all(root, donor, out_dir, game, stock_dir=None, kits=True, log=print, progress=None, cancelled=lambda: False, skip_existing=True):
    """every PLC_xxx folder under ROOT -> OUT_DIR/PLC_xxx.wpk (+ kits).  The donor for a car is its own stock pack in STOCK_DIR when
    there is one, else DONOR.  -> (converted, [(car, error)], skipped)"""
    os.makedirs(out_dir, exist_ok=True)
    own = stock_donors(stock_dir) if stock_dir else {}
    cars = sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, d + ".mdl")))
    ok, bad, skipped = 0, [], 0
    for n, car in enumerate(cars):
        if cancelled():
            break
        dest = os.path.join(out_dir, car + ".wpk")
        try:
            if skip_existing and os.path.exists(dest):
                skipped += 1
            else:
                convert_with_kits(os.path.join(root, car), own.get(car, donor), dest, game, kits, log)
                ok += 1
                log("%s done" % car)
        except Exception as ex:                                  # noqa: BLE001
            bad.append((car, str(ex)))
            log("FAILED %s: %s" % (car, ex))
        if progress:
            progress(n + 1, len(cars), car)
    return ok, bad, skipped


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--all":
        game = a[a.index("--game") + 1] if "--game" in a else "."
        stock = a[a.index("--stock") + 1] if "--stock" in a else None
        ok, bad, sk = convert_all(a[1], a[2], a[3], game, stock, "--no-kits" not in a)
        print("%d cars converted, %d already there, %d failed" % (ok, sk, len(bad)))
        sys.exit(0 if not bad else 1)
    if len(a) < 3:
        print(__doc__)
        sys.exit(2)
    game = a[a.index("--game") + 1] if "--game" in a else "."
    kit = a[a.index("--kit") + 1].upper() if "--kit" in a else "N"
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--"))]
    print(convert_car(pos[0], pos[1], pos[2], game, kit))


def stock_donors(stock_dir):
    """{arcade car name: stock .wpk path}: maz_FD3S.wpk -> PLC_FD3S (the stock pack of the same car, case-insensitive)"""
    out = {}
    for f in os.listdir(stock_dir):
        if f.lower().endswith(".wpk") and "_aero" not in f.lower():
            tok = os.path.splitext(f)[0].split("_", 1)[-1].upper()
            out["PLC_" + tok] = os.path.join(stock_dir, f)
    return out
