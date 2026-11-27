Playtest physics library
========================
The Playtest module (Vehicle Models > Playtest) runs the arcade's own race code: car.c + the recompiled physics (phys_rc.c) from the
Re3DXPlus repo.  vpk_playtest.c wraps them in a flat C interface the tool can call; this folder builds it as a library:

    Windows   vpkplay.dll        macOS   libvpkplay.dylib        Linux   libvpkplay.so

Easiest: GitHub Actions in your Re3DXPlus repo
  1. Copy this folder's vpk_playtest.c and build.sh into the repo as tools/vpk_playtest/
  2. Copy build_playtest_lib.yml to .github/workflows/
  3. Actions > "Build Playtest physics library" > Run workflow; download the artifact for your system (a few minutes).

Or on your own machine (gcc or clang, python3):   ./build.sh /path/to/Re3DXPlus

Then put the library next to vpk_tool (or in this folder), or pick it in Playtest > Game files > Physics library.
The library is built with the same switches as the Android build (-DRC_FPT=double -ffp-contract=off).

What Playtest needs from the game's data folder:  phys.img, carconfig/, and for the course's city  <city>.wpk (+ <city>_night.wpk,
sky_<city>.wpk, sky_<city>_night.wpk), e.g. hakone.wpk.
