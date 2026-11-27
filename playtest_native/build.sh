#!/bin/sh
# Builds the physics library for the VPK tool's Playtest from the Re3DXPlus repo (the arcade's race code, recompiled to C).
#   ./build.sh /path/to/Re3DXPlus            -> libvpkplay.so (Linux) / libvpkplay.dylib (macOS) next to this script
# Needs gcc or clang.  The first compile of phys_rc.c (18 MB of C) takes a few minutes.
set -e
RE3=${1:?usage: build.sh /path/to/Re3DXPlus}
HERE=$(cd "$(dirname "$0")" && pwd)
CC=${CC:-cc}
case "$(uname -s)" in Darwin) OUT=libvpkplay.dylib ;; MINGW*|MSYS*|CYGWIN*) OUT=vpkplay.dll ;; *) OUT=libvpkplay.so ;; esac
[ -f "$RE3/src/phys/phys_rc.c" ] || { echo "no src/phys/phys_rc.c in $RE3 (is that the Re3DXPlus folder?)"; exit 1; }
[ -f "$RE3/tools/recomp/guard_sections.py" ] && python3 "$RE3/tools/recomp/guard_sections.py" "$RE3/src/phys/phys_rc.c" || true
EXTRA=""
# Windows: link the compiler's runtime in, so the .dll needs no libgcc / libwinpthread DLLs beside it
case "$OUT" in vpkplay.dll) EXTRA="-static -static-libgcc" ;; esac
$CC -O2 -shared -fPIC -ffp-contract=off -fvisibility=hidden -std=gnu99 -w -DRC_FPT=double \
    -I"$RE3/src" -I"$RE3/src/phys" -I"$HERE" \
    "$RE3/src/car.c" "$RE3/src/phys/phys_rc.c" "$RE3/src/phys/rc_runtime.c" "$HERE/vpk_playtest.c" \
    $EXTRA -o "$HERE/$OUT" -lm
echo "built $HERE/$OUT"
