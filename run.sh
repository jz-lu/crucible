#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.lock
  .venv/bin/python -m pip install --no-deps -e .
fi
.venv/bin/python -m crucible.manage migrate
exec .venv/bin/uvicorn crucible.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
