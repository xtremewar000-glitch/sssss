"""reader for the arcade's .mdl (XMD container of NDWD models)"""
import gzip, struct, collections
import numpy as np

def read_xmd(path):
    d = gzip.open(path).read() if open(path,'rb').read(2)==b'\x1f\x8b' else open(path,'rb').read()
    if d[:4] != b'XMD\0':
        raise ValueError('not an XMD file')
    n, = struct.unpack_from('<I', d, 12)
    off = struct.unpack_from('<%dI' % n, d, 16)
    ids = struct.unpack_from('<%dI' % n, d, 16 + 8 * n)
    items = [d[off[i]:(off[i + 1] if i + 1 < n else len(d))] for i in range(n)]
    return items, ids

def parse_ndwd(b):
    magic, fsz, ver, nm, _, pcs, fbs, vbs, vabs, _, cx, cy, cz = struct.unpack_from('<4sIHHIIIIIIfff', b, 0)
    assert magic == b'NDWD', magic
    face0 = 0x30 + pcs
    vert0 = face0 + fbs
    vadd0 = vert0 + vbs
    name0 = vadd0 + vabs
    names = [x.decode('latin1') for x in b[name0:fsz].split(b'\0') if x]
    meshes = []
    for m in range(nm):
        r = struct.unpack_from('<8f4I', b, 0x30 + 0x30 * m)
        # r[0:4] sphere, r[4:8] sphere2, then (nameHash/flags, ?, polyCountAndFlags?, polyOffset)
        meshes.append(dict(sphere=r[:4], w=r[8:12]))
    return dict(b=b, nm=nm, face0=face0, vert0=vert0, vadd0=vadd0, name0=name0, names=names, meshes=meshes, pcs=pcs, fbs=fbs, vbs=vbs, vabs=vabs, fsz=fsz)


def decode_ndwd(b):
    """-> dict(names, meshes=[dict(name, polys=[dict(pos, nrm, uv, col, tris, vsf, uvf, hashes, matw)])])"""
    m = parse_ndwd(b)
    nm = m['nm']
    meshes = []
    for k in range(nm):
        r = struct.unpack_from('<12I', b, 0x30 + 0x30 * k)
        npoly, parent = r[10] >> 16, r[10] & 0xffff
        poff = r[11]
        polys = []
        for p in range(npoly):
            a = poff + 0x30 * p
            fo, vo, vao, vc, vsf, uvf, mo = struct.unpack_from('<IIIHBBI', b, a)
            fc, = struct.unpack_from('<H', b, a + 0x20)
            ff = b[a + 0x22]
            ncol = (uvf & 0xf)
            nuv = (uvf >> 4)
            nrm_f = vsf & 0xf
            stride = 12 + (8 if nrm_f else 0) + (4 if ncol == 2 else 0) + 4 * nuv
            fields = [('p', '<f4', 3)]
            if nrm_f:
                fields.append(('n', '<f2', 4))
            if ncol == 2:
                fields.append(('c', 'u1', 4))
            for u in range(nuv):
                fields.append(('uv%d' % u, '<f2', 2))
            dt = np.dtype(fields)
            assert dt.itemsize == stride
            V = np.frombuffer(b, dt, count=vc, offset=m['vert0'] + vo)
            idx = np.frombuffer(b, '<u2', count=fc, offset=m['face0'] + fo)
            tris = []
            cur = []
            def flush(cur):
                for i in range(len(cur) - 2):
                    t = (cur[i], cur[i + 1], cur[i + 2]) if i % 2 == 0 else (cur[i + 1], cur[i], cur[i + 2])
                    if t[0] != t[1] and t[1] != t[2] and t[0] != t[2]:
                        tris.append(t)
            for v in idx:
                if v == 0xffff:
                    flush(cur); cur = []
                else:
                    cur.append(int(v))
            flush(cur)
            # material: words from mo
            mw = struct.unpack_from('<24I', b, mo)
            hashes = [w for w in mw if (w >> 24) == 0x10 and (w & 0xff0000) == 0x180000]
            polys.append(dict(V=V, tris=np.array(tris, np.int64).reshape(-1, 3), vsf=vsf, uvf=uvf, ff=ff, hashes=hashes, matw=mw[:8], vc=vc, fc=fc))
        meshes.append(dict(name=m['names'][k] if k < len(m['names']) else '?', polys=polys, sphere=m['meshes'][k]['sphere']))
    return dict(names=m['names'], meshes=meshes)


# ---------------------------------------------------------------- textures (NTWD, DXT1 / DXT5 blocks)
import io
from PIL import Image


def _dds(w, h, fourcc, data):
    hdr = struct.pack('<4sI', b'DDS ', 124) + struct.pack('<IIIIII', 0x1007, h, w, len(data), 0, 1) + b'\0' * 44
    hdr += struct.pack('<II4sIIIII', 32, 4, fourcc, 0, 0, 0, 0, 0) + struct.pack('<IIIII', 0x1000, 0, 0, 0, 0)
    return hdr + data


def decode_dxt(w, h, fmt, data):
    """fmt 0x100 = DXT1, 0x2000100 = DXT5 (as the NTWD headers say) -> RGBA PIL image"""
    fourcc = b'DXT5' if (fmt >> 24) == 2 else b'DXT1'
    need = w * h // (2 if fourcc == b'DXT1' else 1)
    im = Image.open(io.BytesIO(_dds(w, h, fourcc, data[:need])))
    im.load()
    return im.convert('RGBA')


def read_tex(path):
    """.tex -> [dict(item, w, h, fmt, data, hash)] ; a GIDX group (several images, each with a hash) is expanded,
    mip levels are ignored (only the first image of each block is kept)"""
    d = gzip.open(path).read() if open(path, 'rb').read(2) == b'\x1f\x8b' else open(path, 'rb').read()
    n, = struct.unpack_from('<I', d, 12)
    off = struct.unpack_from('<%dI' % n, d, 16)
    ids = struct.unpack_from('<%dI' % n, d, 16 + 8 * n)
    out = []
    for i in range(n):
        o = off[i]
        e = off[i + 1] if i + 1 < n else len(d)
        b = d[o:e]
        if b[:4] != b'NTWD':
            continue
        base, hdr, fmt, wh = struct.unpack_from('<IIII', b, 0x18)
        w, h = wh & 0xffff, wh >> 16
        if w and fmt in (0x100, 0x2000100):
            out.append(dict(item=i, id=ids[i], w=w, h=h, fmt=fmt, data=b[hdr:hdr + base], hash=None))
        k = 0x50
        while k + 0x50 <= len(b) and b[k:k + 4] == b'GIDX':
            hsh = struct.unpack_from('<I', b, k + 8)[0]
            base2, hdr2, fmt2, wh2 = struct.unpack_from('<IIII', b, k + 0x18)
            dataoff = struct.unpack_from('<I', b, k + 0x30)[0]
            if base2 and (wh2 & 0xffff) and fmt2 in (0x100, 0x2000100):
                out.append(dict(item=i, id=hsh, w=wh2 & 0xffff, h=wh2 >> 16, fmt=fmt2,
                                data=b[dataoff + hdr2:dataoff + hdr2 + base2], hash=hsh))
            k += 0x50
    return out
