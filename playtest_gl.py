"""What the Playtest draws inside the Models page's GPU viewport: the map and its sky (stage_wpk packs), the car at the physics' body and
tyre postures, seen through the arcade's own camera.  One callable layer for gl_viewport's `extras`, and `PlayView`, the camera it hands the
viewport instead of the orbit camera.  Needs moderngl (imported by gl_viewport); the numpy parts (matrices, wheel postures, culling) are
plain functions so they can be tested without a GPU.

World units are the game's (1 unit = 0.1 m, +y up); matrices are numpy 4x4 (row-major maths), the physics gives GL column-major floats."""
import math
import numpy as np

try:
    import moderngl
except Exception:                       # tests without a GPU library
    moderngl = None

# ---- the game's settings for a city and its time of day (app.c: FOG, MAP_GRADE, CAR_LIGHT, the ranges)
RANGES = (5000.0, 7000.0, 10000.0)      # ground, short-range buildings, long-range buildings
CITY_ENV = {
    # city: [(fog far, fog near, fog colour, map grade, car sun dir, diffuse, ambient), same at night]
    "hakone": [(15000.0, 100.0, (0.6, 0.7, 0.75), (1.16, 1.12, 1.02), (0.5, -1.0, -0.3), 0.62, 0.31),
               (3600.0, 250.0, (0.1, 0.1, 0.1), (0.72, 0.74, 0.82), (0.5, -1.0, -0.3), 0.79, 0.14)],
    "tokyo": [(35000.0, 100.0, (0.6, 0.75, 0.8), (1, 1, 1), (-0.5, -1.0, -0.3), 0.62, 0.35),
              (25000.0, 250.0, (0.6, 0.75, 0.8), (1, 1, 1), (0.5, -0.15, -0.3), 0.73, 0.32)],
}
DEFAULT_ENV = [(25000.0, 1.0, (0.88, 0.88, 0.905), (1, 1, 1), (-0.5, -1.0, -0.3), 0.62, 0.35),
               (25000.0, 250.0, (0.3, 0.3, 0.3), (1, 1, 1), (0.5, -0.2, -0.3), 0.73, 0.54)]
SKY_DROP = 10000.0


def env_of(city, night):
    return (CITY_ENV.get(city) or DEFAULT_ENV)[1 if night else 0]


def col_major(f):
    """16 floats from the physics (GL column-major) -> a numpy matrix to multiply with (row-major maths)"""
    return np.asarray(f, np.float64).reshape(4, 4).T.copy()


def box_distance(lo, hi, p):
    d = np.maximum(np.maximum(lo - p, p - hi), 0.0)
    return float(np.sqrt((d * d).sum()))


def wheel_matrix(tyre, rot, centre, hub=False):
    """The posture of one wheel chunk (render.c: the tyre's posture turned about x by the spin, standing so that the chunk's centre lands
    at the posture's hub height).  tyre: the physics' 16 floats, rot: radians, centre: the chunk's box centre."""
    t = np.asarray(tyre, np.float64).reshape(4, 4).T          # columns of the physics matrix -> numpy
    c, s = math.cos(rot), math.sin(rot)
    w = np.eye(4)
    w[:3, 0] = t[:3, 0]
    w[:3, 1] = t[:3, 1] * c + t[:3, 2] * s
    w[:3, 2] = -t[:3, 1] * s + t[:3, 2] * c
    h = np.asarray(centre, np.float64)
    at = np.array([h[0], h[1] if hub else t[1, 3], h[2]])
    w[:3, 3] = at - w[:3, :3] @ h
    return w


def perspective_fovy_h(fov_x, aspect, near, far):
    """the arcade's camera: its fov is across a 4:3 picture; a wider window sees more at the sides"""
    fovy = 2.0 * math.atan(math.tan(fov_x * 0.5) / (4.0 / 3.0))
    f = 1.0 / math.tan(fovy * 0.5)
    P = np.zeros((4, 4))
    P[0, 0], P[1, 1] = f / aspect, f
    P[2, 2], P[2, 3] = (far + near) / (near - far), 2.0 * far * near / (near - far)
    P[3, 2] = -1.0
    return P, fovy


class PlayView:
    """what gl_viewport.View gives its layers (MVP, V, P, size, upp, eye), but from the physics' camera"""

    def __init__(self, st, size, far=20000.0):
        W, H = max(int(size[0]), 1), max(int(size[1]), 1)
        self.size = (W, H)
        self.V = col_major(st["cam_view"])
        self.R = self.V[:3, :3].copy()
        self.P, fovy = perspective_fovy_h(st["cam_fov"], W / float(H), max(st["cam_near"], 1.0), far)
        self.P_sky, _ = perspective_fovy_h(st["cam_fov"], W / float(H), 100.0, 600000.0)
        self.MVP = self.P @ self.V
        self.eye = np.array(st["cam_pos"], np.float64)
        self.upp = 2.0 * 1000.0 * math.tan(fovy * 0.5) / H
        self.dist = 1.0


VS_MAP = """#version 330 core
in vec3 aPos; in vec2 aUV; in vec4 aColor;
uniform mat4 uMVP; uniform vec3 uEye;
out vec2 vUV; out vec4 vCol; out float vDist;
void main(){ gl_Position = uMVP * vec4(aPos, 1.0); vUV = aUV; vCol = aColor; vDist = distance(aPos, uEye); }
"""
FS_MAP = """#version 330 core
in vec2 vUV; in vec4 vCol; in float vDist;
uniform sampler2D uTex; uniform int uHasTex; uniform int uCut; uniform vec3 uGrade; uniform vec2 uFog; uniform vec3 uFogCol; uniform float uAdd;
out vec4 oCol;
void main(){
  vec4 c = vCol;
  if (uHasTex != 0) c *= texture(uTex, vUV);
  if (uCut != 0 && c.a < 0.5) discard;
  c.rgb *= uGrade;
  float f = clamp((uFog.x - vDist) / max(uFog.x - uFog.y, 1.0), 0.0, 1.0);
  if (uAdd > 0.5) { c.rgb *= f; }                     // additive parts fade out in the fog instead of turning fog-coloured
  else c.rgb = mix(uFogCol, c.rgb, f);
  oCol = c;
}
"""
VS_CAR = """#version 330 core
in vec3 aPos; in vec2 aUV; in vec4 aColor; in vec3 aNormalRaw;
uniform mat4 uMVP; uniform mat3 uNrm;
out vec2 vUV; out vec4 vCol; out vec3 vN;
void main(){ gl_Position = uMVP * vec4(aPos, 1.0); vUV = aUV; vCol = aColor; vN = uNrm * max(aNormalRaw / 32767.0, -1.0); }
"""
FS_CAR = """#version 330 core
in vec2 vUV; in vec4 vCol; in vec3 vN;
uniform sampler2D uTex; uniform int uHasTex; uniform int uCut; uniform vec3 uSun; uniform float uDiff; uniform float uAmb; uniform float uLit;
out vec4 oCol;
void main(){
  vec4 c = vCol;
  if (uHasTex != 0) c *= texture(uTex, vUV);
  if (uCut != 0 && c.a < 0.5) discard;
  if (uLit > 0.5) {
    float l = uAmb + uDiff * max(dot(normalize(vN), -normalize(uSun)), 0.0) * 1.6 + 0.35;
    c.rgb *= clamp(l, 0.0, 1.4);
  }
  oCol = c;
}
"""


def _vbo_bytes(V):
    return np.ascontiguousarray(V).tobytes()


class PlayLayer:
    """gl_viewport extras entry: layer(renderer, view).  set_stage / set_sky / set_car give it data (numpy, any thread before the draw);
    `state` is the physics' latest dict (pt_read), `braking` and `night` say what to show."""

    def __init__(self):
        self.stage = self.sky = None
        self.city, self.night = "hakone", False
        self.car = None                   # dict from prepare_car
        self.state = None
        self.braking = False
        self.enabled = True
        self._ctx = None
        self._map = None                  # GPU copy of the stage
        self._skyg = None
        self._cargpu = None
        self._tex = {}
        self._progs = None
        self.draws = 0

    # ---- data in
    def set_stage(self, stage, sky=None, city="hakone", night=False):
        self.stage, self.sky, self.city, self.night = stage, sky, city, night
        self._map = self._skyg = None
        self._free_tex("map")

    def set_car(self, car):
        self.car = car
        self._cargpu = None
        self._free_tex("car")

    def release(self):
        for t in self._tex.values():
            try:
                t.release()
            except Exception:
                pass
        for g in (self._map, self._skyg, self._cargpu):
            if g:
                for o in g.get("objs", ()):
                    try:
                        o.release()
                    except Exception:
                        pass
        self._tex, self._map, self._skyg, self._cargpu, self._progs, self._ctx = {}, None, None, None, None, None

    def _free_tex(self, kind):
        for k in [k for k in self._tex if k[0] == kind]:
            try:
                self._tex.pop(k).release()
            except Exception:
                pass

    # ---- GPU setup
    def _setup(self, r):
        if self._ctx is not r.ctx:
            self.release()
            self._ctx = r.ctx
            c = r.ctx
            self._progs = dict(map=c.program(vertex_shader=VS_MAP, fragment_shader=FS_MAP),
                               car=c.program(vertex_shader=VS_CAR, fragment_shader=FS_CAR))
            for p in self._progs.values():
                if "uTex" in p:
                    p["uTex"].value = 0

    def _texture(self, r, kind, owner, i, im):
        k = (kind, owner, i)
        t = self._tex.get(k)
        if t is None:
            t = self._tex[k] = r.make_texture(im)
        return t

    def _build_scene(self, r, st, kind, with_normals=False):
        """one VBO / IBO for a whole pack and its draw list [(chunk index, batch, first index, count, cutout)]"""
        ctx = r.ctx
        verts, idx, draws, base, at = [], [], [], 0, 0
        for ci, ch in enumerate(st.chunks):
            for b in ch.batches:
                if not len(b.I) or not len(b.V):
                    continue
                verts.append(np.ascontiguousarray(b.V))
                idx.append(b.I.astype(np.uint32) + np.uint32(base))
                alpha = False
                if b.tex >= 0 and b.tex < len(st.textures):
                    im = st.textures[b.tex][1]
                    alpha = im.shape[2] == 4 and int(im[..., 3].min()) < 255
                alpha = alpha or int(b.V["rgba"][:, 3].min()) < 128
                draws.append((ci, b, at, len(b.I), bool(alpha and b.blend == 0)))
                base += len(b.V)
                at += len(b.I)
        if not draws:
            return dict(draws=[], objs=[])
        vbo = ctx.buffer(np.concatenate(verts).tobytes())
        ibo = ctx.buffer(np.concatenate(idx).tobytes())
        prog = self._progs["map"]
        vao = ctx.vertex_array(prog, [(vbo, "3f 2f 4f1", "aPos", "aUV", "aColor")], index_buffer=ibo, index_element_size=4)
        los = np.array([c.lo for c in st.chunks], np.float64)
        his = np.array([c.hi for c in st.chunks], np.float64)
        return dict(draws=draws, vao=vao, objs=[vbo, ibo, vao], lo=los, hi=his, kind=kind)

    # ---- drawing
    def __call__(self, r, view):
        if not self.enabled or self.state is None:
            return
        self._setup(r)
        ctx = r.ctx
        st = self.state
        far, near, fogc, grade, sun, diff, amb = env_of(self.city, self.night)
        self.draws = 0
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        # sky: behind everything, no fog, no depth
        if self.sky is not None:
            if self._skyg is None:
                self._skyg = self._build_scene(r, self.sky, "sky")
            g = self._skyg
            if g["draws"]:
                eye = view.eye
                M = np.eye(4)
                M[:3, 3] = (eye[0], eye[1] - SKY_DROP, eye[2])
                ctx.disable(moderngl.DEPTH_TEST)
                self._draw_stage(r, g, self.sky, "sky", view.P_sky @ view.V @ M, view.eye, (1, 1, 1), (2e9, 1e9), (0, 0, 0), None)
                ctx.enable(moderngl.DEPTH_TEST)
        # the map
        if self.stage is not None:
            if self._map is None:
                self._map = self._build_scene(r, self.stage, "map")
            self._draw_stage(r, self._map, self.stage, "map", view.MVP, view.eye, grade, (far, near), fogc, RANGES)
        # the car
        if self.car is not None:
            self._draw_car(r, view, st, sun, diff, amb)
        ctx.disable(moderngl.BLEND)
        ctx.enable(moderngl.DEPTH_TEST)
        r.msaa.depth_mask = True

    def _draw_stage(self, r, g, stage, kind, mvp, eye, grade, fog, fogc, ranges):
        if not g["draws"]:
            return
        ctx = r.ctx
        prog = self._progs["map"]
        prog["uMVP"].write(r._mat(mvp))
        prog["uEye"].value = tuple(float(x) for x in eye)
        prog["uGrade"].value = tuple(float(x) for x in grade)
        prog["uFog"].value = (float(fog[0]), float(fog[1]))
        prog["uFogCol"].value = tuple(float(x) for x in fogc)
        ok = None
        if ranges is not None:
            d = np.array([box_distance(g["lo"][i], g["hi"][i], eye) for i in range(len(g["lo"]))])
            grp = np.array([min(c.group, 2) for c in stage.chunks])
            ok = d <= np.array(ranges)[grp]
        vao = g["vao"]
        for pass_ in (0, 1, 2):
            if pass_ == 2:
                ctx.enable(moderngl.BLEND)
                r.msaa.depth_mask = False
            for ci, b, at, n, cut in g["draws"]:
                if ok is not None and not ok[ci]:
                    continue
                if pass_ == 0 and (b.blend or cut):
                    continue
                if pass_ == 1 and (b.blend or not cut):
                    continue
                if pass_ == 2 and not b.blend:
                    continue
                if pass_ == 2:
                    ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE) if b.blend == 2 else (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
                prog["uAdd"].value = 1.0 if b.blend == 2 else 0.0
                prog["uCut"].value = 1 if cut else 0
                if 0 <= b.tex < len(stage.textures):
                    self._texture(r, kind, id(stage), b.tex, stage.textures[b.tex][1]).use(0)
                    prog["uHasTex"].value = 1
                else:
                    prog["uHasTex"].value = 0
                vao.render(moderngl.TRIANGLES, vertices=n, first=at)
                self.draws += 1
        ctx.disable(moderngl.BLEND)
        r.msaa.depth_mask = True

    def _draw_car(self, r, view, st, sun, diff, amb):
        car = self.car
        ctx = r.ctx
        g = self._cargpu
        prog = self._progs["car"]
        if g is None:
            verts, idx, draws, base, at = [], [], [], 0, 0
            for ci, ch in enumerate(car["chunks"]):
                for b in ch["batches"]:
                    I = np.asarray(b["I"]).reshape(-1)
                    V = b["V"]
                    if not len(I) or not len(V):
                        continue
                    verts.append(np.ascontiguousarray(V))
                    idx.append(I.astype(np.uint32) + np.uint32(base))
                    draws.append((ci, b, at, len(I)))
                    base += len(V)
                    at += len(I)
            if not draws:
                self._cargpu = dict(draws=[], objs=[])
                return
            vbo = ctx.buffer(np.concatenate(verts).tobytes())
            ibo = ctx.buffer(np.concatenate(idx).tobytes())
            vao = ctx.vertex_array(prog, [(vbo, "3f 2f 4f1 3i2 2x", "aPos", "aUV", "aColor", "aNormalRaw")],
                                   index_buffer=ibo, index_element_size=4, skip_errors=True)
            g = self._cargpu = dict(draws=draws, vao=vao, objs=[vbo, ibo, vao])
        if not g["draws"]:
            return
        body = col_major(st["body"])
        mats = {}
        show = car["shown"](self.braking)
        for ci, ch in enumerate(car["chunks"]):
            tag = ch["tag"]
            if tag not in show:
                continue
            m = body
            if 0x100 <= tag < 0x104:
                m = body @ wheel_matrix(st["tyre"][tag - 0x100], st["tyre_rot"][tag - 0x100], ch["centre"])
            mats[ci] = m
        prog["uSun"].value = tuple(float(x) for x in sun)
        prog["uDiff"].value = float(diff)
        prog["uAmb"].value = float(amb)
        for pass_ in (0, 1, 2):
            if pass_ == 2:
                ctx.enable(moderngl.BLEND)
                r.msaa.depth_mask = False
            for ci, b, at, n in g["draws"]:
                m = mats.get(ci)
                if m is None:
                    continue
                blend = int(b["flags"]) & 3
                cut = car["cut"].get(id(b), False) and not blend
                if (pass_ == 0 and (blend or cut)) or (pass_ == 1 and (blend or not cut)) or (pass_ == 2 and not blend):
                    continue
                if pass_ == 2:
                    ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE) if blend == 2 else (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
                prog["uMVP"].write(r._mat(view.MVP @ m))
                prog["uNrm"].write(np.ascontiguousarray(m[:3, :3].T, np.float32).tobytes())
                prog["uCut"].value = 1 if cut else 0
                prog["uLit"].value = 0.0 if blend == 2 else 1.0
                t = int(b["tex"])
                if 0 <= t < len(car["tex"]):
                    self._texture(r, "car", id(car), t, car["tex"][t][1]).use(0)
                    prog["uHasTex"].value = 1
                else:
                    prog["uHasTex"].value = 0
                g["vao"].render(moderngl.TRIANGLES, vertices=n, first=at)
                self.draws += 1
        ctx.disable(moderngl.BLEND)
        r.msaa.depth_mask = True


def prepare_car(model, hidden):
    """model = wpk_edit.view_model(pack) ((name, rgba) list, chunk tuples (lo*3, hi*3, first, n, tag), batch dicts);
    hidden = the tags the Parts tab leaves out (variants).  Returns what PlayLayer.set_car takes."""
    tex, chunks, batches = model
    out = []
    cut = {}
    for c in chunks:
        first, n, tag = c[6], c[7], c[8]
        bl = [batches[i] for i in range(first, first + n)]
        for b in bl:
            ti = int(b["tex"])
            alpha = 0 <= ti < len(tex) and tex[ti][1].ndim == 3 and tex[ti][1].shape[2] == 4 and int(tex[ti][1][..., 3].min()) < 255
            alpha = alpha or int(b["V"]["rgba"][:, 3].min()) < 128 if len(b["V"]) else alpha
            cut[id(b)] = bool(alpha and int(b["prog"]) != 1)
        out.append(dict(tag=tag, batches=bl, centre=0.5 * (np.array(c[0:3]) + np.array(c[3:6]))))
    tags = {c["tag"] for c in out}

    def shown(braking):
        s = set()
        for t in tags:
            if t in hidden or t == 0xFFFF or t == 0x110:        # hidden variants, the shadow volume, the unlit lamps
                continue
            if t == 0x112 and braking:
                continue
            if t == 0x113 and not braking:
                continue
            if 0x140 <= t < 0x180 and not braking:
                continue
            s.add(t)
        return s
    return dict(tex=tex, chunks=out, cut=cut, shown=shown)
