#!/bin/sh
cd "$(dirname "$0")" || exit 1
python3 -c "import numpy, PIL" 2>/dev/null || python3 -m pip install -r requirements.txt
python3 -c "import moderngl" 2>/dev/null || python3 -m pip install -r requirements-gpu.txt || echo "(GPU viewport not installed: the software viewport will be used)"
python3 vpk_tool.py "$@"
