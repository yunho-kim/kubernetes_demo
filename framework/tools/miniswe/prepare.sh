#!/usr/bin/env bash
# prepare.sh — set up the mini-swe-agent APR adapter in a private venv.
#
#   framework/tools/miniswe/.venv/   python + mini-swe-agent (PyPI, pinned) + litellm
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
# Requirements: python3 >= 3.10 with venv (or uv), network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"; PY="${PYTHON:-python3}"

if [[ ! -x "$VENV/bin/python" ]]; then
  if command -v "$PY" >/dev/null && "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo "[+] creating venv $VENV ($("$PY" --version))"
    "$PY" -m venv "$VENV" || { echo "[x] '$PY -m venv' failed (apt install python3-venv)" >&2; exit 1; }
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
  elif command -v uv >/dev/null; then
    echo "[+] no python3 >= 3.10 on PATH; creating venv with uv-managed Python 3.12"
    uv venv --quiet --python 3.12 --seed "$VENV"
  else
    echo "[x] need python3 >= 3.10 (or uv: https://docs.astral.sh/uv/)" >&2; exit 1
  fi
fi
echo "[+] installing $(grep -v '^#' "$HERE/requirements.txt" | tr '\n' ' ')"
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
MSWEA_SILENT_STARTUP=1 "$VENV/bin/python" -c 'import minisweagent, litellm; print("[+] mini-swe-agent", minisweagent.__version__)'
command -v docker >/dev/null || echo "[!] docker not found — the agent sandbox needs it" >&2
echo "[+] miniswe ready: $VENV  (set OPENAI_BASE_URL / OPENAI_API_KEY [/ MSWEA_MODEL] before 'cvebench apr -t miniswe')"
