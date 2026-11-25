"""Check .wpk files with the game's own loader: builds a tiny program from the real wpk_open / range checks in your
Re3DX+ source (src/render.c) and runs it on every file you name. It checks what the game checks when it opens a pack
(header, tables, file ranges, 65536-vertex pools, chunk ranges, paint pairing); it cannot draw anything.

  python loader_check.py PATH/TO/Re3DXPlus-v10_7 FILE.wpk [FILE.wpk ...]
  (the first argument is the folder that holds src/render.c; needs a C compiler: cc / gcc / clang)
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

MAIN = r'''
int main(int argc, char **argv) {
  int bad = 0;
  for (int a = 1; a < argc; a++) {
    wpk_scene s;
    if (wpk_open(&s, argv[a])) { printf("REJECTED  %s\n", argv[a]); bad++; continue; }
    int ok = 1;
    for (uint32_t i = 0; i < s.n_chunk; i++) if (!wpk_chunk_valid(&s.chunks[i], s.n_batch)) ok = 0;
    printf("%s  %s  (textures %u, parts %u, draws %u, buffers %u, paints %u)\n", ok ? "accepted " : "CHUNKS BAD", argv[a],
           s.n_tex, s.n_chunk, s.n_batch, s.n_pools, s.n_paints);
    if (!ok) bad++;
  }
  return bad ? 1 : 0;
}
'''


def build(root, out_dir):
    src = (pathlib.Path(root) / "src" / "render.c").read_text()

    def function(name):
        m = re.search(r'(?m)^(?:static\s+)?(?:int|void)\s+' + name + r'\s*\([^;]*?\)\s*\{', src)
        if not m:
            raise SystemExit("render.c has no function " + name + " (a different version of the game?)")
        i, depth = m.end(), 1
        while depth:
            depth += (src[i] == '{') - (src[i] == '}')
            i += 1
        return src[m.start():i]
    a = src.index('typedef struct {\n  char magic[4];')
    b = src.index('} wpk_batch_rec;') + len('} wpk_batch_rec;')
    prefix = ('#define _GNU_SOURCE\n#include <assert.h>\n#include <stdint.h>\n#include <stdlib.h>\n#include <stdio.h>\n#include <string.h>\n'
              '#include <fcntl.h>\n#include <unistd.h>\n#include <sys/stat.h>\n#include "render.h"\n#define POOL_VERTS 65536u\n')
    helpers = ('static int is_pot(int v) { return v > 0 && (v & (v - 1)) == 0; }\nstatic void ao_open(wpk_scene *s) { (void)s; }\n'
               'static int gloss_open(wpk_scene *s) { (void)s; return 0; }\nstatic int road_texture(const char *n) { (void)n; return 0; }\n'
               'void wpk_free(wpk_scene *s) { (void)s; }\n')
    body = '\n'.join(function(x) for x in ('wpk_chunk_valid', 'wpk_range_valid', 'wpk_index_buffer_valid', 'read_at', 'wpk_open'))
    c = pathlib.Path(out_dir) / "loader.c"
    c.write_text(prefix + src[a:b] + helpers + body + MAIN)
    exe = pathlib.Path(out_dir) / "loader"
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise SystemExit("No C compiler found (cc / gcc / clang).")
    subprocess.run([cc, "-std=gnu11", "-w", "-I", str(pathlib.Path(root) / "src"), str(c), "-o", str(exe)], check=True)
    return str(exe)


def main(argv):
    if len(argv) < 2 or not (pathlib.Path(argv[0]) / "src" / "render.c").is_file():
        print(__doc__)
        return 2
    with tempfile.TemporaryDirectory() as d:
        exe = build(argv[0], d)
        files = [f for f in argv[1:] if os.path.isfile(f)]
        return subprocess.run([exe] + files).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
