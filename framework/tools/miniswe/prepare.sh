#!/usr/bin/env bash
# prepare.sh — set up the mini-swe-agent APR adapter in a private venv.
#
#   framework/tools/miniswe/.venv/   CPython (PYTHON_VERSION in versions.env) + mini-swe-agent
#                                    (PyPI, pinned) + litellm, exactly requirements.lock
#
# At run time the agent needs
#   * an LLM endpoint — any model litellm supports; for an OpenAI-compatible server
#       OPENAI_BASE_URL   e.g. http://127.0.0.1:8080/v1 (llama.cpp / vLLM)
#       OPENAI_API_KEY    the key (any non-empty string for local servers)
#       MSWEA_MODEL       litellm model name, e.g. openai/<served-name>,
#                         anthropic/claude-..., (default: the first model the
#                         OPENAI_BASE_URL server lists, as openai/<name>)
#   * docker and the bug's buggy image (cvebench compile), which is the sandbox
#     the agent's commands run in.
#
# Requirements: network (uv, the interpreter and the packages are fetched by framework/lib/pyenv.sh).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../lib/pyenv.sh"
VENV="$HERE/.venv"

pyenv_ensure "$HERE"
MSWEA_SILENT_STARTUP=1 "$VENV/bin/python" -c 'import minisweagent, litellm; print("[+] mini-swe-agent", minisweagent.__version__)'
command -v docker >/dev/null || echo "[!] docker not found — the agent sandbox needs it" >&2
echo "[+] miniswe ready: $VENV  (set OPENAI_BASE_URL / OPENAI_API_KEY [/ MSWEA_MODEL] before 'cvebench apr -t miniswe')"
