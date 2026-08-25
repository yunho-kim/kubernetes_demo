#!/usr/bin/env bash
# prepare.sh — set up the AutoFL adapter (Kang, An, Yoo, FSE 2024) in a private venv.
#
#   framework/tools/autofl/.venv/   python + openai client + tree-sitter (C parser)
#
# AutoFL itself is an LLM agent; the prompts/protocol of the upstream tool
# (coinse/autofl, commit in versions.env) are ported to C in autofl_agent.py, so
# nothing else is downloaded. At run time an OpenAI-compatible chat endpoint is
# needed — configure it with environment variables (see run --help):
#   OPENAI_BASE_URL   e.g. https://api.openai.com/v1 (default), http://127.0.0.1:8080/v1 (llama.cpp)
#   OPENAI_API_KEY    the key (any non-empty string for local servers)
#   AUTOFL_MODEL      model name (default gpt-4o-mini)
#
# Requirements: python3 >= 3.9 with venv (3.14 works), git not needed, network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"; PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null || { echo "[x] $PY not found" >&2; exit 1; }
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[+] creating venv $VENV ($("$PY" --version))"
  "$PY" -m venv "$VENV" || { echo "[x] 'python3 -m venv' failed (apt install python3-venv)" >&2; exit 1; }
fi
echo "[+] installing $(grep -v '^#' "$HERE/requirements.txt" | tr '\n' ' ')"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
"$VENV/bin/python" "$HERE/autofl_agent.py" --selftest
echo "[+] autofl ready: $VENV  (set OPENAI_BASE_URL / OPENAI_API_KEY / AUTOFL_MODEL before 'cvebench fl -t autofl')"
