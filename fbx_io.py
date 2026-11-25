"""Minimal FBX reader / writer for the VPK tool (pure Python + numpy, no SDK needed).

  read_fbx(path)  -> dict(P, UV, NR, CL, groups, up_axis, unit_scale)   (same raw data parse_obj builds from an OBJ)
  write_fbx(path, meshes, textures=None)  meshes = [dict(name, material, pos Nx3, nrm Nx3, uv Nx2, tris Mx3)]

Reads binary FBX 7.x (what Unity / Blender / Maya write).  ASCII FBX is rejected with a clear message
(re-save it as binary, or export OBJ).  Writes binary FBX 7400.
"""
import struct
import zlib
import numpy as np

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"


class FbxError(Exception):
    pass


class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props=None, children=None):
        self.name, self.props, self.children = name, props or [], children or []

    def find(self, name):
        return [c for c in self.children if c.name == name]

    def first(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None


# ------------------------------------------------------------------------------------------------ reader
_ARR = {"f": (4, "<f4"), "d": (8, "<f8"), "l": (8, "<i8"), "i": (4, "<i4"), "b": (1, "u1")}


def _read_prop(d, p):
    t = chr(d[p]); p += 1
    if t == "Y": return struct.unpack_from("<h", d, p)[0], p + 2
    if t == "C": return d[p] != 0, p + 1
    if t == "I": return struct.unpack_from("<i", d, p)[0], p + 4
    if t == "F": return struct.unpack_from("<f", d, p)[0], p + 4
    if t == "D": return struct.unpack_from("<d", d, p)[0], p + 8
    if t == "L": return struct.unpack_from("<q", d, p)[0], p + 8
    if t in _ARR:
        n, enc, clen = struct.unpack_from("<III", d, p); p += 12
        size, dt = _ARR[t]
        raw = d[p:p + clen]
        if enc == 1:
            raw = zlib.decompress(raw)
        return np.frombuffer(raw, dt, count=n), p + clen
    if t in "SR":
        n = struct.unpack_from("<I", d, p)[0]; p += 4
        raw = bytes(d[p:p + n])
        return (raw.decode("utf-8", "replace") if t == "S" else raw), p + n
    raise FbxError("unknown property type %r" % t)


def _read_node(d, p, ver):
    if ver >= 7500:
        end, nprop, plen = struct.unpack_from("<QQQ", d, p); p += 24
    else:
        end, nprop, plen = struct.unpack_from("<III", d, p); p += 12
    nl = d[p]; p += 1
    if end == 0:
        return None, p
    name = bytes(d[p:p + nl]).decode("latin1"); p += nl
    props = []
    for _ in range(nprop):
        v, p = _read_prop(d, p)
        props.append(v)
    node = Node(name, props)
    hdr = 25 if ver >= 7500 else 13
    while p < end:
        if end - p <= hdr and not any(d[p:end]):          # null terminator record
            p = end
            break
        c, p = _read_node(d, p, ver)
        if c is None:
            break
        node.children.append(c)
    return node, end


def parse_fbx(path):
    d = open(path, "rb").read()
    if d[:7] == b"; FBX 7" or d[:2] == b"; ":
        raise FbxError("%s is an ASCII FBX. Re-save it as Binary FBX (or export OBJ)." % path)
    if d[:23] != MAGIC:
        raise FbxError("%s is not a binary FBX file." % path)
    ver = struct.unpack_from("<I", d, 23)[0]
    p, top = 27, Node("", [], [])
    while p < len(d) - 32:
        n, p = _read_node(d, p, ver)
        if n is None:
            break
        top.children.append(n)
    return top, ver


def _props70(node):
    out = {}
    if node is None:
        return out
    p70 = node.first("Properties70")
    if p70:
        for c in p70.children:
            if c.name == "P" and len(c.props) >= 5:
                out[c.props[0]] = c.props[4:] if len(c.props) > 5 else c.props[4]
    return out


def _matrix(model):
    """4x4 local transform (row-vector convention: v' = v @ M) from Lcl Translation/Rotation/Scaling."""
    pr = _props70(model)
    t = np.array(pr.get("Lcl Translation", [0, 0, 0]), float)
    r = np.radians(np.array(pr.get("Lcl Rotation", [0, 0, 0]), float))
    s = np.array(pr.get("Lcl Scaling", [1, 1, 1]), float)
    def rot(a, i):
        c, sn = np.cos(a), np.sin(a)
        m = np.eye(3)
        j, k = [(1, 2), (2, 0), (0, 1)][i]
        m[j, j], m[j, k], m[k, j], m[k, k] = c, -sn, sn, c
        return m
    R = rot(r[2], 2) @ rot(r[1], 1) @ rot(r[0], 0)          # FBX default order XYZ
    M = np.eye(4)
    M[:3, :3] = R * s[None, :]
    M[:3, 3] = t
    return M                                                    # column-vector convention (v' = M @ v)


def read_fbx(path, select=None):
    """select(path) -> bool, path = 'Root/Chassis/PartsBody/glass' (names of the model and its parents): read only those meshes"""
    top, ver = parse_fbx(path)
    objs, conns = top.first("Objects"), top.first("Connections")
    if objs is None:
        raise FbxError("no Objects section")
    byid = {}
    for n in objs.children:
        if n.props and isinstance(n.props[0], (int, np.integer)):
            byid[int(n.props[0])] = n
    parent_of = {}                      # child id -> list of parent ids
    for c in (conns.children if conns else []):
        if c.name == "C" and len(c.props) >= 3 and c.props[0] == "OO":
            parent_of.setdefault(int(c.props[1]), []).append(int(c.props[2]))
    gs = top.first("GlobalSettings")
    gp = _props70(gs)
    unit = float(gp.get("UnitScaleFactor", 1.0) or 1.0)
    up = int(gp.get("UpAxis", 1)); upsign = int(gp.get("UpAxisSign", 1))

    def world(mid):
        M = np.eye(4)
        seen = 0
        while mid in byid and byid[mid].name == "Model" and seen < 64:
            M = _matrix(byid[mid]) @ M
            ps = [q for q in parent_of.get(mid, []) if q in byid and byid[q].name == "Model"]
            mid = ps[0] if ps else None
            seen += 1
        return M

    mats = {}
    for i, n in byid.items():
        if n.name == "Material":
            mats[i] = (n.props[1].split("\x00")[0] if len(n.props) > 1 else "mat")
    P, UV, NR, CL, groups = [], [], [], [], {}
    pbase = 0
    for gid, g in byid.items():
        if g.name != "Geometry" or g.first("Vertices") is None:
            continue
        models = [q for q in parent_of.get(gid, []) if q in byid and byid[q].name == "Model"]
        mid = models[0] if models else None
        mname = byid[mid].props[1].split("\x00")[0] if mid else "mesh"
        M = world(mid) if mid else np.eye(4)
        if select is not None:
            names, q, seen = [], mid, 0
            while q in byid and byid[q].name == "Model" and seen < 64:
                names.append(byid[q].props[1].split("\x00")[0])
                ps = [x for x in parent_of.get(q, []) if x in byid and byid[x].name == "Model"]
                q, seen = (ps[0] if ps else None), seen + 1
            if not select("/".join(reversed(names))):
                continue
        V = np.asarray(g.first("Vertices").props[0], float).reshape(-1, 3)
        V = V @ M[:3, :3].T + M[:3, 3]
        pvi = np.asarray(g.first("PolygonVertexIndex").props[0], np.int64)
        ends = np.nonzero(pvi < 0)[0]
        idx = np.where(pvi < 0, ~pvi, pvi)                      # corner -> vertex
        starts = np.concatenate([[0], ends[:-1] + 1])
        ncorner = len(pvi)
        # normals / uvs / materials layers -> per-corner arrays
        def layer(kind, dkey, ikey):
            L = g.first(kind)
            if L is None:
                return None
            mapping = (L.first("MappingInformationType").props[0] if L.first("MappingInformationType") else "ByPolygonVertex")
            ref = (L.first("ReferenceInformationType").props[0] if L.first("ReferenceInformationType") else "Direct")
            dn = L.first(dkey)
            if dn is None:
                return None
            data = np.asarray(dn.props[0], float)
            return mapping, ref, data, (np.asarray(L.first(ikey).props[0], np.int64) if L.first(ikey) else None)
        poly_of = np.repeat(np.arange(len(ends)), ends - starts + 1)
        def per_corner(info, width):
            if info is None:
                return None
            mapping, ref, data, ind = info
            data = data.reshape(-1, width)
            if mapping == "ByPolygonVertex":
                sel = np.arange(ncorner)
            elif mapping == "ByVertice" or mapping == "ByVertex":
                sel = idx
            elif mapping == "ByPolygon":
                sel = poly_of
            else:                                                # AllSame
                sel = np.zeros(ncorner, np.int64)
            if ref in ("IndexToDirect", "Index") and ind is not None:
                sel = ind[sel]
            return data[sel]
        nrm = per_corner(layer("LayerElementNormal", "Normals", "NormalsIndex"), 3)
        uv = per_corner(layer("LayerElementUV", "UV", "UVIndex"), 2)
        col = per_corner(layer("LayerElementColor", "Colors", "ColorIndex"), 4)
        ml = g.first("LayerElementMaterial")
        pmat = np.zeros(len(ends), np.int64)
        if ml is not None and ml.first("Materials") is not None:
            mm = np.asarray(ml.first("Materials").props[0], np.int64)
            mt = ml.first("MappingInformationType").props[0] if ml.first("MappingInformationType") else "AllSame"
            pmat = mm[np.arange(len(ends))] if (mt == "ByPolygon" and len(mm) >= len(ends)) else np.full(len(ends), mm[0] if len(mm) else 0)
        slot_names = []
        if mid:
            slot_names = [mats[q] for q in sorted([c for c, ps in parent_of.items() if mid in ps and c in mats])]
        # corner arrays -> global lists (one entry per corner, so references are 1:1)
        base = len(P)
        P.extend(V.tolist())
        cb = len(UV)
        uvi = nri = None
        if uv is not None:
            u_, uvi = np.unique(np.round(uv, 5), axis=0, return_inverse=True)
            uvi = uvi.reshape(-1)
            UV.extend(u_.tolist())
        nb = len(NR)
        if nrm is not None:
            n_, nri = np.unique(np.round(nrm, 4), axis=0, return_inverse=True)
            nri = nri.reshape(-1)
            NR.extend(n_.tolist())
        CL.extend([None] * len(V))
        if col is not None:                                      # colour per corner -> vertex (last wins)
            for k in range(ncorner):
                CL[base + idx[k]] = col[k][:3].tolist()
        for pi in range(len(ends)):
            s, e = starts[pi], ends[pi] + 1
            corners = list(range(s, e))
            refs = [(base + int(idx[k]), (cb + int(uvi[k])) if uv is not None else -1, (nb + int(nri[k])) if nrm is not None else -1) for k in corners]
            mname_ = slot_names[pmat[pi]] if 0 <= pmat[pi] < len(slot_names) else "none"
            grp = groups.setdefault((mname, mname_), [])
            for j in range(1, len(refs) - 1):
                grp.append((refs[0], refs[j], refs[j + 1]))
    if not P or not groups:
        raise FbxError("%s has no mesh geometry." % path)
    return dict(P=P, UV=UV, NR=NR, CL=CL, groups=groups, up_axis=up * upsign, unit_scale=unit, version=ver)


# ------------------------------------------------------------------------------------------------ writer
def _prop(v):
    if isinstance(v, bool):
        return b"C" + struct.pack("<B", int(v))
    if isinstance(v, (int, np.integer)):
        return (b"I" + struct.pack("<i", int(v))) if -2**31 <= int(v) < 2**31 else (b"L" + struct.pack("<q", int(v)))
    if isinstance(v, (float, np.floating)):
        return b"D" + struct.pack("<d", float(v))
    if isinstance(v, str):
        b = v.encode("utf-8")
        return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, np.ndarray):
        kind = {"f8": b"d", "f4": b"f", "i4": b"i", "i8": b"l"}[v.dtype.str[1:]]
        raw = zlib.compress(np.ascontiguousarray(v).tobytes(), 1)
        return kind + struct.pack("<III", v.size, 1, len(raw)) + raw
    raise FbxError("cannot write property %r" % (v,))


def _node_bytes(n, offset):
    pb = b"".join(_prop(p) for p in n.props)
    name = n.name.encode("latin1")
    head = 13 + len(name)
    body = b""
    pos = offset + head + len(pb)
    for c in n.children:
        cb = _node_bytes(c, pos + len(body))
        body += cb
    if n.children:
        body += b"\x00" * 13
    end = offset + head + len(pb) + len(body)
    return struct.pack("<III", end, len(n.props), len(pb)) + struct.pack("<B", len(name)) + name + pb + body


def _P(name, typ, label, flags, *val):
    return Node("P", [name, typ, label, flags, *val])


def write_fbx(path, meshes, textures=None, unit_cm=100.0, creator="VPK tool"):
    """meshes: [dict(name, material, pos (N,3) metres, nrm (N,3) or None, uv (N,2) or None, tris (M,3) ints)]
    textures: {material name: relative PNG path} (optional).  +Y up, +Z front, units in metres."""
    textures = textures or {}
    nid = [1000]

    def new():
        nid[0] += 1
        return nid[0]
    objs, conns = [], []
    mat_ids, tex_done = {}, {}
    for m in meshes:
        mn = m["material"]
        if mn not in mat_ids:
            mid = mat_ids[mn] = new()
            mat = Node("Material", [mid, mn + "\x00\x01Material", ""], [
                Node("Version", [102]), Node("ShadingModel", ["phong"]), Node("MultiLayer", [0]),
                Node("Properties70", [], [_P("DiffuseColor", "Color", "", "A", 1.0, 1.0, 1.0)])])
            objs.append(mat)
            if mn in textures:
                tid, vid = new(), new()
                fn = textures[mn]
                objs.append(Node("Texture", [tid, mn + "\x00\x01Texture", ""], [
                    Node("Type", ["TextureVideoClip"]), Node("Version", [202]), Node("TextureName", [mn + "\x00\x01Texture"]),
                    Node("FileName", [fn]), Node("RelativeFilename", [fn])]))
                conns.append(("OP", tid, mid, "DiffuseColor"))
    for m in meshes:
        pos = np.asarray(m["pos"], np.float64).reshape(-1, 3)
        T = np.asarray(m["tris"], np.int64).reshape(-1, 3)
        nc = len(T) * 3
        pvi = T.copy().reshape(-1)
        pvi[2::3] = ~pvi[2::3]
        gid, mid_ = new(), new()
        kids = [Node("GeometryVersion", [124]), Node("Vertices", [pos.reshape(-1)]),
                Node("PolygonVertexIndex", [pvi.astype(np.int32)])]
        if m.get("nrm") is not None:
            n_ = np.asarray(m["nrm"], np.float64).reshape(-1, 3)[T.reshape(-1)]
            kids.append(Node("LayerElementNormal", [0], [Node("Version", [102]), Node("Name", [""]),
                                                         Node("MappingInformationType", ["ByPolygonVertex"]),
                                                         Node("ReferenceInformationType", ["Direct"]),
                                                         Node("Normals", [n_.reshape(-1)])]))
        if m.get("uv") is not None:
            uvv = np.asarray(m["uv"], np.float64).reshape(-1, 2)
            kids.append(Node("LayerElementUV", [0], [Node("Version", [101]), Node("Name", ["UVMap"]),
                                                     Node("MappingInformationType", ["ByPolygonVertex"]),
                                                     Node("ReferenceInformationType", ["IndexToDirect"]),
                                                     Node("UV", [uvv.reshape(-1)]),
                                                     Node("UVIndex", [T.reshape(-1).astype(np.int32)])]))
        kids.append(Node("LayerElementMaterial", [0], [Node("Version", [101]), Node("Name", [""]),
                                                       Node("MappingInformationType", ["AllSame"]),
                                                       Node("ReferenceInformationType", ["IndexToDirect"]),
                                                       Node("Materials", [np.array([0], np.int32)])]))
        lay = [Node("Version", [100])]
        for t_, present in (("LayerElementNormal", m.get("nrm") is not None), ("LayerElementUV", m.get("uv") is not None), ("LayerElementMaterial", True)):
            if present:
                lay.append(Node("LayerElement", [], [Node("Type", [t_]), Node("TypedIndex", [0])]))
        kids.append(Node("Layer", [0], lay))
        objs.append(Node("Geometry", [gid, m["name"] + "\x00\x01Geometry", "Mesh"], kids))
        objs.append(Node("Model", [mid_, m["name"] + "\x00\x01Model", "Mesh"], [
            Node("Version", [232]), Node("Properties70", [], [_P("DefaultAttributeIndex", "int", "Integer", "", 0)]),
            Node("Culling", ["CullingOff"])]))
        conns += [("OO", mid_, 0), ("OO", gid, mid_), ("OO", mat_ids[m["material"]], mid_)]
    cn = []
    for c in conns:
        cn.append(Node("C", list(c)))
    top = [
        Node("FBXHeaderExtension", [], [Node("FBXHeaderVersion", [1003]), Node("FBXVersion", [7400]),
                                        Node("Creator", [creator])]),
        Node("GlobalSettings", [], [Node("Version", [1000]), Node("Properties70", [], [
            _P("UpAxis", "int", "Integer", "", 1), _P("UpAxisSign", "int", "Integer", "", 1),
            _P("FrontAxis", "int", "Integer", "", 2), _P("FrontAxisSign", "int", "Integer", "", 1),
            _P("CoordAxis", "int", "Integer", "", 0), _P("CoordAxisSign", "int", "Integer", "", 1),
            _P("UnitScaleFactor", "double", "Number", "", float(unit_cm))])]),
        Node("Definitions", [], [Node("Version", [100]), Node("Count", [len(objs) + 1])]),
        Node("Objects", [], objs),
        Node("Connections", [], cn),
    ]
    out = bytearray(MAGIC + struct.pack("<I", 7400))
    for n in top:
        out += _node_bytes(n, len(out))
    out += b"\x00" * 13
    out += bytes([0xfa, 0xbc, 0xab, 0x09, 0xd0, 0xc8, 0xd4, 0x66, 0xb1, 0x76, 0xfb, 0x83, 0x1c, 0xf7, 0x26, 0x7e])
    out += b"\x00" * 4
    out += b"\x00" * ((16 - len(out) % 16) % 16 or 16)
    out += struct.pack("<I", 7400) + b"\x00" * 120
    out += bytes([0xf8, 0x5a, 0x8c, 0x6a, 0xde, 0xf5, 0xd9, 0x7e, 0xec, 0xe9, 0x0c, 0xe3, 0x75, 0x8f, 0x29, 0x0b])
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return len(meshes)


def mesh_paths(path):
    """full paths ('Root/OffsetChassis/Chassis/...') of every mesh in the file, without reading the geometry"""
    out = []
    try:
        read_fbx(path, select=lambda p: (out.append(p), False)[1])
    except FbxError:
        pass
    return out
