#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python || ! -f static/dist/app.js ]]; then
  echo '먼저 ./setup.sh를 실행해 주세요.' >&2
  exit 1
fi
exec .venv/bin/python server.py "$@"
