@echo off
REM Optional: one vpk_tool.exe that needs no Python (run on Windows).
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller
py -3 -m pip install -r requirements-gpu.txt
py -3 -m PyInstaller --onefile --windowed --add-data "bake_ao.c;." --add-data "bake_ao_win.exe;." --add-data "assets;assets" --collect-all moderngl --collect-all glcontext --collect-all pyopengltk --collect-submodules OpenGL --hidden-import OpenGL.platform.win32 --name vpk_tool vpk_tool.py
echo.
echo Built: dist\vpk_tool.exe
pause
