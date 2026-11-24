#!/usr/bin/env bash
# prepare.sh — install FLITSR into a private venv next to this script.
#
# FLITSR (Callaghan & Fischer, ISSTA 2023) is a pure-Python SBFL/multi-fault
# localization tool: https://github.com/DCallaz/flitsr . Nothing is installed
# system-wide; everything lives in framework/tools/flitsr/.venv (git-ignored): the
# CPython pinned in versions.env and exactly the packages of requirements.lock.
#
# Requirements: network (uv, the interpreter and the packages are fetched by framework/lib/pyenv.sh).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../lib/pyenv.sh"
VENV="$HERE/.venv"

pyenv_ensure "$HERE"
"$VENV/bin/flitsr" --help >/dev/null || { echo "[x] flitsr does not run" >&2; exit 1; }
echo "[+] flitsr ready: $VENV/bin/flitsr ($("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("flitsr"))'))"
