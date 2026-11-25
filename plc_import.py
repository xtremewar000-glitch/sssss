"""Import an arcade car (playerCar/PLC_<car>/PLC_<car>.mdl + .tex of the original game data) into a Re3DX+ car pack:
the body of the chosen arcade aero kit (N = standard, A ... K) replaces the Body of the .wpk you have open (the stock
car or one of its aero kits).  Wheels, lamps, mirrors, wings, bonnets stay as the .wpk has them.

  python plc_import.py CAR.wpk PLC_FOLDER KIT [OUT.wpk] [--no-inner] [--no-shadow] [--no-bonnet]
"""
import os
import re
import struct
import sys

import numpy as np
from PIL import Image

import arcade_mdl as A
import wpk_edit as E
from wpk_edit import VERTEX_LIT, NO_TEX, Batch, EditError


def find_files(folder):
    """-> (mdl path, [tex paths]) for a PLC_<car> folder (or the folder above one)"""
    folder = os.path.abspath(folder)
    name = os.path.basename(folder)
    if not os.path.isfile(os.path.join(folder, name + '.mdl')):
        sub = [d for d in os.listdir(folder) if os.path.isfile(os.path.join(folder, d, d + '.mdl'))]
        if len(sub) == 1:
            folder = os.path.join(folder, sub[0])
            name = sub[0]
        else:
            raise EditError('%s has no <car>.mdl (pick a playerCar/PLC_<car> folder).' % folder)
    mdl = os.path.join(folder, name + '.mdl')
    tex = {k: os.path.join(folder, name + sfx) for k, sfx in (('main', '.tex'), ('aero', '_AERO_SET.tex'), ('change', '_CHANGE.tex'))}
    return name, mdl, {k: v for k, v in tex.items() if os.path.isfile(v)}


# the game data and the app name some cars differently (app name -> arcade name)
ALIASES = {'GG3P_SCHOOL': 'GG3PS', 'R35': 'BNR35', 'R35_SPECV': 'BNR35V', 'GDB_SPECC': 'GDB_C', 'JZS161_TAXI': 'JZS161T',
           'KZH100G_HILIFT': 'KZH100GH', 'UCF10_TAXI': 'UCF10T'}


def guess_folder(wpk_path, where):
    """the arcade folder of the car a .wpk is (maz_FC3S_aeroA.wpk -> PLC_FC3S) inside `where` (a PLC_ folder, or the folder
    holding them, e.g. playerCar); None when it cannot tell"""
    where = os.path.abspath(where)
    n = os.path.basename(where)
    if os.path.isfile(os.path.join(where, n + '.mdl')):
        return where
    key = re.sub(r'_aero[ABC]$', '', os.path.splitext(os.path.basename(wpk_path))[0], flags=re.I).split('_', 1)[-1].upper()
    key = ALIASES.get(key, key)
    for base in (where, os.path.join(where, 'playerCar')):
        if os.path.isdir(base):
            for d in os.listdir(base):
                if d.upper() == 'PLC_' + key and os.path.isfile(os.path.join(base, d, d + '.mdl')):
                    return os.path.join(base, d)
    return None


class Arcade:
    """the decoded parts of an arcade car, found by their names (the names' own prefix is used, not the folder's:
    some folders hold another car's model, and some use lower case)"""

    PART = r'(?:aero|body|bonnet|inner|mirror|rearspoiler|muffler|lamp|trunk|glass|driver|numcover)'

    def __init__(self, folder):
        self.name, self.mdl_path, self.tex_paths = find_files(folder)
        items, _ids = A.read_xmd(self.mdl_path)
        self.items = items
        self.decoded = {}
        self.by_name = {}                 # lower-case part name -> item
        pre = {}
        for i, b in enumerate(items):
            try:
                nm = A.parse_ndwd(b)['names']
            except Exception:
                continue
            if nm:
                self.by_name.setdefault(nm[-1].lower(), i)
                m = re.match(r'^(PLC_.+?)_%s_' % self.PART, nm[-1], re.I)
                if m:
                    pre[m.group(1).lower()] = pre.get(m.group(1).lower(), 0) + 1
        self.prefix = max(pre, key=pre.get) if pre else 'plc_' + self.name[4:].lower()
        self.foreign = self.prefix != self.name.lower()      # the folder holds another car's model
        self.textures = None

    def part(self, i):
        if i not in self.decoded:
            self.decoded[i] = A.decode_ndwd(self.items[i])
        return self.decoded[i]

    def tris(self, i):
        return sum(len(po['tris']) for me in self.part(i)['meshes'] for po in me['polys'])

    def find(self, suffix):
        return self.by_name.get(('%s_%s' % (self.prefix, suffix)).lower())

    def kits(self):
        out = []
        for nm in self.by_name:
            m = re.match(r'^%s_aero_([a-z])$' % re.escape(self.prefix), nm)
            if m:
                out.append(m.group(1).upper())
        if self.find('body_N') is not None and 'N' not in out:
            out.append('N')
        return sorted(set(out), key=lambda k: (k != 'N', k))

    def trunk(self, kit):
        """the boot lid made for this kit (the names end in the letters of the kits that use it)"""
        for nm, i in self.by_name.items():
            m = re.match(r'^%s_trunk_n_([a-z]+)$' % re.escape(self.prefix), nm)
            if m and kit.lower() in m.group(1):
                return i
        return self.find('trunk_N')

    def _keys(self, i):
        d = self.part(i)
        P = np.concatenate([po['V']['p'] for me in d['meshes'] for po in me['polys']]).astype(np.float64)
        return set(map(tuple, np.round(P, 1).tolist()))

    def shell_items(self, kit):
        """the items that make up the body of kit `kit`: the kit's own full shell when it already holds the plain body
        (most of the plain body's vertices are in it), else the plain body plus the kit's add-on parts"""
        aero = self.find('aero_%s' % kit)
        body = self.find('body_N')
        if body is None or aero is None:
            return [x for x in (aero, body) if x is not None]
        kb = self._keys(body)
        return [aero] if kb and len(kb & self._keys(aero)) / len(kb) >= 0.6 else [body, aero]

    KITPART = re.compile(r'^(?:aero|body|bonnet|inner|mirror|rearspoiler|muffler|lamp|trunk|driver|numcover)_', re.I)

    def glass_meshes(self):
        """[(item, mesh)] of the window glass: the first item that is not a kit / part and holds glass polygons (the
        later items are lower detail copies)"""
        pre = self.prefix + '_'
        for i in range(len(self.items)):
            try:
                d = self.part(i)
            except Exception:
                continue
            last = d['names'][-1].lower() if d['names'] else ''
            if last.startswith(pre) and self.KITPART.match(last[len(pre):]):
                continue
            ms = [me for me in d['meshes'] if 'sticker' not in me['name'].lower()
                  and me['polys'] and all((po['matw'][0] & 0xff) == 6 for po in me['polys'])]
            if ms:
                return [(i, me) for me in ms]
        return []

    def hash_textures(self):
        if self.textures is None:
            self.textures = {}
            if 'main' in self.tex_paths:
                for t in A.read_tex(self.tex_paths['main']):
                    if t['hash']:
                        self.textures[t['hash']] = t
        return self.textures


def fit_plate_uv(old, plates, pack, body_tex):
    """the number plate is a baked picture in the car's body texture. Fit (arcade plate uv) -> (that picture's uv) from the
    car's own body, so the new plates show it; falls back to a bright texel. plates: list of vertex arrays (VERTEX_LIT)"""
    pairs = []
    if old is not None:
        S = np.concatenate([b.V for b in old.batches if len(b.V)])
        # the stock plate is a quad about 3.1 x 1.6 whose uv are tiny: find those triangles, match their corners
        cand = []
        for b in old.batches:
            T = b.I.reshape(-1, 3)
            P3 = b.V['pos'][T].astype(np.float64)
            ex = P3[:, :, 0].max(1) - P3[:, :, 0].min(1)
            ey = P3[:, :, 1].max(1) - P3[:, :, 1].min(1)
            uvt = b.V['uv'][T]
            eu = (uvt[:, :, 0].max(1) - uvt[:, :, 0].min(1))
            ev = (uvt[:, :, 1].max(1) - uvt[:, :, 1].min(1))
            sel = (ex > 2.6) & (ex < 3.6) & (ey > 1.2) & (ey < 2.0) & (eu < 0.25) & (ev < 0.25) & (eu + ev > 0.01)
            for t in np.nonzero(sel)[0]:
                cand.append(b.V[T[t]])
        C = np.concatenate(cand) if cand else None
        for V in plates:
            for k in range(len(V)):
                for src, tol in ((S, 0.35), (C, 0.9)):
                    if src is None:
                        continue
                    d = np.linalg.norm(src['pos'] - V['pos'][k], axis=1)
                    j = int(np.argmin(d))
                    if d[j] < tol:
                        pairs.append((V['uv'][k].astype(np.float64), src['uv'][j].astype(np.float64)))
                        break
    sol = None
    if len(pairs) >= 4:
        X = np.array([[u, v, 1.0] for (u, v), _ in pairs])
        Y = np.array([w for _, w in pairs])
        rng = np.random.default_rng(1)
        best = (0, None)
        n = len(X)
        for _ in range(600):
            idx = rng.choice(n, 3, replace=False)
            try:
                cand_sol = np.linalg.solve(X[idx], Y[idx])
            except np.linalg.LinAlgError:
                continue
            if abs(np.linalg.det(cand_sol[:2, :2])) < 0.002:
                continue
            inl = np.abs(X @ cand_sol - Y).max(1) < 0.01
            if inl.sum() > best[0]:
                best = (int(inl.sum()), inl)
        if best[1] is not None and best[0] >= 6:
            sol = np.linalg.lstsq(X[best[1]], Y[best[1]], rcond=None)[0]
            if np.abs(X[best[1]] @ sol - Y[best[1]]).max() > 0.01 or abs(np.linalg.det(sol[:2, :2])) < 0.002:
                sol = None
    if sol is not None:                                            # all four corners of the picture must lie inside the texture
        cs = np.c_[np.array([[0, 0], [1, 0], [0, 1], [1, 1]], np.float64), np.ones(4)] @ sol
        if cs.min() < -0.002 or cs.max() > 1.002:
            sol = None
    if sol is not None:
        return lambda uv: (np.c_[uv, np.ones(len(uv))] @ sol).astype(np.float32)
    # no fit: a flat bright patch of the body texture (a blank plate), else no plate at all
    t = pack.tex[body_tex] if body_tex < len(pack.tex) else None
    if t and t['raw']:
        im = E.decode16(t['w'], t['h'], t['fmt'], t['raw']).astype(np.int32)
        H, W_ = im.shape[:2]
        lum = im[..., :3].min(2) * (im[..., 3] > 200)
        best = None
        for y in range(2, H - 2, 3):
            for x in range(2, W_ - 2, 3):
                if lum[y, x] > 160:
                    patch = im[y - 2:y + 3, x - 2:x + 3, :3]
                    if patch.min() > 150 and patch.std() < 12:
                        best = (x, y)
                        break
            if best:
                break
        if best:
            c = np.array([(best[0] + 0.5) / W_, (best[1] + 0.5) / H], np.float32)
            return lambda uv: np.tile(c, (len(uv), 1))
    return None


def poly_batch_pieces(po, flip_v=True):
    """one decoded polygon -> (VERTEX_LIT array, int triangles)"""
    V = po['V']
    n = len(V)
    out = np.zeros(n, VERTEX_LIT)
    out['pos'] = V['p']
    uv = V['uv0'].astype(np.float32) if 'uv0' in V.dtype.names else np.zeros((n, 2), np.float32)
    if flip_v:
        uv[:, 1] = 1.0 - uv[:, 1]
    out['uv'] = uv
    out['rgba'] = 255
    if 'c' in V.dtype.names:                                       # the arcade's vertex colour (86% the same as the app's own packs)
        out['rgba'] = V['c']
    if 'n' in V.dtype.names:
        nr = V['n'][:, :3].astype(np.float32)
        out['normal'] = np.clip(np.round(nr * 32767), -32767, 32767)
    return out, po['tris']


def tex_slot(pack, arc, hsh, cache, add=True):
    """texture slot in `pack` for an arcade hash texture: the pack's own copy when it has one (the packs keep the
    arcade pictures upside down, as the UVs are), else the picture is added, flipped the same way"""
    if hsh in cache:
        return cache[hsh]
    t = arc.hash_textures().get(hsh)
    if t is None:
        cache[hsh] = NO_TEX
        return NO_TEX
    im = A.decode_dxt(t['w'], t['h'], t['fmt'], t['data']).transpose(Image.FLIP_TOP_BOTTOM)
    ref = np.asarray(im, np.float32)
    for k, pt in enumerate(pack.tex):
        if pt['raw'] and (pt['w'], pt['h']) == im.size and not re.match(r'^p\d+:', pt['name']):
            q = E.decode16(pt['w'], pt['h'], pt['fmt'], pt['raw']).astype(np.float32)
            m = (ref[..., 3] > 200) | (q[..., 3] > 200)
            if m.any() and float(np.abs(ref[..., :3] - q[..., :3])[m].mean()) < 14:
                cache[hsh] = k
                return k
    if not add:                                                    # only pictures the car already has
        cache[hsh] = NO_TEX
        return NO_TEX
    cache[hsh] = pack.add_texture('plc_%08x.tga' % hsh, im, 256)
    return cache[hsh]


class _Build:
    """collects arcade polygons into the pack's materials"""

    def __init__(self, pack, arc):
        self.pack, self.arc = pack, arc
        self.body_tex = next((k for k, t in enumerate(pack.tex) if 'body01' in t['name'].lower() and not re.match(r'^p\d+:', t['name'])), 0)
        self.old = pack.chunk(0)
        self.paint = pack.preset('paint')
        self.gl = pack.preset('glass')
        self.gtex = next((k for k, t in enumerate(pack.tex) if re.search(r'body02_col', t['name'], re.I)), None)
        if self.gtex is None:
            self.gtex = next((k for k, t in enumerate(pack.tex) if 'cola' in t['name'].lower()), NO_TEX)
        self.cache = {}
        self.groups = {}                    # material tuple -> [(V, tris)]
        self.plate_polys = []
        self.glass_uv = None
        self.clear_uv = None
        self.paint_uv = None
        self.flat_uv = None
        self.stock = {}                                            # rounded position -> [(uv, material, normal)]
        if self.old is not None:
            gcount, pcount, fcount = {}, {}, {}
            puv, fuv = [], []
            for b in self.old.batches:
                if not len(b.V):
                    continue
                uvr = np.round(b.V['uv'], 3)
                for k in range(len(b.V)):
                    key = tuple(np.round(b.V['pos'][k].astype(np.float64), 2))
                    self.stock.setdefault(key, []).append((b.V['uv'][k].copy(), b.material(), b.V['normal'][k].astype(np.float32) / 32767))
                tgt = None
                if b.tex == self.gtex and b.flags & 3 == 1:
                    tgt = gcount
                elif b.tex == self.body_tex and b.prog == 1:
                    tgt = pcount
                    puv.append(b.V['uv'])
                elif b.tex == self.body_tex and b.prog == 0 and b.flags & 3 == 0:
                    tgt = fcount
                    fuv.append(b.V['uv'])
                if tgt is not None:
                    if tgt is gcount:                              # the windows are the upper part of the glass batch (lamp lenses sit low)
                        up = b.V['pos'][:, 1] >= np.quantile(b.V['pos'][:, 1], 0.4)
                        uvr = uvr[up]
                    u, cnt = np.unique(uvr, axis=0, return_counts=True)
                    for uu, cc in zip(u, cnt):
                        tgt[tuple(uu)] = tgt.get(tuple(uu), 0) + int(cc)
            mode = lambda d: np.array(max(d, key=d.get), np.float32) if d else None
            self.paint_uv, self.flat_uv = mode(pcount), mode(fcount)
            bim = self._body_pixels()
            if bim is not None:                                    # the typical colour, not just the commonest texel (which can be a dark crease)
                self.paint_uv = self._typical(puv, bim, self.paint_uv)
                self.flat_uv = self._typical(fuv, bim, self.flat_uv)
            self.glass_uv = None
            gim = None
            t = self.pack.tex[self.gtex] if self.gtex is not None and self.gtex < len(self.pack.tex) else None
            if t is not None and t['raw']:
                gim = E.decode16(t['w'], t['h'], t['fmt'], t['raw'])
            for uu, cc in sorted(gcount.items(), key=lambda kv: -kv[1]):
                if gim is None:
                    self.glass_uv = np.array(uu, np.float32)
                    break
                h, w = gim.shape[:2]
                px = gim[min(int(uu[1] * h), h - 1) % h, min(int(uu[0] * w), w - 1) % w]
                if px[3] >= 180 and px[:3].max() < 110:                # the window tint: dark and nearly opaque
                    self.glass_uv = np.array(uu, np.float32)
                    break
            if self.glass_uv is None and gim is not None:
                for uu, cc in sorted(gcount.items(), key=lambda kv: -kv[1]):
                    px = gim[min(int(uu[1] * gim.shape[0]), gim.shape[0] - 1) % gim.shape[0], min(int(uu[0] * gim.shape[1]), gim.shape[1] - 1) % gim.shape[1]]
                    if px[3] >= 60:
                        self.glass_uv = np.array(uu, np.float32)
                        break
            if self.glass_uv is None:
                self.glass_uv = mode(gcount)
            self.clear_uv = None                                       # a clear texel for lamp covers (the lens shows through, with the glass shine)
            if gim is not None:
                ys, xs = np.nonzero(gim[..., 3] <= 8)
                if len(ys):
                    k = len(ys) // 2
                    self.clear_uv = np.array([(xs[k] + 0.5) / gim.shape[1], (ys[k] + 0.5) / gim.shape[0]], np.float32)

    def add(self, po, mat, uv=None):
        V, T = poly_batch_pieces(po)
        if uv is not None:
            V['uv'] = uv
        if len(T):
            self.groups.setdefault(mat, []).append((V, T))

    def _body_pixels(self):
        t = self.pack.tex[self.body_tex] if self.body_tex < len(self.pack.tex) else None
        if t is None:
            return None
        if t['raw']:
            return E.decode16(t['w'], t['h'], t['fmt'], t['raw'])
        st = E.stock_pack_of(self.pack.path) if self.pack.path else None          # an aero kit borrows it from the stock pack
        j = st.tex_index(t['name']) if st else None
        if j is not None and st.tex[j]['raw']:
            return E.decode16(st.tex[j]['w'], st.tex[j]['h'], st.tex[j]['fmt'], st.tex[j]['raw'])
        return None

    @staticmethod
    def _typical(uvs, im, fallback):
        if not uvs:
            return fallback
        uv = np.concatenate(uvs)
        h, w = im.shape[:2]
        x = np.clip((uv[:, 0] * w).astype(int), 0, w - 1)
        y = np.clip((uv[:, 1] * h).astype(int), 0, h - 1)
        col = im[y, x, :3].astype(np.float32)
        med = np.median(col, axis=0)
        k = int(np.argmin(np.abs(col - med).sum(1)))
        return uv[k].astype(np.float32)

    def _stock_arrays(self):
        if getattr(self, '_sa', None) is None:
            P_, U_, N_, M_ = [], [], [], []
            for lst_pos, lst in self.stock.items():
                for uv_, mat_, nrm_ in lst:
                    P_.append(lst_pos); U_.append(uv_); N_.append(nrm_); M_.append(mat_)
            self._sa = (np.array(P_, np.float32), np.array(U_, np.float32), np.array(N_, np.float32), M_)
        return self._sa

    def match(self, V, tol=0.35):
        """for each vertex of V: the stock vertex at the same place (normal closest), else the nearest one within `tol` units whose
        normal points the same way -> (uvs, materials, hit mask)"""
        n = len(V)
        uv = V['uv'].copy()
        mats = [None] * n
        hit = np.zeros(n, bool)
        nr = V['normal'].astype(np.float32) / 32767
        for k in range(n):
            c = self.stock.get(tuple(np.round(V['pos'][k].astype(np.float64), 2)))
            if c:
                best = max(c, key=lambda e: float(np.dot(e[2], nr[k])))
                uv[k], mats[k], hit[k] = best[0], best[1], True
        if self.stock:
            SP, SU, SN, SM = self._stock_arrays()
            for ptol, pdot in ((tol, 0.7),):                       # the same place (a further tier smeared the stock plate and lamp pictures onto new shapes)
                rest = np.nonzero(~hit)[0]
                for a in range(0, len(rest), 400):
                    idx = rest[a:a + 400]
                    d = np.linalg.norm(V['pos'][idx].astype(np.float32)[:, None, :] - SP[None, :, :], axis=2)
                    d = d + 10.0 * (np.einsum('ik,jk->ij', nr[idx], SN) < pdot)
                    j = np.argmin(d, axis=1)
                    ok = d[np.arange(len(idx)), j] < ptol
                    for q, jj, good in zip(idx, j, ok):
                        if good:
                            uv[q], mats[q], hit[q] = SU[jj], SM[jj], True
        return uv, mats, hit

    def add_plain(self, po, kind):
        """a painted ('paint') or unpainted ('flat') surface. Triangle by triangle: where the car's own body has the same
        surface (same place) it keeps its uv and material (the grille mesh, the indicators, the lens detail ...); a triangle
        the car does not have gets a plain paint / plain dark-plastic texel, so no lamp or badge picture lands on it."""
        V, T = poly_batch_pieces(po)
        if not len(T):
            return
        p = self.paint
        if kind == 'lens':                                         # headlight lens: the car's own where it has it, else the decal sheet with reflections
            mat_new, tex_uv = (self.gtex,) + tuple(self.gl), None
        elif kind == 'cover':                                      # lamp cover: clear glass over the lens
            mat_new, tex_uv = (self.gtex,) + tuple(self.gl), (self.clear_uv if self.clear_uv is not None else self.glass_uv)
        else:
            mat_new = (self.body_tex, p[0], p[1], p[2], p[3], p[4]) if kind in ('paint', 'mixed') else (self.body_tex, 0, 0, NO_TEX, NO_TEX, 0)
            tex_uv = self.paint_uv if kind in ('paint', 'mixed') else self.flat_uv
        if not self.stock:
            self.groups.setdefault(mat_new, []).append((V, T))
            return
        uv, mats, hit = self.match(V) if kind != 'cover' else (V['uv'], [None] * len(V), np.zeros(len(V), bool))
        th = hit[T].sum(1)
        stock_t = th >= 2
        # triangles the car does not have: a plain texel (paint, or dark plastic for things facing down)
        new_t = ~stock_t
        if new_t.any():
            Tn = T[new_t]
            if kind == 'mixed':
                ny = V['normal'][:, 1].astype(np.float32) / 32767
                down = ny[Tn].mean(1) < -0.4
                parts = ((Tn[down], 'flat'), (Tn[~down], 'paint'))
            else:
                parts = ((Tn, kind),)
            for Tk, kk in parts:
                if not len(Tk):
                    continue
                if kk == 'lens':
                    mk, uk = (self.gtex,) + tuple(self.gl), None
                elif kk == 'cover':
                    mk, uk = (self.gtex,) + tuple(self.gl), (self.clear_uv if self.clear_uv is not None else self.glass_uv)
                elif kk == 'paint':
                    mk, uk = (self.body_tex, p[0], p[1], p[2], p[3], p[4]), self.paint_uv
                else:
                    mk, uk = (self.body_tex, 0, 0, NO_TEX, NO_TEX, 0), self.flat_uv
                Vn = V.copy()
                if uk is not None:
                    Vn['uv'] = uk
                self.groups.setdefault(mk, []).append((Vn, Tk))
        if not stock_t.any():
            return
        Ts = T[stock_t]
        # a vertex the car does not have, inside a triangle it does: copy the uv of a vertex of that triangle it does have
        Vs = V.copy()
        Vs['uv'] = uv
        extra, ex_uv = [], []
        T2 = Ts.copy()
        tmat = []
        for r in range(len(Ts)):
            tri = Ts[r]
            h = hit[tri]
            ms = [mats[v] for v, ok in zip(tri, h) if ok]
            cnt = {}
            for m in ms:
                cnt[m] = cnt.get(m, 0) + 1
            tmat.append(max(cnt, key=cnt.get))
            if not h.all():
                src = int(tri[int(np.argmax(h))])
                for c in range(3):
                    if not h[c]:
                        extra.append(int(tri[c]))
                        ex_uv.append(uv[src])
                        T2[r, c] = len(Vs) + len(extra) - 1
        if extra:
            Ve = V[np.array(extra)].copy()
            Ve['uv'] = np.array(ex_uv, np.float32)
            Vs = np.concatenate([Vs, Ve])
        by = {}
        for r, m in enumerate(tmat):
            by.setdefault(m, []).append(r)
        for m, rows in by.items():
            self.groups.setdefault(m, []).append((Vs, T2[np.array(rows)]))

    def glass(self, po):
        g = self.gl
        self.add(po, (self.gtex, g[0], g[1], g[2], g[3], g[4]), self.glass_uv)

    def add_item(self, idx, role='shell', only=None):
        d = self.arc.part(idx)
        pk, p = self.pack, self.paint
        for me in d['meshes']:
            if only and only not in me['name'].lower():
                continue
            for po in me['polys']:
                typ = po['matw'][0] & 0xff
                hs = po['hashes']
                if typ == 6:                                    # lamp covers (the windows come from glass_meshes): the car's own where it has them
                    self.add_plain(po, 'cover')
                elif typ == 5:                                  # headlight lens
                    self.add_plain(po, 'lens')
                elif role == 'plate':
                    self.plate_polys.append(poly_batch_pieces(po))
                elif typ == 0:
                    self.add_plain(po, 'paint')
                elif typ == 7:                                  # lamp glow: added light
                    t = tex_slot(pk, self.arc, hs[0], self.cache) if hs else NO_TEX
                    self.add(po, (t, 2, 0, NO_TEX, NO_TEX, 0))
                elif typ >= 10:                                 # neon / helper surfaces: not drawn
                    continue
                elif hs and typ == 8:                           # decals / badges: their own picture (left out when it is not there)
                    t = tex_slot(pk, self.arc, hs[0], self.cache, add=False)
                    if t != NO_TEX:
                        self.add(po, (t, 0, 0, NO_TEX, NO_TEX, 0))
                elif typ == 8:                                  # (49% of these are painted on the app's own cars, 8% plastic)
                    self.add_plain(po, 'paint')
                else:                                           # type 9: half painted, half plastic: by where the surface faces
                    self.add_plain(po, 'mixed')

    def add_plates(self):
        if self.plate_polys:
            f = fit_plate_uv(self.old, [V for V, _ in self.plate_polys], self.pack, self.body_tex)
            if f is not None:
                for V, T in self.plate_polys:
                    V['uv'] = f(V['uv'])
                    self.groups.setdefault((self.body_tex, 0, 0, NO_TEX, NO_TEX, 0), []).append((V, T))

    def batches(self):
        out = []
        for mat, items in sorted(self.groups.items(), key=lambda kv: (kv[0][1] & 3 != 0, kv[0][0], kv[0][2])):
            tex, fl, prog, env, spec, pw = mat
            vbuf, ibuf, base = [], [], 0
            for V, T in items:
                used, inv = np.unique(T, return_inverse=True)
                if base + len(used) > E.POOL:
                    out.append(Batch(tex, fl, prog, 0, env, spec, pw, np.concatenate(vbuf), np.concatenate(ibuf)))
                    vbuf, ibuf, base = [], [], 0
                vbuf.append(V[used])
                ibuf.append((inv.reshape(-1) + base).astype('<u2'))
                base += len(used)
            if vbuf:
                out.append(Batch(tex, fl, prog, 0, env, spec, pw, np.concatenate(vbuf), np.concatenate(ibuf)))
        return out


def _foreign_note(arc):
    return '  (WARNING: this folder holds the model of %s, not of %s)' % (arc.prefix[4:].upper(), arc.name[4:]) if arc.foreign else ''


def import_kit(pack, arc, kit, inner=True, shadow=True, bonnet=True, log=print):
    """replace pack's Body with the arcade kit's body; returns a short report"""
    shells = arc.shell_items(kit)
    if not shells:
        raise EditError('%s has no aero kit %s (it has %s).' % (arc.name, kit, ', '.join(arc.kits()) or 'none'))
    B = _Build(pack, arc)
    old_tris = B.old.counts()[2] if B.old else 0
    for i in shells:
        B.add_item(i)
    for nm in ('aero_%s_l0' % kit, 'body_N_l0'):                          # the fine-detail companions of the shell
        j = arc.find(nm)
        if j is not None and j not in shells and (nm != 'body_N_l0' or len(shells) == 2 or arc.find('aero_%s' % kit) not in shells):
            B.add_item(j)
    j = arc.trunk(kit)                                                  # boot lid
    if j is not None:
        B.add_item(j)
    j = arc.find('numcover_N')                                          # number plates (a baked picture in the body texture)
    if j is not None:
        B.add_item(j, 'plate')
        B.add_plates()
    mu = arc.find('muffler_N_%s' % kit) or (arc.find('muffler_N_ABCEF') if kit in 'ABCEF' else None) or arc.find('muffler_N_N')
    if mu is not None:                                                  # exhaust
        B.add_item(mu)
    for gi, me in arc.glass_meshes():                                   # windows
        for po in me['polys']:
            B.glass(po)
    inn = arc.find('inner_L0')
    if inner and inn is not None:
        B.add_item(inn)
    batches = B.batches()
    pack.set_chunk(0, batches)
    extra = ''
    bj = arc.find('bonnet_%s' % kit) if kit != 'N' else None
    if bonnet and bj is not None and pack.chunk(0x126) is not None:        # the kit's own bonnet takes the place of Bonnet A
        try:
            import_part(pack, arc, 0x126, [bj])
            extra = ' + its bonnet'
        except EditError:
            pass
    if shadow:
        E.rebuild_shadow(pack)
    tris = sum(len(b.I) // 3 for b in batches)
    return 'Body of %s kit %s%s: %d triangles in %d draws (was %d)' % (arc.name, kit, extra, tris, len(batches), old_tris) + _foreign_note(arc)


PART_FAMILIES = (((294, 295, 296, 297), 'bonnet_'), ((290, 291, 292, 293, 321), 'rearspoiler_'), ((288, 289), 'mirror_'),
                 ((272, 273, 274, 275, 276), 'lamp_'))


def part_choices(arc, tag):
    """[(label, item index)] of the arcade parts that make sense for a part group of the .wpk (all parts when unsure)"""
    pre = None
    for tags, p in PART_FAMILIES:
        if tag in tags:
            pre = p
    out = []
    for nm, i in sorted(arc.by_name.items()):
        short = nm[len(arc.prefix) + 1:] if nm.startswith(arc.prefix + '_') else None
        if short is None or (pre and not short.startswith(pre)):
            continue
        out.append(('%s  (%d triangles)' % (short, arc.tris(i)), i))
    return out


def import_part(pack, arc, tag, items):
    """replace one part group (a bonnet, wing, mirror ...) with arcade parts; returns a short report"""
    if not items:
        raise EditError('Pick an arcade part.')
    if tag == 0:
        raise EditError('The body needs the whole kit (windows, lights, plates): use import_kit / Import arcade car.')
    B = _Build(pack, arc)
    old = pack.chunk(tag)
    old_tris = old.counts()[2] if old else 0
    for i in items:
        B.add_item(i)
    batches = B.batches()
    if not batches:
        raise EditError('That arcade part has nothing to draw.')
    pack.set_chunk(tag, batches)
    return 'Part %d: %d triangles (was %d)' % (tag, sum(len(b.I) // 3 for b in batches), old_tris) + _foreign_note(arc)


def cli(argv):
    pos = [a for a in argv if not a.startswith('--')]
    if len(pos) < 3:
        print(__doc__)
        return 2
    try:
        pack = E.Pack.load(pos[0])
        arc = Arcade(pos[1])
        print(import_kit(pack, arc, pos[2].upper(), inner='--no-inner' not in argv, shadow='--no-shadow' not in argv, bonnet='--no-bonnet' not in argv))
        out = pos[3] if len(pos) > 3 else pos[0]
        st = pack.save(out)
        print('wrote %s (%.2f MB)%s' % (out, st['bytes'] / 1e6, ', old file kept as .bak' if st['bak'] else ''))
    except (EditError, OSError) as ex:
        print('error:', ex)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(cli(sys.argv[1:]))
