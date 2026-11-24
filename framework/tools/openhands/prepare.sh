#!/usr/bin/env bash
# prepare.sh — set up the OpenHands APR adapter in a private venv.
#
#   framework/tools/openhands/.venv/   CPython (PYTHON_VERSION in versions.env; the SDK needs
#                                      >= 3.12) + openhands-sdk + openhands-tools (PyPI, pinned),
#                                      exactly requirements.lock
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
# Requirements: network (uv, the interpreter and the packages are fetched by framework/lib/pyenv.sh).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../lib/pyenv.sh"
VENV="$HERE/.venv"

pyenv_ensure "$HERE"
OPENHANDS_SUPPRESS_BANNER=1 "$VENV/bin/python" -c '
import importlib.metadata as m
from openhands.tools.terminal import TerminalTool
from openhands.tools.file_editor import FileEditorTool
print("[+] openhands-sdk", m.version("openhands-sdk"), "/ openhands-tools", m.version("openhands-tools"))'
command -v docker >/dev/null || echo "[!] docker not found — the agent sandbox needs it" >&2
echo "[+] openhands ready: $VENV  (set LLM_BASE_URL / LLM_API_KEY [/ LLM_MODEL] before 'cvebench apr -t openhands')"
