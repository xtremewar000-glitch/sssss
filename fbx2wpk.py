"""fbx2wpk: turn a Unity-style arcade car FBX (DS_cars/CarXXXXXX/FBX_GameObjects/CarXXXXXX/) into a Re3DX+ car pack (.wpk).

  python fbx2wpk.py CAR_FOLDER DONOR.wpk OUT.wpk [--name hon_S2000] [--flip-faces] [--preview OUT.png]
  python fbx2wpk.py --all DS_CARS_DIR DONOR.wpk OUT_DIR          every car in the folder
  (aero kits A/B/C are written as OUT_aeroA.wpk ... when the FBX has them; --no-kits skips them;
   the five in-game paint colours are made unless --no-palette)

The donor is any stock car .wpk (it supplies the pack layout, env/spec maps and shader presets). Its geometry is cleared and
replaced by the FBX: body (+ bonnet, glass, plate, lamps) -> Body, Wheel_FL/FR/RL/RR -> wheels, Parts0Wing_00..03 -> Wing A-D.
Car space: 10 units = 1 m, hub height y = 0, +Z front.
"""
import os, re, sys, glob
import numpy as np
from PIL import Image
import wpk_edit as W


def find_fbx(folder):
    hits = glob.glob(os.path.join(folder, "**", "*.fbx"), recursive=True)
    if not hits:
        raise W.EditError("no .fbx in %s" % folder)
    return sorted(hits, key=len)[0]


def derive_name(folder):
    """HONDA_S2000_AP1 from the body texture HONDA_S2000_AP1_body_n.png (else the folder name)"""
    fbx = find_fbx(folder)
    for p in sorted(glob.glob(os.path.join(os.path.dirname(fbx), "*_body_n.png"))):
        b = os.path.basename(p)[:-len("_body_n.png")]
        if not re.match(r"(?i)(racing|stripe|wing|set\d)", b):
            return re.sub(r"[^A-Za-z0-9_]", "_", b)
    return os.path.splitext(os.path.basename(fbx))[0]


_SHADOW = re.compile(r"(^|[_/ ])shadow")
_ALPHA = re.compile(r"(^|_)alpha($|[_\d\s])")
_EXCL = ("wing", "/texblends/", "/texblendobj", "shadow", "collider", "extraeffect", "/driver/")
_WHEEL = re.compile(r"/Wheel_(FL|FR|RL|RR)(/|$)")


def normal_variants(paths, rank=0, use_index=True):
    """pick one variant of every group of alternatives (Parts0Aero_00/_01/_02, FrontBumper_00/_01 ...): the rank-th lowest-numbered one
    (rank 0 = stock; a group with fewer variants keeps its stock one) and always the N folder of N/A/B/C"""
    vals = {}
    def keyval(parts, i):
        nm = parts[i]
        if re.fullmatch(r"[A-Z]", nm):
            return ("/".join(parts[:i]), "<letter>"), (0 if nm == "N" else 1, nm)
        nums = re.findall(r"\d+", nm)
        if nums and use_index:
            return ("/".join(parts[:i]), re.sub(r"\d+", "#", nm)), tuple(int(x) for x in nums)
        return None
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts) - 1):
            kv = keyval(parts, i)
            if kv:
                vals.setdefault(kv[0], set()).add(kv[1])
    chosen = {}
    for k, v in vals.items():
        s = sorted(v)
        chosen[k] = s[rank] if (k[1] != "<letter>" and rank < len(s)) else s[0]
    keep = []
    for p in paths:
        parts = p.split("/")
        if all((kv is None or chosen[kv[0]] == kv[1]) for kv in (keyval(parts, i) for i in range(1, len(parts) - 1))):
            keep.append(p)
    return keep


def split_parts(paths, kit=0):
    """-> (body paths, {wheel tag: paths}, {wing tag: paths}, {bonnet tag: paths}) from the hierarchy of mesh paths.
    kit 0 = the stock car, 1..3 = aero kit A..C (the 2nd..4th variant of every group that has one)"""
    chassis = [p for p in paths if "/Chassis/" in p]
    rel = lambda p: p.split("/Chassis/", 1)[1].lower()
    wings = {}
    for p in chassis:
        low = rel(p)
        if "wing" in low and not any(x in low for x in ("/texblends/", "/texblendobj", "shadow", "collider", "extraeffect", "/driver/")):
            m = re.search(r"wing_?0*(\d+)", low)
            if m and int(m.group(1)) < 4:
                wings.setdefault(0x122 + int(m.group(1)), []).append(p)
    # Wheels are emitted into their own WPK chunks below; do not also absorb
    # their meshes into the body chunk.
    cands = [p for p in chassis if not _WHEEL.search(p) and not any(x in "/" + rel(p) for x in _EXCL)]
    isfold = lambda p: any(re.match(r"(?i)bonnet_?\d+$", c) for c in p.split("/"))
    bfold = [p for p in cands if isfold(p)]
    sel = normal_variants([p for p in cands if not isfold(p)], kit)
    leaf = [p for p in sel if p.split("/")[-1].lower().startswith("bonnet")]
    body = [p for p in sel if p not in set(leaf)]
    bonnets = {}
    if kit > 0:
        own = [p for p in leaf if "partsbody" not in p.lower()]
        leaf = own or leaf
    if bfold:                                               # Bonnets/Bonnet_00, _01 ... = Bonnet A, B, C, D
        nums = sorted({int(re.search(r"(?i)bonnet_?(\d+)$", [c for c in p.split("/") if re.match(r"(?i)bonnet_?\d+$", c)][0]).group(1)) for p in bfold})
        for p in normal_variants(bfold, 0, use_index=False):
            n = int(re.search(r"(?i)bonnet_?(\d+)$", [c for c in p.split("/") if re.match(r"(?i)bonnet_?\d+$", c)][0]).group(1))
            r = nums.index(n)
            if r < 4:
                bonnets.setdefault(0x126 + r, []).append(p)
    elif leaf:
        bonnets[0x126] = leaf
    wheels = {}
    for p in paths:
        m = _WHEEL.search(p)
        if m:
            wheels.setdefault({"FL": 0x100, "FR": 0x101, "RL": 0x102, "RR": 0x103}[m.group(1)], []).append(p)
    for tag, ps in list(wheels.items()):                    # several wheel designs in one FBX: keep the first so they do not overlap
        def design(p):
            comps = p.split("/")
            i = next(k for k, c in enumerate(comps) if re.fullmatch(r"Wheel_(FL|FR|RL|RR)", c))
            return comps[i + 1] if i + 1 < len(comps) - 1 else ""
        first = sorted({design(p) for p in ps})[0]
        wheels[tag] = [p for p in ps if design(p) == first]
    return body, wheels, wings, bonnets


def _islands(m):
    """connected pieces of a mesh (vertices welded by position) -> array of island numbers per triangle"""
    pos = np.round(m.V["pos"].astype(np.float64) / 0.05).astype(np.int64)
    _, vid = np.unique(pos, axis=0, return_inverse=True)
    vid = vid.reshape(-1)
    par = list(range(int(vid.max()) + 1))
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for a, b, c in m.tris:
        ra = find(vid[a])
        for q in (vid[b], vid[c]):
            rq = find(q)
            if rq != ra:
                par[rq] = ra
    root = np.array([find(vid[t[0]]) for t in m.tris])
    return np.unique(root, return_inverse=True)[1].reshape(-1)


def split_mirrors(meshes):
    """side mirrors out of the body: a mirror-image pair of small pieces at the sides, above the sills, near the windscreen,
    sticking out to the body's full width.  -> (body meshes without them, mirror meshes)  (both unchanged when no pair is found)"""
    comps = []                                   # (mesh index, island number, tris, lo, hi)
    isl = []
    for mi, m in enumerate(meshes):
        lab = _islands(m)
        isl.append(lab)
        for k in range(int(lab.max()) + 1):
            sel = np.nonzero(lab == k)[0]
            v = m.V["pos"][m.tris[sel].reshape(-1)].astype(np.float64)
            comps.append((mi, k, len(sel), v.min(0), v.max(0)))
    if not comps:
        return meshes, []
    allv = np.concatenate([m.V["pos"][m.tris.reshape(-1)] for m in meshes]).astype(np.float64)
    lo, hi = allv.min(0), allv.max(0)
    big = max(comps, key=lambda c: c[2])
    w0 = max(abs(big[3][0]), abs(big[4][0]))
    w0 = max(w0, 0.9 * max(abs(lo[0]), abs(hi[0])))
    cand = []
    for c in comps:
        n, a, b = c[2], c[3], c[4]
        d, cen = b - a, (a + b) / 2
        if not (30 <= n <= 2500) or d[0] > 4.0 or d[1] > 4.0 or d[2] > 5.0:
            continue
        if max(abs(a[0]), abs(b[0])) < 0.9 * w0 or abs(cen[0]) < 0.75 * w0:
            continue
        if cen[1] < lo[1] + 0.45 * (hi[1] - lo[1]) or abs(cen[2] - (lo[2] + hi[2]) / 2) > 0.22 * (hi[2] - lo[2]):
            continue
        cand.append(c)
    best = None
    for i, p in enumerate(cand):
        for q in cand[i + 1:]:
            cp, cq = (p[3] + p[4]) / 2, (q[3] + q[4]) / 2
            if cp[0] * cq[0] >= 0 or abs(cp[0] + cq[0]) > 0.6 or abs(cp[1] - cq[1]) > 0.8 or abs(cp[2] - cq[2]) > 0.8:
                continue
            if abs(p[2] - q[2]) > 0.15 * max(p[2], q[2]):
                continue
            score = (max(abs(p[4][0]), abs(p[3][0])), p[2])
            if best is None or score > best[0]:
                best = (score, p, q)
    if best is None:
        return meshes, []
    chosen = [best[1], best[2]]
    for c in comps:                               # little pieces inside a mirror's box (the glass) go with it
        if c in chosen:
            continue
        for mc in best[1:]:
            if np.all(c[3] >= mc[3] - 0.25) and np.all(c[4] <= mc[4] + 0.25) and c[2] < mc[2]:
                chosen.append(c)
                break
    take = {(c[0], c[1]) for c in chosen}
    rest, mirrors = [], []
    for mi, m in enumerate(meshes):
        lab = isl[mi]
        sel = np.array([(mi, int(k)) in take for k in lab])
        if sel.any():
            mirrors.append(W.Mesh(m.name + "_mirror", m.material, None, m.V, m.tris[sel]))
        if (~sel).any():
            rest.append(W.Mesh(m.name, m.material, m.tag, m.V, m.tris[~sel]))
    return rest, mirrors


def split_tyre(meshes):
    """the tyre of a one-piece wheel: triangles in the outer ring (wheel axis = X) -> tyre meshes, [] if it does not look like a tyre"""
    allv = np.concatenate([m.V["pos"][m.tris.reshape(-1)] for m in meshes]).astype(np.float64)
    lo, hi = allv.min(0), allv.max(0)
    cy, cz = (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2
    rmax = max(hi[1] - lo[1], hi[2] - lo[2]) / 2
    out, n_tyre, n_all = [], 0, 0
    for m in meshes:
        v = m.V["pos"].astype(np.float64)[m.tris]                      # (n,3,3)
        c = v.mean(1)
        r = np.hypot(c[:, 1] - cy, c[:, 2] - cz)
        mask = r >= 0.74 * rmax
        n_all += len(mask); n_tyre += int(mask.sum())
        if mask.any():
            out.append(W.Mesh(m.name + "_tyre", m.material, None, m.V, m.tris[mask]))
    if not out or not (0.05 <= n_tyre / max(n_all, 1) <= 0.75):
        return []
    return out


def split_lamps(meshes):
    """the additive glow meshes (lightADD) out of the body into the lamp parts, like stock cars:
    rear lamp glows -> 0x112 (tail) and 0x113 (brake, a touch bigger and brighter, with flare quads), everything else -> 0x114.
    -> (body meshes, {tag: [Mesh]})"""
    add = [m for m in meshes if m.material.endswith("__add")]
    if not add:
        return meshes, {}
    allv = np.concatenate([m.V["pos"][m.tris.reshape(-1)] for m in meshes]).astype(np.float64)
    zlo, zhi = allv[:, 2].min(), allv[:, 2].max()
    rear_z = zlo + 0.25 * (zhi - zlo)                         # the rear quarter of the car
    rest = [m for m in meshes if not m.material.endswith("__add")]
    tail, other, flares = [], [], []
    for m in add:
        lab = _islands(m)
        for k in range(int(lab.max()) + 1):
            sel = lab == k
            v = m.V["pos"][m.tris[sel].reshape(-1)].astype(np.float64)
            lo, hi = v.min(0), v.max(0)
            cen, d = (lo + hi) / 2, hi - lo
            is_tail = cen[2] < rear_z and abs(cen[0]) > 2.5
            (tail if is_tail else other).append(W.Mesh(m.name, m.material, None, m.V, m.tris[sel]))
            if is_tail:
                flares.append((lo, hi))
    out = {}
    if other:
        out[0x114] = other
    if tail:
        def tint(ms, rgba, dz):
            res = []
            for m in ms:
                V = m.V.copy()
                V["rgba"] = rgba
                V["pos"][:, 2] += dz
                res.append(W.Mesh(m.name, m.material, None, V, m.tris))
            return res
        out[0x112] = tint(tail, 178, 0.0)
        brake = tint(tail, 255, -0.15)
        sides = []                                              # one flare per side: the box around that side's rear lamp pieces
        for sgn in (1, -1):
            bs = [f for f in flares if (f[0][0] + f[1][0]) * sgn > 0]
            if bs:
                lo = np.min([b[0] for b in bs], 0)
                hi = np.max([b[1] for b in bs], 0)
                sides.append(((lo + hi) / 2, max(hi[0] - lo[0], hi[1] - lo[1])))
        if sides:
            vd = tail[0].V.dtype
            V = np.zeros(4 * len(sides), vd)
            T = []
            for i, (cen, size) in enumerate(sides):
                h = max(size, 1.0) * 0.9
                z = cen[2] - 0.35
                for j, (sx, sy) in enumerate(((-1, -1), (1, -1), (1, 1), (-1, 1))):
                    V["pos"][4 * i + j] = (cen[0] + sx * h, cen[1] + sy * h, z)
                    V["uv"][4 * i + j] = ((sx + 1) / 2, (sy + 1) / 2)
                    V["normal"][4 * i + j] = (0, 0, -32767)
                T += [(4 * i, 4 * i + 1, 4 * i + 2), (4 * i, 4 * i + 2, 4 * i + 3)]
            V["rgba"] = 128
            brake.append(W.Mesh("flare", "car_brakelamp_glow__add", None, V, np.array(T, np.int64)))
        out[0x113] = brake
    return rest, out


def kits_available(paths):
    """which aero kits (1..3 = A..C) differ from the stock car"""
    b0, _, _, bn0 = split_parts(paths, 0)
    out = []
    for k in (1, 2, 3):
        b, _, _, bn = split_parts(paths, k)
        if set(b) != set(b0) or {t: set(v) for t, v in bn.items()} != {t: set(v) for t, v in bn0.items()}:
            out.append(k)
    return out


def mat_kind(mat):
    m = mat.lower()
    if "glass" in m or re.search(r"(^|_)(win|window|windows)($|_)", m):
        return "glass"
    if m.endswith("_add") or "lightadd" in m or "_add" in m:
        return "add"
    if re.search(r"_(body|bonnet)", m):
        return "paint"
    return "flat"


def fill_pack(pack, fbx, stem, flip_faces=False, log=print, kit=0, only_tags=None, clear=True, prune=True, parts=True, palette=True):
    """put the FBX's body / wheels / wings / bonnets into PACK (all of them with a fresh donor, or only_tags in a finished car).
    -> dict(tris, hub, textures, missing, nowheels, errors)"""
    pngs = {os.path.splitext(os.path.basename(p))[0].lower(): p for p in glob.glob(os.path.join(os.path.dirname(fbx), "*.png"))}
    # hub height from the wheels
    import fbx_io
    paths = []
    try:
        fbx_io.read_fbx(fbx, select=lambda p: (paths.append(p), False)[1])
    except fbx_io.FbxError:
        pass
    body_paths, wheel_paths, wing_paths, bonnet_paths = split_parts(paths, kit)
    body_aero = None
    if not body_paths:
        raise W.EditError("no body meshes found in %s" % os.path.basename(fbx))
    allw = {p for v in wheel_paths.values() for p in v}
    bset = set(body_paths)
    if allw:
        wy = np.array(fbx_io.read_fbx(fbx, select=lambda p: p in allw)["P"])[:, 1]
        hub = float((wy.min() + wy.max()) / 2.0)
    else:                                                    # no wheel meshes (pacman, ...): hub 0.3 m above the body's lowest point
        hub = float(np.array(fbx_io.read_fbx(fbx, select=lambda p: p in bset)["P"])[:, 1].min()) + 0.3
    off = (0.0, -hub * 10.0, 0.0)
    tex_slots, tex_img = {}, {}
    missing = set()

    def find_png(material):
        base = re.sub(r"(__\d+)?_(ref|matte)(_.*)?$", "", material).lower()
        cands = [base]
        b2 = re.sub(r"_(add|alpha)(_.*)?$", "", base)
        cands += [b2, re.sub(r"_n$", "", b2)]
        for cnd in cands:
            if cnd in pngs:
                return cnd
        best, bl = None, 0                                  # longest shared prefix with a texture name (bonnet_n_ref -> ..._bonnet)
        for k in pngs:
            if k.startswith(("racing_", "car_shadow")):
                continue
            for cnd in cands:
                if cnd.startswith(k) or k.startswith(cnd):
                    L = min(len(k), len(cnd))
                    if L > bl and L >= 0.6 * len(cnd):
                        best, bl = k, L
        if best is None:                                    # the FBX still carries an older car name: match on the part (body / bonnet)
            for part in ("bonnet", "body"):
                if re.search(r"(^|_)%s" % part, base.split("_", 1)[-1] if False else base) or ("_" + part) in base:
                    hits = [k for k in pngs if re.search(r"_%s_n$" % part, k) and not k.startswith(("racing_", "car_shadow"))]
                    if len(hits) == 1:
                        return hits[0]
                    break
        return best

    def texture_for(material):
        key = find_png(material)
        if key is None:
            return None
        if key in tex_slots:
            return tex_slots[key]
        base = os.path.splitext(os.path.basename(pngs[key]))[0]
        nm = (stem + "_" + re.sub(r".*?_(?=(body|bonnet|WH|punch))", "", base, flags=re.I)).replace("__", "_")[:40] + ".dds"
        im = Image.open(pngs[key]).convert("RGBA")
        slot = pack.add_texture(nm, im)
        tex_img[slot] = im
        tex_slots[key] = pack.tex[slot]["name"]
        return tex_slots[key]

    glass_slots = {}

    def glass_texture(which):
        if which not in glass_slots:
            col = (225, 225, 232, 110) if which == "lamp" else (26, 24, 28, 205)       # pale see-through for lamp covers, dark tint for windows
            nm = (stem + ("_glass_lamp" if which == "lamp" else "_glass"))[:40] + ".dds"
            pack.add_texture(nm, Image.new("RGBA", (32, 32), col))
            glass_slots[which] = nm
        return glass_slots[which]

    def build(select, tag):
        ms = W.parse_obj(fbx, 10.0, 0, True, flip_faces, off, fbx_select=select)
        try:
            import uvgen
            if parts and uvgen.needs_unwrap(ms):                  # no UVs in the FBX: make some
                ms = uvgen.unwrap(ms)
        except ImportError:
            pass
        keep = []
        for m in ms:
            if _SHADOW.search(m.material.lower()) or _SHADOW.search(m.name.lower()) or _ALPHA.search(m.material.lower()):
                continue
            t = texture_for(m.material)
            kind = mat_kind(m.material)
            if not t and kind == "glass":                         # the game tints glass through a small texture (stock: body02_colA01)
                t = glass_texture("lamp" if "light" in m.material.lower() else "win")
            if not t and kind in ("paint", "flat"):
                missing.add(m.material)
            m.material = ("%s__%s" % (os.path.splitext(t)[0], kind) if t else ("untextured__%s" % kind))
            keep.append(m)
        return keep

    if clear:
        for c in pack.chunks:                                 # clear the donor's geometry, keep the part table
            if c.tag != W.WPK_SHADOW:
                c.batches = []
    plan = [(0, bset, "Body")]
    for tag, ps_ in sorted(wheel_paths.items()):
        plan.append((tag, set(ps_), "Wheel"))
        tyre = [p for p in ps_ if "/Gom/" in p]
        if parts and tyre and len(tyre) < len(ps_):         # the rubber is its own mesh: tyre-only slot 0x12a..0x12d (for the other wheel set)
            plan.append((0x12a + tag - 0x100, set(tyre), "Tyre"))
    for tag, ps_ in sorted(wing_paths.items()):
        plan.append((tag, set(ps_), "Wing"))
    for tag, ps_ in sorted(bonnet_paths.items()):
        plan.append((tag, set(ps_), "Bonnet"))
    if only_tags is not None:
        plan = [x for x in plan if x[0] in only_tags or (x[0] == 0 and 0x120 in only_tags)]
    report, errors = {}, []
    for tag, pset, label in plan:
        try:
            ms = build(lambda p, pset=pset: p in pset, tag)
            if not ms:
                continue
            if tag == 0 and parts and (only_tags is None or 0x120 in only_tags or 0 in only_tags):
                ms, mir = split_mirrors(ms)
                if mir:
                    res = W.replace_parts(pack, mir, default_tag=0x120, only_tags=None)
                    report[0x120] = res[0x120][0]
            if tag == 0 and parts and (only_tags is None or 0 in only_tags):
                ms, lamps = split_lamps(ms)
                for ltag, lms in sorted(lamps.items()):
                    res = W.replace_parts(pack, lms, default_tag=ltag, only_tags=None)
                    report[ltag] = res[ltag][0]
            res = W.replace_parts(pack, ms, default_tag=tag, only_tags=None)
            report[tag] = res[tag][0]
            if label == "Wheel" and parts and not any("/Gom/" in p for p in pset) and (only_tags is None or (tag + 0x2a) in only_tags):
                ty = split_tyre(ms)
                if ty:
                    r2 = W.replace_parts(pack, ty, default_tag=0x12a + tag - 0x100, only_tags=None)
                    report[0x12a + tag - 0x100] = r2[0x12a + tag - 0x100][0]
        except W.EditError as ex:
            errors.append("%s %s: %s" % (label, hex(tag), ex))
            log("  skipped %s %s: %s" % (label, hex(tag), ex))
    # the body's main paint texture is the one the game swaps for the paint colours: name it body01 and keep the p1..p5 copies
    body_ch = [c for c in pack.chunks if c.tag == 0][0]
    use = {}
    for b in (body_ch.batches if any(x[0] == 0 for x in plan) else []):
        if b.tex != W.NO_TEX and b.tex in tex_img:
            use[b.tex] = use.get(b.tex, 0) + len(b.I) // 3
    if use:
        main = max(use, key=use.get)
        nm = (stem + "_body01_white")[:40] + ".dds"
        pack.tex[main]["name"] = nm
        for k in range(1, 6):
            pack.add_texture("p%d:%s" % (k, nm), tex_img[main])
        if palette:                                           # the five in-game paint colours (blue, red, white, silver, black)
            try:
                import livery
                livery.apply_palette(pack)
            except ImportError:
                pass
    if any(x[0] == 0 for x in plan):
        W.rebuild_shadow(pack)
    if prune:
        prune_textures(pack)
    if only_tags is None or 0 in only_tags:
        try:                                               # solid lens covers hide the bulbs in the game: make them glass
            lamp = W.fix_headlights(pack)
            if lamp["covers"] or lamp["bulbs"]:
                log("headlights: " + " ".join(lamp["notes"]))
        except Exception as ex:                            # never lose an import over this
            log("headlights not fixed: %s" % ex)
    return dict(tris=report, hub=hub, textures=list(tex_slots.values()), missing=sorted(missing), body_aero=body_aero, nowheels=not allw, errors=errors)


def convert(folder, donor, out, name=None, flip_faces=False, log=print, kit=0, palette=True):
    fbx = find_fbx(folder)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    pack = W.Pack.load(donor)
    r = fill_pack(pack, fbx, name or derive_name(folder), flip_faces, log, kit, palette=palette)
    pack.check()
    r["bytes"] = pack.save(out, backup=False)["bytes"]
    return r


def fbx_overview(fbx):
    """{kit: {tag: triangle-less path count}} of what the FBX holds, for the Replace dialog: (kits available, parts of the stock car)"""
    import fbx_io
    paths = fbx_io.mesh_paths(fbx)
    kits = kits_available(paths)
    b, w, wg, bn = split_parts(paths, 0)
    parts = {0: len(b)}
    for d in (w, wg, bn):
        for tag, ps in d.items():
            parts[tag] = len(ps)
    return kits, parts


def fbx_mapping(fbx, kit=0):
    """Return the exact FBX mesh paths assigned to each WPK part for the chosen kit.

    The mapping is intentionally read-only: it lets the import dialog show which
    hierarchy entries will be used before fill_pack changes the destination pack.
    """
    import fbx_io
    paths = fbx_io.mesh_paths(fbx)
    body, wheels, wings, bonnets = split_parts(paths, kit)
    out = {0: list(body)}
    for group in (wheels, wings, bonnets):
        for tag, mesh_paths in group.items():
            out.setdefault(tag, []).extend(mesh_paths)
    return {tag: sorted(mesh_paths) for tag, mesh_paths in out.items()}


def kit_path(out, k):
    return os.path.splitext(out)[0] + "_aero%s.wpk" % "ABC"[k - 1]


def convert_with_kits(folder, donor, out, name=None, flip_faces=False, log=print, kits=True, palette=True):
    """OUT.wpk plus OUT_aeroA/B/C.wpk for every aero kit the FBX has -> {0: result, 1: .., ..}"""
    import fbx_io
    res = {0: convert(folder, donor, out, name, flip_faces, log, 0, palette)}
    if kits:
        for k in kits_available(fbx_io.mesh_paths(find_fbx(folder))):
            res[k] = convert(folder, donor, kit_path(out, k), name, flip_faces, log, kit=k, palette=palette)
    return res


def prune_textures(pack):
    """drop the donor textures nothing uses any more (keeps env/spec maps in use and the p1: .. p5: paint copies of a used one)"""
    NO = W.NO_TEX
    used = set()
    for c in pack.chunks:
        for b in c.batches:
            for k in (b.tex, b.env, b.spec):
                if k != NO and k < len(pack.tex):
                    used.add(k)
    env, spec = W.reflection_indices(pack)                  # the reflection / specular maps stay even when no material uses them yet,
    used |= {k for k in (env, spec) if k is not None}       # so Gloss can switch paint on later
    names = {pack.tex[i]["name"] for i in used}
    keep = [i for i, t in enumerate(pack.tex) if i in used or (re.match(r"p\d:", t["name"]) and t["name"][3:] in names)]
    remap = {old: new for new, old in enumerate(keep)}
    pack.tex = [pack.tex[i] for i in keep]
    for c in pack.chunks:
        for b in c.batches:
            b.tex = remap.get(b.tex, NO) if b.tex != NO else NO
            b.env = remap.get(b.env, NO) if b.env != NO else NO
            b.spec = remap.get(b.spec, NO) if b.spec != NO else NO
    return len(keep)


def preview(path, out_png, views=((-35, 20), (90, 5), (145, 20)), stock=None):
    import wpk_view
    p = W.Pack.load(path)
    model = W.view_model(p, stock)
    # hide alternates like the viewer does
    model = (model[0], wpk_view.default_chunks(model), model[2])
    tiles = []
    for yaw, pit in views:
        g = wpk_view.rasterize(model, yaw, pit, 640, 360, ss=2)
        tiles.append(wpk_view.compose(g, model[0]).convert("RGB"))
    sheet = Image.new("RGB", (640 * len(tiles), 360))
    for i, t in enumerate(tiles):
        sheet.paste(t, (640 * i, 0))
    sheet.save(out_png)


def car_folders(root):
    """folders holding a .fbx under root (root itself when it is one car)"""
    return sorted({os.path.dirname(p) for p in glob.glob(os.path.join(root, "**", "*.fbx"), recursive=True)})


def convert_all(root, donor, out_dir, log=print, progress=None, cancelled=lambda: False, skip_existing=True, kits=True, palette=True):
    """every car folder under root -> out_dir/<CarXXXXXX>_<name>.wpk ; a car that fails is listed and skipped.
    progress(done, total, text) after each car.  -> (converted, [(folder, error)], skipped)"""
    os.makedirs(out_dir, exist_ok=True)
    folders = car_folders(root)
    bad, n, skipped = [], 0, 0
    for k, f in enumerate(folders):
        if cancelled():
            break
        car = os.path.basename(os.path.dirname(f)) if os.path.basename(f).startswith("Car") is False else os.path.basename(f)
        try:
            nm = derive_name(f)
            dest = os.path.join(out_dir, "%s_%s.wpk" % (os.path.basename(f), nm))
            if skip_existing and os.path.exists(dest):
                skipped += 1
            else:
                rs = convert_with_kits(f, donor, dest, nm, False, log, kits, palette)
                n += 1
                r = rs[0]
                log("%s  %.1f MB  %d tris%s" % (nm, r["bytes"] / 1e6, sum(r["tris"].values()),
                                              ("  + aero kit " + ", ".join("ABC"[k - 1] for k in rs if k)) if len(rs) > 1 else ""))
        except Exception as ex:
            bad.append((f, str(ex)))
            log("FAILED %s: %s" % (f, ex))
        if progress:
            progress(k + 1, len(folders), os.path.basename(f))
    return n, bad, skipped


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--all":
        n, bad, skipped = convert_all(sys.argv[2], sys.argv[3], sys.argv[4], kits="--no-kits" not in sys.argv, palette="--no-palette" not in sys.argv)
        print("%d cars converted, %d already there, %d failed" % (n, skipped, len(bad)))
        sys.exit(0 if not bad else 1)
    a = [x for x in sys.argv[1:]]
    opt = {}
    for k in ("--name", "--preview"):
        if k in a:
            i = a.index(k); opt[k] = a[i + 1]; del a[i:i + 2]
    flip = "--flip-faces" in a
    a = [x for x in a if x not in ("--flip-faces", "--no-kits", "--no-palette")]
    if len(a) != 3:
        print(__doc__); sys.exit(2)
    rs = convert_with_kits(a[0], a[1], a[2], opt.get("--name"), flip, kits="--no-kits" not in sys.argv, palette="--no-palette" not in sys.argv)
    r = rs[0]
    for k in rs:
        if k:
            print("aero kit %s: %s" % ("ABC"[k - 1], kit_path(a[2], k)))
    print("wrote %s (%.2f MB), triangles per part: %s" % (a[2], r["bytes"] / 1e6, {hex(k): v for k, v in r["tris"].items()}))
    if "--preview" in opt:
        preview(a[2], opt["--preview"])
