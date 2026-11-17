#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")"
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON=python3.11
else
  PYTHON=python3
fi
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 11), "需要 Python 3.11"'
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf '%s\n' '安装完成：运行 bash start.sh'
