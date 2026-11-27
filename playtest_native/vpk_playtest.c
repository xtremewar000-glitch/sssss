/* A flat C interface to the arcade's own race code (car.c + the recompiled physics) for the VPK tool's Playtest: ctypes can call it
 * without knowing car_state's layout.  One race at a time.  Units: the game's (1 unit = 0.1 m, +y up).  Matrices are GL column-major.
 *
 * pt_read(out) fills out[0..PT_N): see the PT_* offsets below. */
#include <string.h>
#include "car.h"
#include "cardb.h"

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

static car_state S;
static int started;

enum { PT_BODY = 0,          /* 16 */
       PT_TYRE = 16,         /* 4 x 16: FL FR RL RR, relative to the body */
       PT_TYRE_ROT = 80,     /* 4 */
       PT_CAM_VIEW = 84,     /* 16: world -> camera */
       PT_CAM_POS = 100,     /* 3 */
       PT_CAM_FOV = 103, PT_CAM_NEAR = 104,
       PT_KMH = 105, PT_RPM = 106, PT_GEAR = 107, PT_SPEED = 108, PT_ON_GROUND = 109,
       PT_COUNTDOWN = 110, PT_TOTAL = 111, PT_TIME_LEFT = 112, PT_REMAIN = 113, PT_SECTION = 114, PT_PLAY_STATE = 115,
       PT_SLIP = 116, PT_BRAKE = 117, PT_BLUR = 118, PT_REV_RATE = 119, PT_BOOST = 120,
       PT_TRAFFIC_N = 121,   /* then 12 x (model, 16 floats) */
       PT_TRAFFIC = 122,
       PT_N = 122 + 12 * 17 };

EXPORT int pt_size(void) { return PT_N; }
EXPORT int pt_init(const char *data_dir, char *err, int n) { return car_init(data_dir, err, n); }

/* course: car.h's CAR_COURSES order (9 = Hakone).  tyres: front x, radius, z, rear x, radius, z (mm). */
EXPORT int pt_start(int course, int manual, unsigned type, int gears, const float *tyres, int spokes, int power, int handling, int traffic) {
  car_spec sp;
  memset(&sp, 0, sizeof sp);
  sp.type = type; sp.gears = gears; sp.spokes = spokes; sp.power = power; sp.handling = handling;
  memcpy(sp.tyres, tyres, 6 * sizeof(float));
  memset(&S, 0, sizeof S);
  car_set_rivals(NULL, 0);
  car_set_ta_traffic(traffic);
  started = car_start(&S, course, manual, &sp) == 0;
  return started ? 0 : -1;
}

EXPORT void pt_step(float steer, float throttle, float brake, int up, int down) {
  car_input in;
  memset(&in, 0, sizeof in);
  in.steer = steer; in.throttle = throttle; in.brake = brake; in.shift_up = up; in.shift_down = down;
  if (started) car_update(&S, &in);
}
EXPORT void pt_reset(void) { if (started) car_reset(&S); }
EXPORT void pt_manual(int m) { if (started) car_set_manual(&S, m); }
EXPORT void pt_view(int v) { car_view(v); }

EXPORT int pt_read(float *o) {
  if (!started) return -1;
  memcpy(o + PT_BODY, S.body, 64);
  memcpy(o + PT_TYRE, S.tyre, 4 * 64);
  memcpy(o + PT_TYRE_ROT, S.tyre_rot, 16);
  memcpy(o + PT_CAM_VIEW, S.cam_view, 64);
  memcpy(o + PT_CAM_POS, S.cam_pos, 12);
  o[PT_CAM_FOV] = S.cam_fov; o[PT_CAM_NEAR] = S.cam_near;
  o[PT_KMH] = car_kmh(&S); o[PT_RPM] = S.rpm; o[PT_GEAR] = (float)S.gear; o[PT_SPEED] = S.speed; o[PT_ON_GROUND] = (float)S.on_ground;
  o[PT_COUNTDOWN] = S.race.countdown; o[PT_TOTAL] = S.race.total; o[PT_TIME_LEFT] = S.race.time_left; o[PT_REMAIN] = S.race.remain;
  o[PT_SECTION] = (float)S.race.section; o[PT_PLAY_STATE] = (float)S.race.play_state;
  o[PT_SLIP] = S.slip; o[PT_BRAKE] = (float)S.brake; o[PT_BLUR] = S.cam_blur; o[PT_REV_RATE] = S.rev_rate; o[PT_BOOST] = S.boost;
  o[PT_TRAFFIC_N] = (float)S.n_traffic;
  for (int i = 0; i < S.n_traffic && i < CAR_TRAFFIC; i++) {
    o[PT_TRAFFIC + 17 * i] = (float)S.traffic[i].model;
    memcpy(o + PT_TRAFFIC + 17 * i + 1, S.traffic[i].body, 64);
  }
  return 0;
}

/* the car pack's name (e.g. "nis_Z33") -> its ECarType, gears and tyre dimensions from the arcade's car select data; 0 if unknown */
EXPORT int pt_car_info(const char *pack, unsigned *type, int *gears, float *tyres, int *spokes) {
  int idx = -1;
  for (int i = 0; i < CARDB_NPACKS; i++) if (!strcmp(CARDB_PACKS[i], pack)) { idx = i; break; }
  if (idx < 0) return 0;
  *type = 0u; *gears = 5;
  for (int m = 0; m < CARDB_ALL; m++)
    for (int c = 0; c < CARDB_MAX_CARS; c++) {
      const cardb_car *k = &CARDB_CARS[m][c];
      for (int j = 0; j < k->ncolors; j++)
        if (k->colors[j].pack == idx) { *type = (unsigned)k->colors[j].type; *gears = k->colors[j].gears; goto found; }
    }
found:
  memcpy(tyres, CARDB_TYRES[idx], 6 * sizeof(float));
  *spokes = (int)CARDB_TYRES[idx][6];
  return 1;
}
