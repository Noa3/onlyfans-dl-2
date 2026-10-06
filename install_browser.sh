#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ ! -x .venv/bin/python ]]; then
  bash setup.sh
fi
echo 'Installing optional Playwright and downloading Chromium. No login details are needed.'
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
echo 'Browser helper installed. Linux may additionally need OS browser libraries; see README.md.'
