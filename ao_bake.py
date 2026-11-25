"""Bake a car pack's ambient occlusion with the game's own tool (tools/stage/bake_ao.c of your Re3DX+ source).

The game darkens a car's body texture with a map in the body texture's UV layout, <pack>.ao beside <pack>.wpk. After the body
has been swapped the old map no longer fits it, so bake a new one:

  python ao_bake.py CAR.wpk [size 256] [rays 96]                      (uses the baker that ships with the tool)
  python ao_bake.py PATH/TO/Re3DXPlus-v10_7 CAR.wpk [size 256] [rays 96]   (uses the game's own bake_ao.c)

The tool carries its own copy of bake_ao.c and, for Windows, a ready-built bake_ao_win.exe: no game source and no compiler needed there.
Elsewhere (Mac / Linux) the bundled bake_ao.c is compiled once with the system's C compiler.

It needs a C compiler (cc / gcc / clang / cl; on macOS the Command Line Tools: xcode-select --install; on Windows MinGW-w64 or the Visual Studio Build Tools) or, on Windows, WSL with gcc, which it
uses by itself when the native compile fails. The baker is compiled once and kept in ~/.vpk_tool/ao.
An existing CAR.ao is kept once as CAR.ao.bak. A bake takes a few seconds."""
import os
import shutil
import subprocess
import sys


IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"


class AoError(Exception):
    pass


def _here():
    """the folder the tool's own files are in (inside the .exe: PyInstaller's unpack folder)"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def source(root=None):
    """bake_ao.c: the game's own copy if a Re3DX+ folder is set (it may be newer), else the copy that ships with the tool"""
    for p in (os.path.join(root or "", "tools", "stage", "bake_ao.c") if root else None, os.path.join(_here(), "bake_ao.c")):
        if p and os.path.isfile(p):
            return p
    return None


def prebuilt():
    """a ready-made baker that ships with the tool (Windows), so nobody needs a compiler"""
    p = os.path.join(_here(), "bake_ao_win.exe")
    return p if IS_WINDOWS and os.path.isfile(p) else None


MAC_HELP = ("macOS has no C compiler until Apple's Command Line Tools are installed. Open Terminal and run:\n"
            "  xcode-select --install\n"
            "click Install, wait for it to finish, then press Bake AO again.")


def mac_tools_ok():
    """True when the Command Line Tools are there (macOS has a /usr/bin/cc stub that only pops up an installer)"""
    try:
        r = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True)
        return r.returncode == 0 and os.path.isdir(r.stdout.strip())
    except OSError:
        return False


def compiler():
    names = ["cc", "gcc", "clang"] + (["cl"] if IS_WINDOWS else [])
    for c in names:
        p = shutil.which(c)
        if p:
            return p
    return None


def commands(cc, exe, src, windows=None):
    """compile command lines to try, best first. The maths functions need '-lm' on Linux / Mac / MinGW, but with Visual Studio's clang or
    cl they are in the C runtime and '-lm' fails ('could not open m.lib'), so Windows tries without it first."""
    windows = IS_WINDOWS if windows is None else windows
    base = os.path.splitext(os.path.basename(cc).lower())[0]
    if base == "cl":                                            # Microsoft cl.exe (not clang)
        return [[cc, "/nologo", "/O2", "/w", "/D_CRT_SECURE_NO_WARNINGS", "/Fe:" + exe, src]]
    plain = [cc, "-O2", "-w", "-D_CRT_SECURE_NO_WARNINGS", "-o", exe, src]
    withm = plain + ["-lm"]
    return [plain, withm] if windows else [withm, plain]


def wsl():
    """wsl.exe on Windows with WSL installed, else None"""
    return shutil.which("wsl") if IS_WINDOWS else None


def _wslpath(p):
    r = subprocess.run(["wsl", "wslpath", "-a", os.path.abspath(p)], capture_output=True, text=True)
    out = r.stdout.strip()
    if r.returncode or not out:
        raise AoError("WSL could not translate the path %s:\n%s" % (p, (r.stderr or r.stdout)[-300:]))
    return out


def _cache():
    d = os.path.join(os.path.expanduser("~"), ".vpk_tool", "ao")
    os.makedirs(d, exist_ok=True)
    return d


def _build_native(src):
    exe = os.path.join(_cache(), "bake_ao" + (".exe" if IS_WINDOWS else ""))
    if os.path.isfile(exe) and os.path.getmtime(exe) >= os.path.getmtime(src):
        return exe
    if IS_MAC and not mac_tools_ok():
        raise AoError(MAC_HELP)
    cc = compiler()
    if not cc:
        raise AoError("No C compiler found (cc / gcc / clang / cl).")
    last = ""
    for cmd in commands(cc, exe, src):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0 and os.path.isfile(exe):
            return exe
        last = (r.stderr or r.stdout)
    if IS_MAC and "xcode-select" in last:
        raise AoError(MAC_HELP)
    errs = [l for l in last.splitlines() if "error" in l.lower()] or last.splitlines()
    raise AoError("Compiling bake_ao.c failed:\n" + "\n".join(errs[-6:]))


def _build_wsl(src):
    """compile inside WSL (Ubuntu): needs gcc there:  sudo apt update && sudo apt install -y gcc"""
    exe = os.path.join(_cache(), "bake_ao_wsl")
    if os.path.isfile(exe) and os.path.getmtime(exe) >= os.path.getmtime(src):
        return exe
    r = subprocess.run(["wsl", "gcc", "-O2", "-w", "-o", _wslpath(exe), _wslpath(src), "-lm"], capture_output=True, text=True)
    if r.returncode or not os.path.isfile(exe):
        raise AoError("Compiling in WSL failed (is gcc installed there? In Ubuntu run: sudo apt update && sudo apt install -y gcc):\n"
                      + (r.stderr or r.stdout)[-400:])
    subprocess.run(["wsl", "chmod", "+x", _wslpath(exe)], capture_output=True, text=True)
    return exe


def build(root):
    """-> (kind, exe): a program built here ('native'), or one built and run inside WSL ('wsl')"""
    if not (root and source(root) and os.path.join(root, "tools") in source(root)):   # no game folder: the tool's own baker
        pre = prebuilt()
        if pre:
            return "native", pre
    src = source(root)
    if not src:
        raise AoError("bake_ao.c is missing next to the tool.")
    try:
        return "native", _build_native(src)
    except AoError as native:
        if wsl():
            try:
                return "wsl", _build_wsl(src)
            except AoError as ex:
                raise AoError("%s\n\nWSL: %s" % (native, ex))
        if IS_MAC:
            raise native
        raise AoError("%s\n\nInstall a C compiler (MinGW-w64 or the Visual Studio Build Tools), or WSL with gcc, or run the game's tool by hand:\n"
                      "  bake_ao CAR.wpk CAR.ao 256 96" % native)


def bake(root, wpk, size=256, rays=96):
    """-> (path of the .ao, the baker's report)"""
    if not os.path.isfile(wpk):
        raise AoError("%s does not exist." % wpk)
    kind, exe = build(root)
    out = os.path.splitext(wpk)[0] + ".ao"
    tmp = out + ".new"
    if kind == "wsl":
        cmd = ["wsl", _wslpath(exe), _wslpath(wpk), _wslpath(tmp), str(size), str(rays)]
    else:
        cmd = [exe, wpk, tmp, str(size), str(rays)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or not os.path.isfile(tmp):
        raise AoError("The baker failed:\n" + (r.stderr or r.stdout)[-600:])
    if os.path.isfile(out) and not os.path.exists(out + ".bak"):
        shutil.copy2(out, out + ".bak")
    os.replace(tmp, out)
    return out, (r.stdout + r.stderr).strip()


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0].lower().endswith(".wpk"):
        a = [""] + a                                   # no game folder given
    if len(a) < 2:
        print(__doc__)
        sys.exit(2)
    try:
        o, rep = bake(a[0], a[1], int(a[2]) if len(a) > 2 else 256, int(a[3]) if len(a) > 3 else 96)
        print(rep)
        print("wrote", o)
    except AoError as ex:
        print("error:", ex)
        sys.exit(1)
