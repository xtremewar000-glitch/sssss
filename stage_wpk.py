"""Reader for the game's map packs (.wpk "WPK2": data/hakone.wpk, hakone_night.wpk, sky_hakone.wpk ...), for the Playtest.
Layout: see tools/stage/pack.py of the game's repo.  Pure numpy; no Tk, no GL.

    st = load("hakone.wpk")
    st.textures[i]  -> (name, RGBA uint8 array)
    st.chunks[i]    -> Chunk(lo, hi, group, batches)   group 0 ground, 1 short-range buildings, 2 long-range buildings, 3+ objects
    Batch: tex (-1 none), blend (0 opaque, 1 alpha, 2 additive), flip (flipbook bits), V (structured: pos, uv, rgba), I (u16 triangles)
"""
import struct
import numpy as np

VDT = np.dtype([("pos", "<f4", 3), ("uv", "<f4", 2), ("rgba", "u1", 4)])


class StageError(Exception):
    pass


class Batch:
    __slots__ = ("tex", "blend", "flip", "V", "I")


class Chunk:
    __slots__ = ("lo", "hi", "group", "batches")


class Stage:
    def __init__(self):
        self.textures, self.chunks = [], []
        self.chunk_size = 0.0

    @property
    def n_tris(self):
        return sum(len(b.I) // 3 for c in self.chunks for b in c.batches)


def _tex_rgba(raw, w, h, fmt):
    a = np.frombuffer(raw, "<u2", w * h).astype(np.uint32)
    if fmt == 0:      # RGB565
        r, g, b = (a >> 11) & 31, (a >> 5) & 63, a & 31
        out = np.stack([r * 255 // 31, g * 255 // 63, b * 255 // 31, np.full_like(r, 255)], -1)
    elif fmt == 1:    # RGBA4444
        r, g, b, al = (a >> 12) & 15, (a >> 8) & 15, (a >> 4) & 15, a & 15
        out = np.stack([r * 17, g * 17, b * 17, al * 17], -1)
    elif fmt == 2:    # RGBA5551
        r, g, b, al = (a >> 11) & 31, (a >> 6) & 31, (a >> 1) & 31, a & 1
        out = np.stack([r * 255 // 31, g * 255 // 31, b * 255 // 31, al * 255], -1)
    else:
        raise StageError("texture format %d" % fmt)
    return out.reshape(h, w, 4).astype(np.uint8)


def load(path, with_textures=True):
    with open(path, "rb") as fh:
        d = fh.read()
    if d[:4] != b"WPK2":
        raise StageError("%s is not a WPK2 map pack (it starts with %r)" % (path, d[:4]))
    n_tex, n_chunk, n_batch, csize, tex_off, chunk_off, batch_off = struct.unpack_from("<IIIfIII", d, 4)
    st = Stage()
    st.chunk_size = csize
    if with_textures:
        for i in range(n_tex):
            name, w, h, fmt, off, size = struct.unpack_from("<48sHHIII", d, tex_off + i * 64)
            nm = name.split(b"\0", 1)[0].decode("latin1")
            st.textures.append((nm, _tex_rgba(d[off:off + w * h * 2], w, h, fmt)))
    batches = []
    for i in range(n_batch):
        t, flags, voff, vcount, ioff, icount = struct.unpack_from("<HHIIII", d, batch_off + i * 20)
        b = Batch()
        b.tex = -1 if t == 0xFFFF else t
        b.blend, b.flip = flags & 3, flags >> 2
        b.V = np.frombuffer(d, VDT, vcount, voff)
        b.I = np.frombuffer(d, "<u2", icount, ioff)
        batches.append(b)
    for i in range(n_chunk):
        f = struct.unpack_from("<6fIII", d, chunk_off + i * 36)
        c = Chunk()
        c.lo, c.hi = np.array(f[0:3], np.float32), np.array(f[3:6], np.float32)
        c.group = f[8]
        c.batches = batches[f[6]:f[6] + f[7]]
        st.chunks.append(c)
    return st
