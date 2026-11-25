@echo off
REM Needs Python 3.9+ from python.org (tick "tcl/tk"). Installs numpy and pillow on the first start.
REM The GPU viewport (moderngl, pyopengltk) is installed too when it can be; without it the old software viewport is used.
REM Tip: drop a car's .vpk onto this file to open it.
cd /d "%~dp0"
py -3 -c "import numpy, PIL" 2>nul || py -3 -m pip install -r requirements.txt
py -3 -c "import moderngl, pyopengltk" 2>nul || py -3 -m pip install -r requirements-gpu.txt
py -3 vpk_tool.py %*
if errorlevel 1 pause
