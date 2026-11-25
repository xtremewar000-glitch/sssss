#!/bin/sh
# Build vpk_tool.app for macOS.  Run this ON A MAC (a Windows .exe cannot be turned into a Mac app).
# Needs: Python 3.9+ from python.org (it includes Tk).  Result: dist/vpk_tool.app  and  dist/vpk_tool-mac.zip
# The app is for the chip you build on (Apple Silicon or Intel).  It is not signed: see the note at the end.
cd "$(dirname "$0")" || exit 1
set -e
python3 -m pip install -r requirements.txt pyinstaller
# GPU viewport on macOS: moderngl draws off screen (OpenGL 4.1 via Apple), the picture is copied into the window.  (pyopengltk has no macOS support.)
python3 -m pip install -r requirements-gpu.txt
python3 -m PyInstaller --noconfirm --windowed --name vpk_tool \
    --add-data "bake_ao.c:." --add-data "assets:assets" --collect-all moderngl --collect-all glcontext vpk_tool.py
rm -f dist/vpk_tool-mac.zip
ditto -c -k --keepParent dist/vpk_tool.app dist/vpk_tool-mac.zip       # a zip that keeps the .app intact
echo
echo "Built: dist/vpk_tool.app   (zip to send: dist/vpk_tool-mac.zip)"
echo "Not signed: the first time, right-click the app > Open > Open (or: xattr -dr com.apple.quarantine vpk_tool.app)."
echo "Bake AO needs Apple's Command Line Tools once:  xcode-select --install"
