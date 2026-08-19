#!/usr/bin/env bash
# One-click launcher: installs what's needed, then opens the app in your browser.
set -e
cd "$(dirname "$0")"

echo
echo "  Credit Report Extractor - starting up..."
echo

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "  Python is not installed. Get it from https://www.python.org/downloads/"
  exit 1
fi

echo "  Checking dependencies (first run takes a minute)..."
"$PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt

exec "$PY" app.py
