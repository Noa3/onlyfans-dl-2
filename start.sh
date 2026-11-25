#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  bash setup.sh
fi
exec .venv/bin/python onlyfans-dl.py "$@"
