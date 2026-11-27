"""GPU viewport for the Models (WPK) tab: the car drawn with OpenGL (moderngl) inside the Tk window (pyopengltk).

Orbit, pan and zoom redraw on the GPU every frame, at the window's full resolution, 4x multisampled.  Picking and the
selection outline use a per-pixel "which part is this" buffer drawn on the GPU as well.

What it draws is what the game draws.  The shading is a port of the game's own programs (Re3DXPlus src/render.c, VS_LIT / FS_LIT)
and lights (src/app.c, CAR_LIGHT / MENU_LIGHT):
    prog 0  the arcade's fixed-function lighting (tyres, underbody, lamps' bodies ...)
    prog 1  car.cg       the paint: sphere-mapped environment, scaled by its own brightness, plus a specular of power 256
    prog 2  car_blend.cg glass, lamps, wheels: the pack's specular map mixes the reflected environment into the texture
    the baked ambient occlusion (<pack>.ao beside the .wpk, "Bake AO") darkens the texture
    passes: opaque, alpha-tested (alpha < 0.5 discarded), then blended (alpha or additive, no depth write); both sides of a
    triangle are drawn (the cars' models mix both windings, as the game's cars do)
Left out, because they need the race's frame or the overhead map and are not there in a viewer: the paint's reflection of the
last race frame, the overhead reflection, fog.

Layout of this file
    shaders and light presets        (text and numbers)
    camera maths                     (numpy only)
    Renderer / GPUModel              (moderngl only: runs on any GL 3.3 context, e.g. a headless one for tests)
    GLViewport                       (the Tk side: pyopengltk frame, redraw requests, picking, failure -> software fallback)

    python gl_viewport.py info               what GL the machine gives, and whether the shaders compile
    python gl_viewport.py shot CAR.wpk OUT.png [yaw pitch [shading]]    draw one picture without a window
"""
import math
import os
import struct
import sys
import time
import traceback

import numpy as np

try:
    import moderngl
    _MGL_ERROR = None
except Exception as _ex:                      # not installed, or no usable OpenGL library
    moderngl = None
    _MGL_ERROR = _ex

WPK_SHADOW = 0xFFFF
FOVY_DEG = 30.0                               # the viewport's vertical field of view (perspective)
SAMPLES = 4                                   # multisampling of the picture
SEL_FILL = (1.0, 140 / 255.0, 0.0, 70 / 255.0)
SEL_EDGE = (1.0, 200 / 255.0, 0.0)


# ------------------------------------------------------------------------------------------------
# lights: the game's (app.c).  dir = the direction the light TRAVELS; "view" = fixed to the camera (the menus: the car
# turns under fixed lights, so orbiting the camera with the lights on it is the same picture), "model" = fixed to the car
# ------------------------------------------------------------------------------------------------
class Light:
    def __init__(self, dirs, diffuse, ambient, mat_diffuse, mat_ambient, frame, note=""):
        self.dirs, self.diffuse, self.ambient = dirs, diffuse, ambient
        self.mat_diffuse, self.mat_ambient, self.frame, self.note = mat_diffuse, mat_ambient, frame, note


SHADINGS = {
    # app.c MENU_LIGHT (clMenu3dEnvironment: data/menu/menu_4.lua's first two lights)
    "Game: car select": Light(((1.0, -0.8, -0.9), (-1.0, -0.7, -0.8)), (0.85, 0.8), 0.1, 0.8, 0.2, "view"),
    # app.c CAR_LIGHT[tokyo][day / night], [hakone][day / night] (data/car_light.lua)
    "Game: Tokyo, day": Light(((-0.5, -1.0, -0.3), (0.0, -1.0, 0.0)), (0.62, 0.52), 0.35, 1.0, 0.9, "model"),
    "Game: Tokyo, night": Light(((0.5, -0.15, -0.3), (0.0, -1.0, -0.2)), (0.73, 0.55), 0.32, 1.0, 0.9, "model"),
    "Game: Hakone, day": Light(((0.5, -1.0, -0.3), (0.0, -1.0, 0.0)), (0.62, 0.45), 0.31, 1.0, 0.9, "model"),
    "Game: Hakone, night": Light(((0.5, -1.0, -0.3), (0.0, -1.0, 0.0)), (0.79, 0.66), 0.14, 1.0, 0.9, "model"),
    # not the game's: a light at the camera, to look at a part from any side
    "Headlight (follows the camera)": Light(((0.35, -0.45, -1.0), (-0.6, -0.25, -1.0)), (0.85, 0.45), 0.3, 1.0, 0.9, "view"),
    "Flat (no lighting, no reflections)": None,
}
DEFAULT_SHADING = "Game: car select"


# ------------------------------------------------------------------------------------------------
# shaders (GLSL 330 core).  The three car programs are the game's (render.c VS_LIT / FS_LIT), same arithmetic.
# ------------------------------------------------------------------------------------------------
_COMMON = """
vec3 nrm(vec3 v) { return v * inversesqrt(max(dot(v, v), 1e-12)); }
"""

VS_CAR = """#version 330 core
// PROG 0 fixed function, 1 car.cg, 2 car_blend.cg   (render.c VS_LIT)
in vec3 aPos; in vec2 aUV; in vec4 aColor; in vec3 aNormalRaw;       // the normal as raw int16
uniform mat4 uMVP, uMV;
uniform vec3 uEye, uL0, uL1, uD0, uD1, uAmb;
uniform float uPower, uFlat;
out vec2 vUV; out vec4 vColor; out vec2 vEnv; out vec3 vHalf; out vec3 vNormal;
""" + _COMMON + """
void main() {
  vec3 aNormal = max(aNormalRaw / 32767.0, -1.0);         // GL's signed-normalised conversion
  gl_Position = uMVP * vec4(aPos, 1.0);
  vUV = aUV;
  vec3 d = clamp((max(dot(aNormal, uL0), 0.0) * uD0 + uAmb + max(dot(aNormal, uL1), 0.0) * uD1) * aColor.rgb, 0.0, 1.0);
  d = mix(d, aColor.rgb, uFlat);
  vEnv = vec2(0.0); vHalf = vec3(0.0, 0.0, 1.0); vNormal = vec3(0.0, 0.0, 1.0);
#if PROG == 0
  vColor = vec4(d, aColor.a);
#elif PROG == 1
  // the environment's weight 1 - uniformEnvMapPower rides in the colour's alpha, the sphere map's v takes the upper half
  vColor = vec4(d, 1.0 - uPower);
  vHalf = nrm(nrm(uEye - aPos) + uL0); vNormal = aNormal;
  vec2 n = (uMV * vec4(aNormal, 0.0)).xy * 0.5 + 0.5;
  vEnv = vec2(n.x, n.y * 0.5 + 0.5);
#else
  // car_blend.cg: the view vector reflected about the normal in view space, the upper 40 %
  vColor = vec4(d, aColor.a);
  vec3 p = (uMV * vec4(aPos, 1.0)).xyz;
  vec3 e = p * inversesqrt(dot(p, p) + 1.0);
  vec3 n = nrm((uMV * vec4(aNormal, 0.0)).xyz);
  vec2 r = e.xy - 2.0 * dot(e, n) * n.xy;
  vEnv = vec2(r.x * 0.5 + 0.5, (r.y * 0.5 + 0.5) * 0.4 + 0.6);
#endif
}
"""

FS_CAR = """#version 330 core
// render.c FS_LIT (no overhead map, no race frame: uOver would be 0 in a viewer)
uniform sampler2D uTex, uEnv, uSpec, uAO;
uniform float uRefl;
in vec2 vUV; in vec4 vColor; in vec2 vEnv; in vec3 vHalf; in vec3 vNormal;
out vec4 oColor;
""" + _COMMON + """
void main() {
  vec4 t = texture(uTex, vUV);
  float ao = texture(uAO, vUV).r;
#if PROG == 0
  vec4 c = t * vColor;
  c.rgb *= ao;
#elif PROG == 1
  // the environment scaled by its own brightness, plus a specular power of 256
  vec3 e = texture(uEnv, vEnv).rgb * vColor.rgb;
  e *= dot(e, e);
  float s = pow(clamp(dot(nrm(vNormal), nrm(vHalf)), 0.0, 1.0), 256.0);
  vec4 c = vec4((t.rgb * vColor.rgb + uRefl * (vColor.a * e + s)) * ao, 1.0);
#else
  vec3 env = texture(uEnv, vEnv).rgb;
  float k = min(texture(uSpec, vUV).r, 1.0) * uRefl;
  vec4 c = vec4(mix(t.rgb, env, k) * ao, t.a) * vColor;
#endif
#if CUTOUT
  if (c.a < 0.5) discard;
#endif
  oColor = c;
}
"""

VS_ID = """#version 330 core
in vec3 aPos; in vec2 aUV; in vec4 aColor;
uniform mat4 uMVP;
out vec2 vUV; out float vA;
void main() { gl_Position = uMVP * vec4(aPos, 1.0); vUV = aUV; vA = aColor.a; }
"""

FS_ID = """#version 330 core
uniform sampler2D uTex;
uniform int uId;
in vec2 vUV; in float vA;
out vec4 oColor;
void main() {
#if CUTOUT
  if (texture(uTex, vUV).a * vA < 0.5) discard;
#endif
  oColor = vec4(float(uId & 255) / 255.0, float((uId >> 8) & 255) / 255.0, 0.0, 1.0);
}
"""

VS_FULL = """#version 330 core
out vec2 vUV;
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));      // one triangle over the screen
  vUV = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.9999, 1.0);
}
"""

FS_BG = """#version 330 core
uniform vec3 uTop, uBottom;
in vec2 vUV; out vec4 oColor;
void main() { oColor = vec4(mix(uBottom, uTop, clamp(vUV.y, 0.0, 1.0)), 1.0); }
"""

FS_COMPOSITE = """#version 330 core
// the resolved picture, with the selected parts tinted and outlined from the part-id buffer
uniform sampler2D uColor, uIds, uSel;
uniform vec2 uPx;
uniform int uHasSel;
uniform vec4 uFill; uniform vec3 uEdge;
in vec2 vUV; out vec4 oColor;
int idAt(vec2 uv) {
  vec4 q = texture(uIds, uv);
  return int(q.r * 255.0 + 0.5) + 256 * int(q.g * 255.0 + 0.5);
}
bool selAt(vec2 uv) {
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) return false;
  int id = idAt(uv);
  return id > 0 && texelFetch(uSel, ivec2(id & 1023, id >> 10), 0).r > 0.5;
}
void main() {
  vec3 col = texture(uColor, vUV).rgb;
  if (uHasSel != 0 && selAt(vUV)) {
    col = mix(col, uFill.rgb, uFill.a);
    bool edge = false;       // a 2 px outline: a neighbour within 2 steps (up, down, left, right, diagonal) is not selected
    for (int i = 0; i < 12 && !edge; ++i) {
      ivec2 o = ivec2(0);
      if (i == 0) o = ivec2(1, 0); else if (i == 1) o = ivec2(-1, 0); else if (i == 2) o = ivec2(0, 1); else if (i == 3) o = ivec2(0, -1);
      else if (i == 4) o = ivec2(1, 1); else if (i == 5) o = ivec2(-1, 1); else if (i == 6) o = ivec2(1, -1); else if (i == 7) o = ivec2(-1, -1);
      else if (i == 8) o = ivec2(2, 0); else if (i == 9) o = ivec2(-2, 0); else if (i == 10) o = ivec2(0, 2); else o = ivec2(0, -2);
      if (!selAt(vUV + vec2(o) * uPx)) edge = true;
    }
    if (edge) col = uEdge;
  }
  oColor = vec4(col, 1.0);
}
"""



VS_FLAME = """#version 330 core
in vec3 aPos; in vec2 aUV; in vec4 aColor;
uniform mat4 uMVP;
out vec2 vUV; out vec4 vColor;
void main() { gl_Position = uMVP * vec4(aPos, 1.0); vUV = aUV; vColor = aColor; }
"""

FS_FLAME = """#version 330 core
uniform sampler2D uTex;
uniform float uGain;
in vec2 vUV; in vec4 vColor; out vec4 oColor;
void main() { vec4 c = texture(uTex, vUV) * vColor; c.rgb *= uGain; oColor = c; }
"""

VS_FLAT = """#version 330 core
in vec3 aPos; in vec3 aCol;
uniform mat4 uMVP;
out vec3 vCol;
void main() { gl_Position = uMVP * vec4(aPos, 1.0); vCol = aCol; }
"""

FS_FLAT = """#version 330 core
in vec3 vCol; out vec4 oColor;
void main() { oColor = vec4(vCol, 1.0); }
"""

VS_TRI = """#version 330 core
in vec3 aPos; in vec4 aCol;
uniform mat4 uMVP; uniform float uBias;
out vec4 vCol;
void main() { vec4 p = uMVP * vec4(aPos, 1.0); p.z -= uBias * p.w; gl_Position = p; vCol = aCol; }
"""

FS_TRI = """#version 330 core
in vec4 vCol; out vec4 oColor;
void main() { oColor = vCol; }
"""

# ------------------------------------------------------------------------------------------------
# camera maths (numpy).  Same turn as wpk_view.view_point: q = Rx(pitch) Ry(yaw) p, x right, y up, z towards the viewer.
# ------------------------------------------------------------------------------------------------
def rotation(yaw, pitch):
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    return np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], np.float64) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], np.float64)


def perspective(fovy, aspect, near, far):
    t = 1.0 / math.tan(fovy / 2.0)
    return np.array([[t / aspect, 0, 0, 0], [0, t, 0, 0],
                     [0, 0, (far + near) / (near - far), 2.0 * far * near / (near - far)], [0, 0, -1, 0]], np.float64)


def orthographic(half_w, half_h, near, far):
    return np.array([[1.0 / half_w, 0, 0, 0], [0, 1.0 / half_h, 0, 0],
                     [0, 0, -2.0 / (far - near), -(far + near) / (far - near)], [0, 0, 0, 1]], np.float64)


def bounds_of(chunks):
    """(centre, radius) of the bounding sphere of every part (hidden ones too, no shadow): the framing stays put while parts
    are shown and hidden, as it always did"""
    lo = hi = None
    for c in chunks:
        if c[8] == WPK_SHADOW:
            continue
        a, b = np.array(c[0:3], np.float64), np.array(c[3:6], np.float64)
        lo = a if lo is None else np.minimum(lo, a)
        hi = b if hi is None else np.maximum(hi, b)
    if lo is None:
        return np.zeros(3), 1.0
    return (lo + hi) / 2.0, max(float(np.linalg.norm(hi - lo) / 2.0) * 0.82, 1e-3)


class View:
    """one frame's camera, from the viewport's wpk_browser.Camera (yaw, pitch, zoom, pan) and the model's bounding sphere"""

    def __init__(self, cam, centre, radius, size, perspective_on=True, fovy=FOVY_DEG):
        W, H = max(int(size[0]), 1), max(int(size[1]), 1)
        self.size = (W, H)
        aspect = W / float(H)
        R = rotation(cam.yaw, cam.pitch)
        self.R = R
        half = math.tan(math.radians(fovy) / 2.0)
        fit = radius / (half * min(1.0, aspect))              # the sphere fits the window's short side
        self.dist = fit / max(cam.zoom, 1e-6)
        self.target = np.asarray(centre, np.float64) + R.T @ np.array([cam.pan[0], cam.pan[1], 0.0])
        self.eye = self.target + R.T @ np.array([0.0, 0.0, self.dist])
        V = np.eye(4)
        V[:3, :3] = R
        V[:3, 3] = -(R @ self.target) + np.array([0.0, 0.0, -self.dist])
        self.V = V
        near = max(radius * 0.002, self.dist - 1.5 * radius)
        far = self.dist + 1.5 * radius
        if perspective_on:
            self.P = perspective(math.radians(fovy), aspect, near, far)
            self.upp = 2.0 * self.dist * half / H            # model units one pixel covers at the target
        else:
            hh = self.dist * half
            self.P = orthographic(hh * aspect, hh, near, far)
            self.upp = 2.0 * hh / H
        self.MVP = self.P @ V

    def project(self, p):
        """a model point -> (x, y) pixels from the top left (for tests and tools)"""
        q = self.MVP @ np.array([p[0], p[1], p[2], 1.0])
        return (q[0] / q[3] * 0.5 + 0.5) * self.size[0], (0.5 - q[1] / q[3] * 0.5) * self.size[1]


def read_ao(path):
    """{texture name: (w, h, bytes)} from a <pack>.ao ("WAO1", n, then {name[48], u16 w, u16 h, u8 [w h]}); {} if none / bad"""
    out = {}
    try:
        with open(path, "rb") as f:
            raw = f.read(16 << 20)
    except OSError:
        return out
    if len(raw) < 8 or raw[:4] != b"WAO1":
        return out
    n = struct.unpack_from("<I", raw, 4)[0]
    at = 8
    for _ in range(n):
        if at + 52 > len(raw):
            break
        name = raw[at:at + 48].split(b"\0")[0].decode("latin-1")
        w, h = struct.unpack_from("<HH", raw, at + 48)
        at += 52
        if w < 1 or h < 1 or at + w * h > len(raw):
            break
        out.setdefault(name, (w, h, raw[at:at + w * h]))
        at += w * h
    return out


# ------------------------------------------------------------------------------------------------
# the GPU side
# ------------------------------------------------------------------------------------------------
class Draw:
    __slots__ = ("ci", "first", "count", "tex", "prog", "env", "spec", "power", "blend", "cut", "ao", "kind")


def _pot(n):
    return n > 0 and (n & (n - 1)) == 0


class GPUModel:
    """a model (wpk_edit.view_model's (textures, chunks, batches)) as GPU buffers and draw lists"""

    def __init__(self, r, model, ao_path=None):
        self.r = r
        ctx = r.ctx
        self.tex_data, self.chunks, batches = model
        self.tags = [c[8] for c in self.chunks]
        self.centre, self.radius = bounds_of(self.chunks)
        self.gl_tex = {}                              # texture index -> moderngl texture, made when a drawn batch needs it
        self.over = {}                                # texture index -> picture standing in for it (the sticker on the body ...)
        self.show_brake = self.show_lamps = True      # additive lamp parts: brake lamps / the others
        self.ao_tex = {}
        ao = read_ao(ao_path) if ao_path else {}
        self.has_ao = bool(ao)
        self._ao_raw = ao
        alpha_min = {}

        def tex_has_alpha(ti):
            if ti >= len(self.tex_data):
                return False
            if ti not in alpha_min:
                im = self.tex_data[ti][1]
                alpha_min[ti] = int(im[..., 3].min()) if im.ndim == 3 and im.shape[2] == 4 else 255
            return alpha_min[ti] < 255

        verts, idx, draws, base = [], [], [], 0
        index_at = 0
        for ci, c in enumerate(self.chunks):
            if c[8] == WPK_SHADOW:
                continue
            for bi in range(c[6], c[6] + c[7]):
                b = batches[bi]
                I = np.asarray(b["I"]).reshape(-1)
                V = b["V"]
                if not len(I) or not len(V):
                    continue
                d = Draw()
                d.ci, d.first, d.count = ci, index_at, len(I)
                d.tex, d.prog, d.env, d.spec, d.power = int(b["tex"]), int(b["prog"]), int(b["env"]), int(b["spec"]), float(b["power"])
                d.blend = int(b["flags"]) & 3
                has_a = tex_has_alpha(d.tex) or int(V["rgba"][:, 3].min()) < 128
                d.cut = bool(has_a and d.prog != 1)       # the game: car.cg writes alpha 1, so it never cuts out
                d.ao = None
                d.kind = None
                if d.blend == 2:                          # a lamp's glow: the brake lamps (car_brakelamp_* textures) or another lamp
                    nm = self.tex_data[d.tex][0].lower() if d.tex < len(self.tex_data) else ""
                    d.kind = "brake" if "brake" in nm else "lamp"
                verts.append(V)
                idx.append(I.astype(np.uint32) + np.uint32(base))
                base += len(V)
                index_at += len(I)
                draws.append(d)
        self.empty = not draws
        if self.empty:
            self.vbo = self.ibo = None
            self.opaque = self.cutout = self.blend = []
            return
        vb = np.concatenate(verts)
        self.vbo = ctx.buffer(np.ascontiguousarray(vb).tobytes())
        self.ibo = ctx.buffer(np.concatenate(idx).tobytes())
        self.vaos = {}
        key = lambda d: (d.prog, d.tex, d.env, d.spec, d.power)       # the order inside opaque passes does not matter: fewer state changes
        self.opaque = sorted([d for d in draws if not d.blend and not d.cut], key=key)
        self.cutout = sorted([d for d in draws if not d.blend and d.cut], key=key)
        self.blend = [d for d in draws if d.blend]                    # blended parts keep the file's order, as the game's
        self.n_tris = sum(d.count for d in draws) // 3
        self.vis = [True] * len(self.chunks)

    # --- textures
    def texture(self, ti):
        t = self.gl_tex.get(ti)
        if t is None:
            if not (0 <= ti < len(self.tex_data)):
                return self.r.white
            t = self.gl_tex[ti] = self.r.make_texture(self.over.get(ti, self.tex_data[ti][1]))
        return t

    def set_overrides(self, over):
        """{texture index: picture} shown instead of the pack's texture (only those that changed are sent to the GPU again)"""
        for ti in set(self.over) | set(over):
            if self.over.get(ti) is not over.get(ti):
                t = self.gl_tex.pop(ti, None)
                if t is not None:
                    t.release()
        self.over = dict(over)

    def ao_texture(self, ti):
        """the baked occlusion map of texture ti (a white one without)"""
        if not self.has_ao or not (0 <= ti < len(self.tex_data)):
            return self.r.white
        if ti not in self.ao_tex:
            name = self.tex_data[ti][0]
            hit = self._ao_raw.get(name)
            if hit is None:
                low = name.lower()
                hit = next((v for k, v in self._ao_raw.items() if k.lower() == low), None)
            self.ao_tex[ti] = self.r.make_ao_texture(*hit) if hit and _pot(hit[0]) and _pot(hit[1]) else None
        return self.ao_tex[ti] or self.r.white

    def vao(self, program_key):
        v = self.vaos.get(program_key)
        if v is None:
            prog = self.r.programs[program_key]
            v = self.vaos[program_key] = self.r.ctx.vertex_array(
                prog, [(self.vbo, "3f 2f 4f1 3i2 2x", "aPos", "aUV", "aColor", "aNormalRaw")],
                index_buffer=self.ibo, index_element_size=4, skip_errors=True)
        return v

    def release(self):
        for t in list(self.gl_tex.values()) + [t for t in self.ao_tex.values() if t]:
            try:
                t.release()
            except Exception:
                pass
        for v in getattr(self, "vaos", {}).values():
            v.release()
        for b in (self.vbo, self.ibo):
            if b is not None:
                b.release()
        self.gl_tex, self.ao_tex = {}, {}


class FlameLayer:
    """Extra things drawn on top of the car in the car's own space (the Backfire tab): flame meshes placed by 4x4 matrices, and small
    axis crosses that mark the exhaust outlets.  Plain numpy in, GPU buffers made lazily on the renderer's context.
        set_assets(meshes, textures)   meshes {key: dict(V (VDT array), I (u16), tex index, blend)}, textures [RGBA uint8 array]
        update_mesh(key, V)            the same mesh with other vertices (a slider moved)
        instances = [(key, 4x4 model matrix)]     markers = [((x, y, z), size, (r, g, b))]
    Use it as a callable: renderer.extras.append(layer)."""

    def __init__(self):
        self.meshes, self.textures = {}, []
        self.instances, self.markers = [], []
        self.gain = 1.0
        self.enabled = True
        self._ctx = None
        self._gpu = {}                 # key -> (vbo, ibo, vao)
        self._tex = {}                 # texture index -> moderngl texture
        self._progs = None
        self._dirty = set()
        self._all_dirty = True

    def set_assets(self, meshes, textures):
        self.meshes, self.textures = dict(meshes), list(textures)
        self._all_dirty = True

    def update_mesh(self, key, V):
        if key in self.meshes:
            self.meshes[key]["V"] = V
            self._dirty.add(key)

    def release(self):
        for vbo, ibo, vao in self._gpu.values():
            for o in (vao, vbo, ibo):
                try:
                    o.release()
                except Exception:
                    pass
        for t in self._tex.values():
            try:
                t.release()
            except Exception:
                pass
        self._gpu, self._tex, self._ctx, self._progs = {}, {}, None, None

    def _setup(self, r):
        if self._ctx is not r.ctx:
            self.release()
            self._ctx = r.ctx
            self._progs = (r.ctx.program(vertex_shader=VS_FLAME, fragment_shader=FS_FLAME),
                           r.ctx.program(vertex_shader=VS_FLAT, fragment_shader=FS_FLAT))
            self._all_dirty = True
        if self._all_dirty:
            for vbo, ibo, vao in self._gpu.values():
                for o in (vao, vbo, ibo):
                    o.release()
            for t in self._tex.values():
                t.release()
            self._gpu, self._tex, self._dirty = {}, {}, set()
            self._all_dirty = False
        for key in list(self._dirty):
            g = self._gpu.get(key)
            m = self.meshes.get(key)
            if g is not None and m is not None and len(m["V"]) * 24 == g[0].size:
                g[0].write(np.ascontiguousarray(m["V"]).tobytes())
            elif g is not None:
                for o in (g[2], g[0], g[1]):
                    o.release()
                del self._gpu[key]
        self._dirty = set()

    def _mesh_gpu(self, r, key):
        g = self._gpu.get(key)
        if g is None:
            m = self.meshes[key]
            if not len(m["V"]) or not len(m["I"]):
                return None
            vbo = r.ctx.buffer(np.ascontiguousarray(m["V"]).tobytes())
            ibo = r.ctx.buffer(np.ascontiguousarray(m["I"], "<u2").tobytes())
            vao = r.ctx.vertex_array(self._progs[0], [(vbo, "3f 2f 4f1", "aPos", "aUV", "aColor")], index_buffer=ibo, index_element_size=2)
            g = self._gpu[key] = (vbo, ibo, vao)
        return g

    def _texture(self, r, ti):
        if not (0 <= ti < len(self.textures)):
            return r.white
        t = self._tex.get(ti)
        if t is None:
            t = self._tex[ti] = r.make_texture(self.textures[ti])
        return t

    def __call__(self, r, view):
        if not self.enabled or (not self.instances and not self.markers):
            return
        self._setup(r)
        ctx = r.ctx
        prog = self._progs[0]
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)                       # both sides of a flame are drawn, as the game does (two_sided)
        ctx.enable(moderngl.BLEND)
        r.msaa.depth_mask = False
        last = None
        prog["uTex"].value = 0
        prog["uGain"].value = float(self.gain)
        for key, M in self.instances:
            m = self.meshes.get(key)
            g = self._mesh_gpu(r, key) if m is not None else None
            if g is None:
                continue
            blend = m["blend"]
            if blend != last:
                ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE) if blend == 2 else (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
                last = blend
            self._texture(r, m["tex"]).use(0)
            prog["uMVP"].write(r._mat(view.MVP @ np.asarray(M, np.float64)))
            g[2].render(moderngl.TRIANGLES)
        if self.markers:
            ctx.disable(moderngl.DEPTH_TEST)                  # the outlet crosses show through the body
            ctx.disable(moderngl.BLEND)
            data = self._marker_triangles(view)
            if len(data):
                fp = self._progs[1]
                buf = ctx.buffer(data.tobytes())
                vao = ctx.vertex_array(fp, [(buf, "3f 3f", "aPos", "aCol")])
                fp["uMVP"].write(r._mat(view.MVP))
                vao.render(moderngl.TRIANGLES)
                vao.release()
                buf.release()
        r.msaa.depth_mask = True
        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)

    def _marker_triangles(self, view):
        """each marker = three axis segments, drawn as thin quads that face the camera (GL lines are one pixel wide)"""
        out = []
        right, up = view.R.T @ np.array([1.0, 0, 0]), view.R.T @ np.array([0, 1.0, 0])
        w = max(view.upp, 1e-6) * 1.6
        for p, size, col in self.markers:
            p = np.asarray(p, np.float64)
            for axis in range(3):
                d = np.zeros(3)
                d[axis] = size
                a, b = p - d, p + d
                for side in (right, up):
                    o = side * w
                    q = [a - o, a + o, b + o, b - o]
                    for k in (0, 1, 2, 0, 2, 3):
                        out.append(np.concatenate([q[k], col]))
        return np.array(out, np.float32) if out else np.zeros((0, 6), np.float32)


class TriLayer:
    """Coloured, see-through triangles drawn over the car in the car's own space, a hair in front of the surface they lie on (the Lamps
    tab: blue = the selected lamps, pink = lens candidates).  set_triangles(data): float32 array (N * 3, 7) = x y z r g b a per vertex,
    colours 0..1.  Plain numpy in, a GPU buffer made lazily on the renderer's context.  Use it as renderer.extras.append(layer)."""
    BIAS = 6e-4                                          # depth pulled toward the camera, as a fraction of the clip depth

    def __init__(self):
        self.data = np.zeros((0, 7), np.float32)
        self.enabled = True
        self._ctx = self._prog = self._vbo = self._vao = None
        self._dirty = True

    def set_triangles(self, data):
        d = np.asarray(data, np.float32).reshape(-1, 7)
        self.data = np.ascontiguousarray(d[: (len(d) // 3) * 3])
        self._dirty = True

    def clear(self):
        self.set_triangles(np.zeros((0, 7), np.float32))

    def release(self):
        for o in (self._vao, self._vbo, self._prog):
            try:
                if o is not None:
                    o.release()
            except Exception:
                pass
        self._ctx = self._prog = self._vbo = self._vao = None
        self._dirty = True

    def __call__(self, r, view):
        if not self.enabled or not len(self.data):
            return
        ctx = r.ctx
        if self._ctx is not ctx:
            self.release()
            self._ctx = ctx
            self._prog = ctx.program(vertex_shader=VS_TRI, fragment_shader=FS_TRI)
        if self._dirty or self._vao is None:
            for o in (self._vao, self._vbo):
                if o is not None:
                    o.release()
            self._vbo = ctx.buffer(self.data.tobytes())
            self._vao = ctx.vertex_array(self._prog, [(self._vbo, "3f 4f", "aPos", "aCol")])
            self._dirty = False
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        r.msaa.depth_mask = False
        self._prog["uMVP"].write(r._mat(view.MVP))
        self._prog["uBias"].value = float(self.BIAS)
        self._vao.render(moderngl.TRIANGLES)
        r.msaa.depth_mask = True
        ctx.disable(moderngl.BLEND)


class Renderer:
    """programs, targets and the draw: needs only a GL 3.3 context (the window's, or a headless one)"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.programs = {}
        self.uniforms = {}
        for prog in (0, 1, 2):
            for cut in (0, 1):
                if prog == 1 and cut:
                    continue                               # car.cg writes alpha 1: one program for both, as in the game
                self._program(("car", prog, cut), VS_CAR, FS_CAR, prog, cut)
        for cut in (0, 1):
            self._program(("id", cut), VS_ID, FS_ID, 0, cut)
        self._program("bg", VS_FULL, FS_BG, 0, 0)
        self._program("composite", VS_FULL, FS_COMPOSITE, 0, 0)
        self.bg_vao = ctx.vertex_array(self.programs["bg"], [])
        self.comp_vao = ctx.vertex_array(self.programs["composite"], [])
        self.white = ctx.texture((1, 1), 4, bytes([255, 255, 255, 255]))
        self.white.repeat_x = self.white.repeat_y = False
        self.sel_tex = ctx.texture((1024, 64), 1, bytes(1024 * 64))
        self.sel_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.sel_tex.repeat_x = self.sel_tex.repeat_y = False
        self.size = (0, 0)
        self.samples = SAMPLES
        self.msaa = self.resolve = self.id_fbo = None
        self.top, self.bottom = (0.24, 0.29, 0.36), (0.09, 0.115, 0.15)
        self.last = None                                  # (view, light, flags) of the last picture: the id pass reuses it
        self.ids_valid = False
        self.sel_ids = set()
        self.extra_error = None
        self.extras = []                                  # callables fn(renderer, view): drawn after the car (FlameLayer)
        self.max_aniso = 1.0
        try:
            self.max_aniso = float(ctx.max_anisotropy)
        except Exception:
            pass

    # --- programs
    def _program(self, key, vs, fs, prog, cut):
        head = "#define PROG %d\n#define CUTOUT %d\n" % (prog, cut)
        v = vs.replace("#version 330 core\n", "#version 330 core\n" + head, 1)
        f = fs.replace("#version 330 core\n", "#version 330 core\n" + head, 1)
        p = self.ctx.program(vertex_shader=v, fragment_shader=f)
        self.programs[key] = p
        self.uniforms[key] = {n: p[n] for n in p if hasattr(p[n], "value") or hasattr(p[n], "write")}
        for name, unit in (("uTex", 0), ("uEnv", 1), ("uSpec", 2), ("uAO", 3), ("uColor", 0), ("uIds", 1), ("uSel", 2)):
            if name in self.uniforms[key]:
                self.uniforms[key][name].value = unit

    def _set(self, key, name, value):
        u = self.uniforms[key].get(name)
        if u is not None:
            if isinstance(value, (bytes, bytearray)):
                u.write(value)
            else:
                u.value = value

    @staticmethod
    def _mat(m):
        return np.ascontiguousarray(np.asarray(m, np.float32).T).tobytes()      # GL wants columns

    # --- textures
    def make_texture(self, im):
        im = np.ascontiguousarray(im)
        if im.ndim == 2:
            im = np.repeat(im[..., None], 3, axis=2)
        if im.shape[2] == 3:
            im = np.concatenate([im, np.full(im.shape[:2] + (1,), 255, im.dtype)], axis=2)
        im = np.ascontiguousarray(im[..., :4], np.uint8)
        h, w = im.shape[:2]
        t = self.ctx.texture((w, h), 4, im.tobytes())
        if _pot(w) and _pot(h):                            # the game: power-of-two = repeat + mipmaps, other sizes clamp, no mipmaps
            t.repeat_x = t.repeat_y = True
            t.build_mipmaps()
            t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            if self.max_aniso > 1.0:
                t.anisotropy = min(8.0, self.max_aniso)
        else:
            t.repeat_x = t.repeat_y = False
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
        return t

    def make_ao_texture(self, w, h, data):
        t = self.ctx.texture((w, h), 1, data)
        t.repeat_x = t.repeat_y = False
        t.build_mipmaps()
        t.filter = (moderngl.LINEAR_MIPMAP_NEAREST, moderngl.LINEAR)
        return t

    # --- targets
    def _targets(self, size):
        if size == self.size and self.msaa is not None:
            return
        self._free_targets()
        ctx, (W, H) = self.ctx, size
        self.size = size
        self.color_tex = ctx.texture((W, H), 4)
        self.color_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.color_tex.repeat_x = self.color_tex.repeat_y = False
        self.resolve = ctx.framebuffer([self.color_tex])
        self.id_tex = ctx.texture((W, H), 4)
        self.id_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.id_tex.repeat_x = self.id_tex.repeat_y = False
        self.id_depth = ctx.depth_renderbuffer((W, H))
        self.id_fbo = ctx.framebuffer([self.id_tex], self.id_depth)
        self.msaa = None
        for s in (self.samples, 0):
            try:
                if s:
                    self.msaa_col = ctx.renderbuffer((W, H), 4, samples=s)
                    self.msaa_depth = ctx.depth_renderbuffer((W, H), samples=s)
                    self.msaa = ctx.framebuffer([self.msaa_col], self.msaa_depth)
                else:
                    self.msaa = self.resolve
                    self.msaa_depth = ctx.depth_renderbuffer((W, H))
                    self.msaa = ctx.framebuffer([self.color_tex], self.msaa_depth)
                self.samples = s
                break
            except Exception:
                continue
        self.ids_valid = False

    def _free_targets(self):
        for name in ("msaa", "resolve", "id_fbo", "color_tex", "id_tex", "id_depth", "msaa_col", "msaa_depth"):
            o = getattr(self, name, None)
            if o is not None:
                try:
                    o.release()
                except Exception:
                    pass
                setattr(self, name, None)
        self.size = (0, 0)

    # --- selection
    def set_selection(self, model, tags):
        self.sel_ids = {ci + 1 for ci, t in enumerate(model.tags) if t in tags} if model is not None else set()
        buf = np.zeros(1024 * 64, np.uint8)
        for i in self.sel_ids:
            if i < buf.size:
                buf[i] = 255
        self.sel_tex.write(buf.tobytes())

    # --- the draw
    def _lights(self, light, view):
        """toward-light unit vectors in the model's space, diffuse colours and ambient (render.c lit_begin / lit_use, the
        fixed-function parts take the material)"""
        R = view.R
        toward = []
        for w in light.dirs:
            w = np.asarray(w, np.float64)
            w = w / max(np.linalg.norm(w), 1e-9)
            toward.append(R.T @ (-w) if light.frame == "view" else -w)
        return toward

    def _frame_uniforms(self, key, view, light, refl, flat, lights):
        p = self.uniforms[key]
        self._set(key, "uMVP", self._mat(view.MVP))
        self._set(key, "uMV", self._mat(view.V))
        self._set(key, "uEye", tuple(float(x) for x in view.eye))
        self._set(key, "uFlat", 1.0 if flat else 0.0)
        self._set(key, "uRefl", 0.0 if flat else float(refl))
        ff = key[1] == 0
        if light is not None:
            self._set(key, "uL0", tuple(float(x) for x in lights[0]))
            self._set(key, "uL1", tuple(float(x) for x in lights[1]))
            d = [tuple(float(light.diffuse[i] * (light.mat_diffuse if ff else 1.0)) for _ in range(3)) for i in range(2)]
            self._set(key, "uD0", d[0])
            self._set(key, "uD1", d[1])
            a = light.ambient * (light.mat_ambient if ff else 1.0)
            self._set(key, "uAmb", (a, a, a))

    def draw(self, model, view, target, light=None, refl=1.0, use_ao=True, flat=False, want_ids=None):
        """one picture into `target` (a moderngl Framebuffer: the window's screen, or an offscreen one)"""
        ctx = self.ctx
        self._targets(view.size)
        W, H = view.size
        self.last = (model, view, light, refl, use_ao, flat)
        self.ids_valid = False
        lights = self._lights(light, view) if light is not None else None
        # ---- 1. the car, multisampled
        fb = self.msaa
        fb.use()
        fb.viewport = (0, 0, W, H)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND | moderngl.CULL_FACE)
        fb.depth_mask = True
        fb.clear(0.0, 0.0, 0.0, 1.0, depth=1.0)
        self._set("bg", "uTop", self.top)
        self._set("bg", "uBottom", self.bottom)
        fb.depth_mask = False
        self.bg_vao.render(moderngl.TRIANGLES, vertices=3)
        fb.depth_mask = True
        if model is not None and not model.empty:
            self._draw_car(model, view, light, lights, refl, use_ao, flat)
        for extra in list(self.extras):
            try:
                extra(self, view)
            except Exception:                                 # an overlay must never take the whole viewport down with it
                self.extra_error = traceback.format_exc()
                self.extras.remove(extra)
                ctx.disable(moderngl.BLEND)
                ctx.enable(moderngl.DEPTH_TEST)
                self.msaa.depth_mask = True
        # ---- 2. resolve
        if self.msaa is not self.resolve and self.samples:
            ctx.copy_framebuffer(self.resolve, self.msaa)
        # ---- 3. part ids (only while something is selected: they outline it)
        has_sel = bool(self.sel_ids) and model is not None and not model.empty
        if has_sel if want_ids is None else want_ids:
            self.render_ids(model, view)
        # ---- 4. onto the target, with the selection
        target.use()
        target.viewport = (0, 0, W, H)
        ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND | moderngl.CULL_FACE)
        self.color_tex.use(0)
        self.id_tex.use(1)
        self.sel_tex.use(2)
        k = "composite"
        self._set(k, "uPx", (1.0 / W, 1.0 / H))
        self._set(k, "uHasSel", 1 if has_sel else 0)
        self._set(k, "uFill", SEL_FILL)
        self._set(k, "uEdge", SEL_EDGE)
        self.comp_vao.render(moderngl.TRIANGLES, vertices=3)

    def _draw_car(self, m, view, light, lights, refl, use_ao, flat):
        ctx = self.ctx
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<="
        done = set()
        cur = {"key": None, "tex": [None] * 4, "power": None}

        def bind(key):
            if key not in done:
                done.add(key)
                self._frame_uniforms(key, view, light, refl, flat, lights)
            if cur["key"] != key:
                cur["key"], cur["power"] = key, None
                cur["tex"] = [None] * 4

        def tex_unit(unit, t):
            if cur["tex"][unit] is not t:
                t.use(unit)
                cur["tex"][unit] = t

        def run(draws, cutout):
            vis = m.vis
            for d in draws:
                if not vis[d.ci]:
                    continue
                key = ("car", d.prog, 1 if (cutout and d.prog != 1) else 0)
                bind(key)
                tex_unit(0, m.texture(d.tex) if d.tex != 0xFFFF else self.white)
                if d.prog:
                    tex_unit(1, m.texture(d.env) if d.env != 0xFFFF else self.white)
                    tex_unit(2, m.texture(d.spec) if d.spec != 0xFFFF else self.white)
                    if d.prog == 1 and cur["power"] != d.power:
                        self._set(key, "uPower", d.power)
                        cur["power"] = d.power
                tex_unit(3, m.ao_texture(d.tex) if (use_ao and m.has_ao) else self.white)
                m.vao(key).render(moderngl.TRIANGLES, vertices=d.count, first=d.first)

        ctx.disable(moderngl.BLEND)
        self.msaa.depth_mask = True
        run(m.opaque, False)
        run(m.cutout, True)
        if any(m.vis[d.ci] for d in m.blend):          # (the lamp switches only skip draws below)
            ctx.enable(moderngl.BLEND)
            self.msaa.depth_mask = False                   # render_blend_pass: blended, no depth write
            last = None
            for d in m.blend:
                if not m.vis[d.ci]:
                    continue
                if d.kind == "brake" and not m.show_brake or d.kind == "lamp" and not m.show_lamps:
                    continue
                if d.blend != last:
                    ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE) if d.blend == 2 else (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
                    last = d.blend
                run([d], False)
            self.msaa.depth_mask = True
            ctx.disable(moderngl.BLEND)

    def render_ids(self, model, view):
        """every opaque pixel's part (chunk index + 1) into the id buffer: for the outline and for picking"""
        if model is None or model.empty:
            return
        ctx = self.ctx
        self._targets(view.size)
        W, H = view.size
        fb = self.id_fbo
        fb.use()
        fb.viewport = (0, 0, W, H)
        ctx.disable(moderngl.BLEND | moderngl.CULL_FACE)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.depth_func = "<="
        fb.depth_mask = True
        fb.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        mvp = self._mat(view.MVP)
        bound = [None, None]
        for draws, cutout in ((model.opaque, 0), (model.cutout, 1)):
            for d in draws:
                if not model.vis[d.ci]:
                    continue
                key = ("id", 1 if (cutout and d.prog != 1) else 0)
                self._set(key, "uMVP", mvp) if bound[0] != key else None
                if bound[0] != key:
                    bound[0] = key
                    bound[1] = None
                self._set(key, "uId", d.ci + 1)
                if key[1]:
                    (model.texture(d.tex) if d.tex != 0xFFFF else self.white).use(0)
                model.vao(key).render(moderngl.TRIANGLES, vertices=d.count, first=d.first)
        self.ids_valid = True

    def pick(self, x, y, radius=3):
        """chunk index under pixel (x, y) from the top left of the last picture; the nearest one within `radius` px, or None"""
        if self.last is None or self.id_fbo is None:
            return None
        model, view = self.last[0], self.last[1]
        if model is None or model.empty:
            return None
        if not self.ids_valid:
            self.render_ids(model, view)
        W, H = view.size
        x, y = int(x), int(y)
        x0, y0 = max(x - radius, 0), max(y - radius, 0)
        x1, y1 = min(x + radius + 1, W), min(y + radius + 1, H)
        if x1 <= x0 or y1 <= y0:
            return None
        raw = self.id_fbo.read(viewport=(x0, H - y1, x1 - x0, y1 - y0), components=4)
        a = np.frombuffer(raw, np.uint8).reshape(y1 - y0, x1 - x0, 4)
        ids = a[..., 0].astype(np.int32) + 256 * a[..., 1].astype(np.int32)
        ids = ids[::-1]                                                  # GL rows run bottom to top
        best, bd = None, None
        for j in range(ids.shape[0]):
            for i in range(ids.shape[1]):
                if ids[j, i] > 0:
                    dd = (x0 + i - x) ** 2 + (y0 + j - y) ** 2
                    if bd is None or dd < bd:
                        best, bd = int(ids[j, i]) - 1, dd
        return best

    def release(self):
        self._free_targets()


def render_image(r, model, cam, size, light=None, **kw):
    """a picture without a window (tests, `shot`): -> PIL image"""
    from PIL import Image
    ctx = r.ctx
    W, H = size
    view = View(cam, model.centre if model else np.zeros(3), model.radius if model else 1.0, size, kw.pop("perspective_on", True))
    tex = ctx.texture((W, H), 4)
    fbo = ctx.framebuffer([tex])
    r.draw(model, view, fbo, light=light, **kw)
    data = fbo.read(components=3)
    fbo.release()
    tex.release()
    return Image.frombytes("RGB", (W, H), data).transpose(Image.FLIP_TOP_BOTTOM), view


# ------------------------------------------------------------------------------------------------
# the Tk side
# ------------------------------------------------------------------------------------------------
def mode():
    """how the GPU picture reaches the Tk window:
    "direct"    an OpenGL surface inside the window (pyopengltk): Windows and Linux; the fastest
    "offscreen" drawn by the GPU into a window-less OpenGL context, copied to memory and shown in the window: macOS (Tk there cannot host
                an OpenGL surface), or anywhere pyopengltk is not installed.  A few milliseconds slower per picture, the same picture.
    VPK_GL_MODE=direct / offscreen forces one (to try the Mac path on Windows)."""
    forced = os.environ.get("VPK_GL_MODE", "").strip().lower()
    if forced in ("direct", "offscreen"):
        return forced
    if sys.platform == "darwin":
        return "offscreen"
    try:
        import pyopengltk  # noqa: F401
    except Exception:
        return "offscreen"
    return "direct"


def import_error():
    """why the GPU viewport cannot run here (a short text), or None when its libraries import"""
    if moderngl is None:
        return "moderngl is not installed (%s)" % _MGL_ERROR
    if mode() == "direct":
        try:
            import pyopengltk  # noqa: F401
        except Exception as ex:
            return "pyopengltk is not installed (%s)" % ex
    else:
        try:
            from PIL import ImageTk  # noqa: F401
        except Exception as ex:
            return "Pillow's ImageTk is not available (%s)" % ex
    return None


_FRAME = []


def _frame_class():
    """pyopengltk's OpenGLFrame with its resize / expose handling replaced (it calls PyOpenGL before a context is current,
    and runs the Tk event loop inside a draw)"""
    if _FRAME:
        return _FRAME[0]
    from pyopengltk import OpenGLFrame

    class Frame(OpenGLFrame):
        owner = None

        def tkMap(self, evt):
            self._wid = self.winfo_id()
            if not self.context_created:
                try:
                    self.tkCreateContext()
                    self.context_created = True
                    self.initgl()
                except Exception:
                    self.owner._fail("Cannot create the OpenGL context", traceback.format_exc())
                    return
            self.owner.request_draw()

        def tkResize(self, evt):
            self.width, self.height = evt.width, evt.height
            self.owner.request_draw()

        def tkExpose(self, evt):
            self.owner.request_draw()

        def _display(self):
            self.tkMakeCurrent()
            self.redraw()
            self.tkSwapBuffers()

        def initgl(self):
            self.owner._init_gl()

        def redraw(self):
            self.owner._redraw()

    _FRAME.append(Frame)
    return Frame


PAUSED = False
_VIEWPORTS = set()


def pause():
    """no redraws until resume() (the window is being re-laid out many times in a row, e.g. while the UI size is measured)"""
    global PAUSED
    PAUSED = True


def resume():
    global PAUSED
    PAUSED = False
    for v in list(_VIEWPORTS):
        v.request_draw()


class GLViewport:
    """the Tk widget (`.widget`), the camera it follows, and what is drawn.  Everything here runs on the Tk thread.
    on_fail(reason, detail) is called (once) when the GPU path cannot work: the owner then goes back to the software viewport."""

    def __init__(self, parent, cam, on_fail, width=600, height=340, on_draw=None):
        self.cam = cam
        self.on_fail = on_fail
        self.on_draw = on_draw
        self.ctx = self.r = self.model = None
        self._pending = None                  # (model, ao_path) waiting for a context
        self._sel = set()
        self.light_name = DEFAULT_SHADING
        self.refl, self.use_ao, self.perspective = 1.0, True, True
        self.bg = None                        # (top colour, bottom colour) of the background, or the default gradient
        self._over, self._over_dirty = {}, False
        self.show_brake = self.show_lamps = True
        self.upp = 0.01
        self.extras = []                      # drawn after the car, see FlameLayer
        self.focus = None                     # (centre, radius) that frames the picture instead of the car's own bounds
        self.failed = None
        self.info = ""
        self._job = None
        self._dirty_sel = True
        _VIEWPORTS.add(self)
        self.widget = self._make_widget(parent, width, height)
        self.widget.bind("<Destroy>", lambda e: self._destroyed() if e.widget is self.widget else None)
        self._dead = False

    # --- the parts that differ between the direct and the offscreen viewport
    def _make_widget(self, parent, width, height):
        w = _frame_class()(parent, width=width, height=height)
        w.owner = self
        w.pack_propagate(False)
        return w

    def _make_current(self):
        self.widget.tkMakeCurrent()

    def _target(self, W, H):
        return self.ctx.screen

    def _present(self, W, H):
        pass                                                   # the direct frame is swapped to the screen by the widget

    def _new_context(self):
        return moderngl.create_context(require=330)

    # --- context
    def _init_gl(self):
        if self.ctx is not None:
            return
        try:
            self.ctx = self._new_context()
            self.r = Renderer(self.ctx)
            self.info = "%s  |  OpenGL %s  |  %d x MSAA%s" % (self.ctx.info.get("GL_RENDERER", "?"), self.ctx.info.get("GL_VERSION", "?"), SAMPLES,
                                                                "  |  off-screen copy" if isinstance(self, OffscreenGLViewport) else "")
        except Exception:
            self.ctx = self.r = None
            self._fail("The GPU viewport could not start (needs OpenGL 3.3)", traceback.format_exc())

    def _fail(self, reason, detail=""):
        if self.failed:
            return
        self.failed = reason
        cb = self.on_fail
        try:
            self.widget.after_idle(lambda: cb(reason, detail))
        except Exception:
            cb(reason, detail)

    def _destroyed(self):
        self._dead = True
        _VIEWPORTS.discard(self)
        self.model = None

    # --- what to draw
    def set_model(self, model, ao_path=None):
        self._pending = (model, ao_path)
        self.request_draw()

    def clear_model(self):
        self._pending = (None, None)
        self.request_draw()

    def set_visible(self, tags):
        self._visible = set(tags)
        if self.model is not None:
            self._apply_visible()
        self.request_draw()

    def _apply_visible(self):
        vis = getattr(self, "_visible", None)
        if self.model is not None and not self.model.empty:
            self.model.vis = [True if vis is None else (t in vis) for t in self.model.tags]

    def set_selection(self, tags):
        self._sel = set(tags)
        self._dirty_sel = True
        self.request_draw()

    def set_look(self, shading=None, refl=None, ao=None, perspective=None):
        if shading is not None and shading in SHADINGS:
            self.light_name = shading
        if refl is not None:
            self.refl = max(0.0, min(1.0, float(refl)))
        if ao is not None:
            self.use_ao = bool(ao)
        if perspective is not None:
            self.perspective = bool(perspective)
        self.request_draw()

    def set_overrides(self, over):
        """{texture index: RGBA picture} to show instead of the pack's textures (the same dict objects: nothing is sent again)"""
        self._over, self._over_dirty = over, True
        self.request_draw()

    def set_lamps(self, brake=True, lamps=True):
        self.show_brake, self.show_lamps = bool(brake), bool(lamps)
        self.request_draw()

    def set_focus(self, centre=None, radius=None):
        """frame this point and radius instead of the model's bounding sphere (None = back to the model)"""
        self.focus = None if centre is None else (np.asarray(centre, np.float64), float(radius))
        self.request_draw()

    def set_background(self, top, bottom):
        self.bg = (top, bottom)
        self.request_draw()

    def reset_background(self):
        """back to the default sky gradient (a module that set its own, e.g. the Lamps dark scene, calls this when it leaves)"""
        self.bg = ((0.24, 0.29, 0.36), (0.09, 0.115, 0.15))
        self.request_draw()

    @property
    def has_ao(self):
        return bool(self.model is not None and self.model.has_ao)

    # --- drawing
    def request_draw(self):
        if PAUSED:
            return
        if self._job is None and not self._dead and not self.failed:
            try:
                self._job = self.widget.after_idle(self._run)
            except Exception:
                self._job = None

    def _run(self):
        self._job = None
        w = self.widget
        if self._dead or self.failed or not w.winfo_exists() or not w.winfo_ismapped() or not w.context_created:
            return
        w._display()

    def _redraw(self):
        if self.r is None or self.failed:
            return
        try:
            t0 = time.perf_counter()
            pend_new = False
            if self._pending is not None:
                pend, self._pending = self._pending, None
                pend_new = True
                if self.model is not None:
                    self.model.release()
                    self.model = None
                if pend[0] is not None:
                    self.model = GPUModel(self.r, pend[0], pend[1])
                    self._apply_visible()
                self._dirty_sel = True
            if self.model is not None:
                if self._over_dirty or pend_new:
                    self.model.set_overrides(self._over)
                    self._over_dirty = False
                self.model.show_brake, self.model.show_lamps = self.show_brake, self.show_lamps
            if self.bg:
                self.r.top, self.r.bottom = self.bg
            if self._dirty_sel:
                self.r.set_selection(self.model, self._sel)
                self._dirty_sel = False
            W, H = max(self.widget.winfo_width(), 2), max(self.widget.winfo_height(), 2)
            m = self.model
            if self.focus is not None:
                centre, radius = self.focus
            else:
                centre, radius = (m.centre, m.radius) if m else (np.zeros(3), 1.0)
            self.r.extras = self.extras
            view = View(self.cam, centre, radius, (W, H), self.perspective)
            self.upp = view.upp
            light = SHADINGS.get(self.light_name, SHADINGS[DEFAULT_SHADING])
            self.r.draw(m, view, self._target(W, H), light=light, refl=self.refl, use_ao=self.use_ao, flat=light is None)
            self._present(W, H)
            self.last_ms = (time.perf_counter() - t0) * 1000.0
            if self.on_draw:
                self.on_draw(self)
        except Exception:
            self._fail("The GPU viewport failed while drawing", traceback.format_exc())

    # --- picking
    def pick(self, x, y):
        """the part tag under pixel (x, y) of the widget, or None"""
        if self.r is None or self.model is None or self.failed:
            return None
        try:
            self._make_current()
            ci = self.r.pick(x, y)
        except Exception:
            self._fail("The GPU viewport failed while picking", traceback.format_exc())
            return None
        return None if ci is None else self.model.tags[ci]

    def screenshot(self):
        """the current picture as a PIL image (drawn again off screen), or None"""
        if self.r is None or self.failed:
            return None
        self._make_current()
        W, H = max(self.widget.winfo_width(), 2), max(self.widget.winfo_height(), 2)
        light = SHADINGS.get(self.light_name, SHADINGS[DEFAULT_SHADING])
        if self.bg:
            self.r.top, self.r.bottom = self.bg
        self.r.extras = self.extras
        img, _ = render_image(self.r, self.model, self.cam, (W, H), light, refl=self.refl, use_ao=self.use_ao,
                              flat=light is None, perspective_on=self.perspective)
        return img

    def close(self):
        self._dead = True
        try:
            if self.r is not None:
                self._make_current()
                if self.model is not None:
                    self.model.release()
                for ex in self.extras:
                    if hasattr(ex, "release"):
                        ex.release()
                self.r.release()
        except Exception:
            pass
        self.model = None


class OffscreenGLViewport(GLViewport):
    """the same viewport for machines where Tk cannot host an OpenGL surface (macOS) or pyopengltk is missing: the GPU draws into a
    window-less OpenGL context (moderngl's standalone context), each picture is read back and shown on a Tk canvas.
    Same interface as GLViewport (camera, picking, selection, screenshot, failure -> software fallback)."""

    def _make_widget(self, parent, width, height):
        import tkinter as tk
        c = tk.Canvas(parent, width=width, height=height, highlightthickness=0, background="#1b2330")
        c.bind("<Configure>", lambda _e: self.request_draw())
        c.bind("<Map>", lambda _e: self.request_draw())
        self._item = self._photo = self._fbo = self._tex = None
        self._fsize = None
        return c

    def _make_current(self):
        pass                                                   # one standalone context, always current on the Tk thread

    def _new_context(self):
        return headless_context()

    def _run(self):
        self._job = None
        w = self.widget
        if self._dead or self.failed or not w.winfo_exists() or not w.winfo_ismapped():
            return
        if self.ctx is None:
            self._init_gl()
            if self.failed or self.ctx is None:
                return
        self._redraw()

    def _target(self, W, H):
        if self._fbo is None or self._fsize != (W, H):
            self._free_fbo()
            self._tex = self.ctx.texture((W, H), 4)
            self._fbo = self.ctx.framebuffer([self._tex])
            self._fsize = (W, H)
        return self._fbo

    def _free_fbo(self):
        for o in (self._fbo, self._tex):
            try:
                if o is not None:
                    o.release()
            except Exception:
                pass
        self._fbo = self._tex = None
        self._fsize = None

    def _present(self, W, H):
        from PIL import Image, ImageTk
        raw = self._fbo.read(components=3)
        img = Image.frombytes("RGB", (W, H), raw).transpose(Image.FLIP_TOP_BOTTOM)      # GL rows run bottom to top
        if self._photo is not None and (self._photo.width(), self._photo.height()) == (W, H):
            self._photo.paste(img)                                                         # same size: no new Tk image
        else:
            self._photo = ImageTk.PhotoImage(img)
            if self._item is None:
                self._item = self.widget.create_image(0, 0, anchor="nw", image=self._photo)
            else:
                self.widget.itemconfigure(self._item, image=self._photo)

    def close(self):
        super().close()
        self._free_fbo()


def create_viewport(parent, cam, on_fail, **kw):
    """the viewport that suits this machine (see mode())"""
    cls = GLViewport if mode() == "direct" else OffscreenGLViewport
    return cls(parent, cam, on_fail, **kw)


# ------------------------------------------------------------------------------------------------
# command line: check a machine, or draw one picture without a window
# ------------------------------------------------------------------------------------------------
def headless_context():
    """a GL 3.3 context without a window (tests, `info`, `shot`): the platform's own, else EGL (a machine without a display)"""
    if moderngl is None:
        raise RuntimeError("moderngl is not installed: pip install moderngl pyopengltk  (%s)" % _MGL_ERROR)
    errs = []
    for backend in (None, "egl"):
        try:
            return moderngl.create_standalone_context(require=330, backend=backend) if backend else moderngl.create_standalone_context(require=330)
        except Exception as ex:
            errs.append("%s: %s" % (backend or "default", ex))
    raise RuntimeError("No OpenGL 3.3 context here: " + "; ".join(errs))


def main(argv):
    if len(argv) < 2 or argv[1] not in ("info", "shot"):
        print(__doc__)
        return 1
    err = import_error()
    print("libraries:", "ok" if not err else err)
    print("viewport  :", mode(), "(OpenGL surface inside the window)" if mode() == "direct" else "(drawn off screen, copied into the window)")
    try:
        ctx = headless_context()
    except RuntimeError as ex:
        raise SystemExit(str(ex))
    print("GL_RENDERER:", ctx.info.get("GL_RENDERER"))
    print("GL_VERSION :", ctx.info.get("GL_VERSION"))
    r = Renderer(ctx)
    print("shaders    : compiled (%d programs), %dx MSAA, anisotropy up to %g" % (len(r.programs), r.samples, r.max_aniso))
    if argv[1] == "info":
        return 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import wpk_edit
    import wpk_browser as WB
    pack = wpk_edit.Pack.load(argv[2])
    model = GPUModel(r, wpk_edit.view_model(pack, wpk_edit.stock_pack_of(argv[2])), os.path.splitext(argv[2])[0] + ".ao")
    from wpk_view import hidden_tags
    hide = hidden_tags(c.tag for c in pack.chunks) | {WPK_SHADOW}
    model.vis = [t not in hide for t in model.tags]
    yaw = float(argv[4]) if len(argv) > 4 else -35.0
    pitch = float(argv[5]) if len(argv) > 5 else 14.0
    name = argv[6] if len(argv) > 6 else DEFAULT_SHADING
    img, _ = render_image(r, model, WB.Camera(yaw, pitch), (1280, 720), SHADINGS[name], flat=SHADINGS[name] is None)
    img.save(argv[3])
    print("wrote", argv[3])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
