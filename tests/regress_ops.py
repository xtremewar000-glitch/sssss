"""Regression harness for the EXISTING WPK operations, on temp copies of real cars.

    python regress_ops.py <vpk_tool folder> <folder with real car .wpk> [out.json]

Runs open / export OBJ + FBX / replace from OBJ + FBX / add part / move tag / copy to kits / delete part / delete + replace texture /
save + .bak / the CLI, using the wpk_edit.py in <vpk_tool folder>, and prints one SHA-256 per result.  Run it once per version
(for example the previous milestone and the current one) and diff the two outputs: identical hashes = identical behaviour.
Also asserts the results make sense (triangle counts survive an export -> import round trip, a copied wing really arrives in the kit ...).
Source files are only read; everything written goes to a temp folder."""
import contextlib, hashlib, io, json, os, shutil, sys, tempfile

tool, cars = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, tool)
import numpy as np
from PIL import Image
import wpk_edit as E

CAR = "nis_BNR34"
out = {}
checks = []


def h(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def hb(b):
    return hashlib.sha256(b).hexdigest()[:16]


def check(name, ok):
    checks.append((name, bool(ok)))


def tree_hashes(d):
    return {os.path.relpath(os.path.join(r, f), d): h(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in sorted(fs)}


work = tempfile.mkdtemp()
for suf in ("", "_aeroA", "_aeroB", "_aeroC"):
    shutil.copy(os.path.join(cars, CAR + suf + ".wpk"), os.path.join(work, CAR + suf + ".wpk"))
src_hash = {s: h(os.path.join(cars, CAR + s + ".wpk")) for s in ("", "_aeroA", "_aeroB", "_aeroC")}
stock_path = os.path.join(work, CAR + ".wpk")

# 1 open: serialisation of the untouched pack
packs = {s: E.Pack.load(os.path.join(work, CAR + s + ".wpk")) for s in ("", "_aeroA", "_aeroB", "_aeroC")}
for s, p in packs.items():
    out["open_to_bytes" + s] = hb(p.to_bytes())
st = packs[""]
tris = {c.tag: c.counts()[2] for c in st.chunks}

# 2 export OBJ and FBX
d = os.path.join(work, "x_obj"); os.makedirs(d)
r = E.export_model(st, os.path.join(d, "car.obj"), None, 0.1, None)
out["export_obj_files"] = tree_hashes(d)
out["export_obj_result"] = {k: r[k] for k in ("objects", "triangles")}
d = os.path.join(work, "x_fbx"); os.makedirs(d)
E.export_model(st, os.path.join(d, "car.fbx"), None, 0.1, None)
out["export_fbx_files"] = {k: v for k, v in tree_hashes(d).items()}

# 3 replace with OBJ (round trip into a fresh copy of the same car) and with FBX
for kind in ("obj", "fbx"):
    p = E.Pack.load(stock_path)
    meshes = E.parse_obj(os.path.join(work, "x_" + kind, "car." + kind), 10.0)
    res = E.replace_parts(p, meshes, default_tag=None)
    dst = os.path.join(work, "rt_%s.wpk" % kind)
    shutil.copy(stock_path, dst)
    p.save(dst)
    out["replace_%s" % kind] = h(dst)
    out["replace_%s_tris" % kind] = {str(k): v[0] for k, v in sorted(res.items())}
    got = {c.tag: c.counts()[2] for c in E.Pack.load(dst).chunks}
    check("%s round trip keeps every drawn part's triangles" % kind, all(got[t] == tris[t] for t in res))
    check("%s round trip keeps the part list" % kind, sorted(got) == sorted(tris))

# 4 add part (copy of the bonnet), move tag, delete part, then save
p = E.Pack.load(stock_path)
free = next(t for t in range(0x127, 0x12A) if p.chunk(t) is None) if any(p.chunk(t) is None for t in range(0x127, 0x12A)) else None
if free is None:
    p.delete_chunk(0x129); free = 0x129
E.add_part(p, free, 0x126)
check("add part copies the source geometry", p.chunk(free).counts() == p.chunk(0x126).counts())
E.move_part(p, free, 0x129) if free != 0x129 else None
p.delete_chunk(0x128) if p.chunk(0x128) else None
dst = os.path.join(work, "edit.wpk"); shutil.copy(stock_path, dst)
sv = p.save(dst)
out["add_move_delete"] = h(dst)
check("save kept the original once as .bak", sv["bak"] and h(dst + ".bak") == src_hash[""])
p.save(dst)
check("a second save does not overwrite .bak", h(dst + ".bak") == src_hash[""])

# 5 copy to kits: stock wing A + bonnet A into Aero B and C (as the Copy to kits dialog does: copy_parts then save)
for k in ("_aeroB", "_aeroC"):
    kp = E.Pack.load(os.path.join(work, CAR + k + ".wpk"))
    notes = E.copy_parts(st, kp, [0x122, 0x126])
    dst = os.path.join(work, CAR + k + ".wpk")
    kp.save(dst)
    out["copy_to_kit" + k] = h(dst)
    out["copy_to_kit_notes" + k] = notes
    after = E.Pack.load(dst)
    check("copied wing arrives in %s with its geometry" % k, after.chunk(0x122).counts() == st.chunk(0x122).counts())
    check("%s .bak is the original kit" % k, h(dst + ".bak") == src_hash[k])
for k in ("", "_aeroA"):
    check("untouched file %s is still byte-identical to the source" % (k or "stock"), h(os.path.join(work, CAR + k + ".wpk")) == src_hash[k])

# 6 texture edits
p = E.Pack.load(stock_path)
pi = next(i for i, t in enumerate(p.tex) if t["name"].startswith("p1:"))
n, lost = p.delete_textures([pi])
out["deltex_result"] = (n, lost)
dst = os.path.join(work, "deltex.wpk"); shutil.copy(stock_path, dst); p.save(dst); out["deltex"] = h(dst)
p = E.Pack.load(stock_path)
i = p.add_texture(p.tex[0]["name"], Image.new("RGBA", (64, 64), (1, 2, 3, 255)))
dst = os.path.join(work, "addtex.wpk"); shutil.copy(stock_path, dst); p.save(dst); out["addtex"] = h(dst)
out["addtex_slot"] = i

# 7 CLI
for argv in (["info", stock_path], ["export", stock_path, os.path.join(work, "cli.obj"), "--tags", "0,0x100"]):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = E.cli(argv)
    out["cli_" + argv[0]] = (rc, hb(buf.getvalue().replace(work, "<w>").encode()))
out["cli_export_files"] = tree_hashes(os.path.join(work)) and {k: v for k, v in tree_hashes(work).items() if k.startswith("cli")}

bad = [n for n, ok in checks if not ok]
out["_checks"] = {"passed": len(checks) - len(bad), "failed": bad}
text = json.dumps(out, indent=1, sort_keys=True, default=str)
if len(sys.argv) > 3:
    with open(sys.argv[3], "w") as f:
        f.write(text)
print(text)
sys.exit(1 if bad else 0)
