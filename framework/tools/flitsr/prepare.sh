#!/usr/bin/env bash
# prepare.sh — install FLITSR into a private venv next to this script.
#
# FLITSR (Callaghan & Fischer, ISSTA 2023) is a pure-Python SBFL/multi-fault
# localization tool: https://github.com/DCallaz/flitsr . Nothing is installed
# system-wide; everything lives in framework/tools/flitsr/.venv (git-ignored).
#
# Requirements on the machine: python3 (>= 3.8) with the `venv` module
# (Debian/Ubuntu: apt install python3-venv) and network access to PyPI.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null || { echo "[x] $PY not found" >&2; exit 1; }
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[+] creating venv $VENV ($("$PY" --version))"
  "$PY" -m venv "$VENV" || {
    echo "[x] 'python3 -m venv' failed — install the venv module (e.g. apt install python3-venv)" >&2; exit 1; }
fi
echo "[+] installing $(grep -v '^#' "$HERE/requirements.txt" | tr '\n' ' ')"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
"$VENV/bin/flitsr" --help >/dev/null || { echo "[x] flitsr does not run" >&2; exit 1; }
echo "[+] flitsr ready: $VENV/bin/flitsr ($("$VENV/bin/python" -m pip show flitsr | awk '/^Version/{print $2}'))"
