#!/usr/bin/env bash
# prepare.sh — set up the OpenHands APR adapter in a private venv.
#
#   framework/tools/openhands/.venv/   python >= 3.12 + openhands-sdk + openhands-tools (PyPI, pinned)
#
# At run time the agent needs
#   * an LLM endpoint — any model litellm supports; for an OpenAI-compatible server
#       LLM_BASE_URL (or OPENAI_BASE_URL)   e.g. http://127.0.0.1:8080/v1 (llama.cpp / vLLM)
#       LLM_API_KEY  (or OPENAI_API_KEY)    any non-empty string for local servers
#       LLM_MODEL                           litellm model name, e.g. openai/<served-name>,
#                                           anthropic/claude-... (default: the first model
#                                           the base-url server lists, as openai/<name>)
#   * docker and the bug's buggy image (cvebench compile) — the sandbox.
#
# Requirements: python3 >= 3.12 with venv (or uv), network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"; PY="${PYTHON:-python3}"

if [[ ! -x "$VENV/bin/python" ]]; then
  if command -v "$PY" >/dev/null && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "[+] creating venv $VENV ($("$PY" --version))"
    "$PY" -m venv "$VENV" || { echo "[x] '$PY -m venv' failed (apt install python3-venv)" >&2; exit 1; }
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
  elif command -v uv >/dev/null; then
    echo "[+] no python3 >= 3.12 on PATH; creating venv with uv-managed Python 3.12"
    uv venv --quiet --python 3.12 --seed "$VENV"
  else
    echo "[x] need python3 >= 3.12 (or uv: https://docs.astral.sh/uv/)" >&2; exit 1
  fi
fi
echo "[+] installing $(grep -v '^#' "$HERE/requirements.txt" | tr '\n' ' ')"
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
OPENHANDS_SUPPRESS_BANNER=1 "$VENV/bin/python" -c '
import importlib.metadata as m
from openhands.tools.terminal import TerminalTool
from openhands.tools.file_editor import FileEditorTool
print("[+] openhands-sdk", m.version("openhands-sdk"), "/ openhands-tools", m.version("openhands-tools"))'
command -v docker >/dev/null || echo "[!] docker not found — the agent sandbox needs it" >&2
echo "[+] openhands ready: $VENV  (set LLM_BASE_URL / LLM_API_KEY [/ LLM_MODEL] before 'cvebench apr -t openhands')"
