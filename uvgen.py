"""uvgen: automatic UV unwrapping for models that come without UVs (or with broken ones).

Box-projection charts: every triangle goes to the axis its normal points along (+-X, +-Y, +-Z); connected triangles of the same direction
form a chart, a chart is flattened by dropping that axis, and all charts are shelf-packed into the 0..1 square with the same scale
(equal texel density).  Vertices on a chart border are duplicated.

  unwrap(meshes)            -> [Mesh] with new V / tris (wpk_edit.Mesh objects)
  needs_unwrap(meshes)      -> True when the UVs are missing (all zero / no area)
  python uvgen.py in.obj out.obj [--pad 0.004]   writes an OBJ with the new UVs (to paint it in another program)
"""
import sys
import numpy as np
import wpk_edit as W

DIRS = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
# (u axis, v axis, flip u) per direction, so a chart is seen from outside without mirroring
AXES = [(2, 1, 1), (2, 1, -1), (0, 2, 1), (0, 2, -1), (0, 1, -1), (0, 1, 1)]


def needs_unwrap(meshes):
    tot = 0.0
    n = 0
    for m in meshes:
        uv = m.V["uv"].astype(np.float64)[m.tris]
        e1, e2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
        a = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
        tot += float(np.sum(a > 1e-9))
        n += len(m.tris)
    return n > 0 and tot / n < 0.5


def _charts(P, T, N, weld):
    """chart id per triangle: same normal direction + connected through shared (welded) vertices"""
    lab = np.argmax(N @ DIRS.T, axis=1)
    par = np.arange(len(T))
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    owner = {}
    for ti in range(len(T)):
        for vv in weld[T[ti]]:
            key = (int(vv), int(lab[ti]))
            if key in owner:
                ra, rb = find(owner[key]), find(ti)
                if ra != rb:
                    par[rb] = ra
            else:
                owner[key] = ti
    root = np.array([find(i) for i in range(len(T))])
    return np.unique(root, return_inverse=True)[1].reshape(-1), lab


def _pack(sizes, scale, pad):
    """shelf packing of (w, h) boxes (already in uv units * scale); -> (positions, height used)"""
    order = np.argsort([-s[1] for s in sizes])
    x = y = rowh = 0.0
    pos = [None] * len(sizes)
    for i in order:
        w, h = sizes[i][0] * scale + 2 * pad, sizes[i][1] * scale + 2 * pad
        if x + w > 1.0:
            x, y, rowh = 0.0, y + rowh, 0.0
        pos[i] = (x + pad, y + pad)
        x += w
        rowh = max(rowh, h)
    return pos, y + rowh


def unwrap(meshes, pad=0.004):
    """-> new list of Mesh with generated UVs"""
    allp, allt, owner = [], [], []
    for mi, m in enumerate(meshes):
        allp.append(m.V["pos"].astype(np.float64))
        allt.append(m.tris)
    off = np.cumsum([0] + [len(p) for p in allp])
    P = np.concatenate(allp)
    T = np.concatenate([t + off[i] for i, t in enumerate(allt)])
    tri_mesh = np.concatenate([np.full(len(t), i) for i, t in enumerate(allt)])
    v = P[T]
    N = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    ln = np.linalg.norm(N, axis=1)
    keep = ln > 1e-12
    N = N / np.where(keep, ln, 1)[:, None]
    _, weld = np.unique(np.round(P / 0.02).astype(np.int64), axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    chart, lab = _charts(P, T, N, weld)
    nch = int(chart.max()) + 1
    # flatten each chart
    uvc = np.zeros((len(T), 3, 2))
    boxes = []
    for c in range(nch):
        sel = np.nonzero(chart == c)[0]
        ua, va, fl = AXES[int(lab[sel[0]])]
        pts = v[sel]
        u = pts[..., ua] * fl
        w_ = pts[..., va]
        uvc[sel, :, 0], uvc[sel, :, 1] = u, w_
        lo = np.array([u.min(), w_.min()])
        hi = np.array([u.max(), w_.max()])
        uvc[sel] -= lo
        boxes.append((hi[0] - lo[0], hi[1] - lo[1]))
    # biggest scale that fits the square
    lo_s, hi_s = 1e-6, 1.0
    for _ in range(40):
        mid = (lo_s + hi_s) / 2
        _p, used = _pack(boxes, mid, pad)
        if used <= 1.0:
            lo_s = mid
        else:
            hi_s = mid
    scale = lo_s
    pos, _ = _pack(boxes, scale, pad)
    for c in range(nch):
        sel = chart == c
        uvc[sel] = uvc[sel] * scale + np.array(pos[c])
    # new vertices: one per (old vertex, chart); v runs bottom-up like the game's textures
    key = T.reshape(-1).astype(np.int64) * nch + np.repeat(chart, 3)
    ukey, first, inv = np.unique(key, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    uvs = uvc.reshape(-1, 2)[first]
    old_v = (ukey // nch).astype(np.int64)
    out = []
    # split the combined vertex list back into meshes
    mesh_of_vertex = np.concatenate([np.full(len(p), i) for i, p in enumerate(allp)])
    for mi, m in enumerate(meshes):
        selv = np.nonzero(mesh_of_vertex[old_v] == mi)[0]
        nv = m.V[(old_v[selv] - off[mi])].copy()
        nv["uv"] = uvs[selv].astype(np.float32)
        remap = np.full(len(ukey), -1, np.int64)
        remap[selv] = np.arange(len(selv))
        tris = remap[inv.reshape(-1, 3)[tri_mesh == mi]]
        out.append(W.Mesh(m.name, m.material, m.tag, nv, tris.astype(m.tris.dtype)))
    return out


def write_obj(meshes, path, scale=0.1):
    with open(path, "w") as f:
        base = 1
        for m in meshes:
            f.write("o %s\nusemtl %s\n" % (m.name.replace(" ", "_"), m.material.replace(" ", "_")))
            for p in m.V["pos"]:
                f.write("v %.5f %.5f %.5f\n" % (p[0] * scale, p[1] * scale, p[2] * scale))
            for t in m.V["uv"]:
                f.write("vt %.5f %.5f\n" % (t[0], t[1]))
            for t in m.tris:
                f.write("f " + " ".join("%d/%d" % (i + base, i + base) for i in t) + "\n")
            base += len(m.V)


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__)
        sys.exit(2)
    ms = W.parse_obj(a[0], 10.0, 0, False, False)
    pad = float(a[a.index("--pad") + 1]) if "--pad" in a else 0.004
    out = unwrap(ms, pad)
    write_obj(out, a[1])
    print("%d pieces unwrapped -> %s" % (len(out), a[1]))
