"""synthetic car pack for tests: boxes in the real WPK3 layout (written and re-read through wpk_edit.Pack)"""
import numpy as np
import wpk_edit as E
from wpk_view import VERTEX_LIT


def box(c, s, col=(200, 200, 200, 255)):
    cx, cy, cz = c
    sx, sy, sz = s
    P = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], np.float32) * (np.array(s) / 2) + np.array(c)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    V = np.zeros(8, VERTEX_LIT)
    V["pos"] = P
    V["rgba"] = col
    V["uv"] = np.random.RandomState(1).rand(8, 2)
    # outward-facing triangles: build both windings per quad then keep the one facing away from centre
    I = []
    for a, b, c_, d in quads:
        for t in ((a, b, c_), (a, c_, d)):
            n = np.cross(P[t[1]] - P[t[0]], P[t[2]] - P[t[0]])
            mid = P[list(t)].mean(0) - np.array(c)
            I.extend(t if np.dot(n, mid) > 0 else t[::-1])
    return V, np.array(I, "<u2")


def make_pack(path):
    p = E.Pack()
    raw = np.full((4, 4), 0xF800, "<u2").tobytes()          # 4x4 RGB565 red
    p.tex = [dict(name="body_red", w=4, h=4, fmt=0, raw=raw), dict(name="borrowed_tex", w=4, h=4, fmt=0, raw=b"")]
    specs = [(0, (0, 0.5, 0), (1.8, 1.0, 4.0), 0), (0x100, (1.1, 0.3, 1.2), (0.3, 0.6, 0.6), 0),
             (0x101, (-1.1, 0.3, 1.2), (0.3, 0.6, 0.6), 0), (0x122, (0, 1.6, -1.8), (1.6, 0.1, 0.4), 1),
             (0x123, (0, 1.9, -1.8), (1.6, 0.1, 0.4), 0), (0x126, (0, 1.15, 1.5), (1.2, 0.1, 0.8), 0),
             (0x111, (1.0, 0.6, 2.0), (0.3, 0.2, 0.1), 0)]
    for tag, c, s, ti in specs:
        V, I = box(c, s)
        b = E.Batch(ti, 0, 1, 0, 0xFFFF, 0xFFFF, 0, V, I)
        ch = E.Chunk(tag, (0, 0, 0), (0, 0, 0), [b])
        ch.update_bbox()
        p.chunks.append(ch)
    p.chunk_size = 10.0
    p.save(path, backup=False)
    return E.Pack.load(path)
