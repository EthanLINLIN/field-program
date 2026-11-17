#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' '请先运行 bash setup.sh' >&2
  exit 1
fi
exec .venv/bin/python run.py "$@"
