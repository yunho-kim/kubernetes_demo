#!/usr/bin/env bash
# prepare.sh — set up the AutoFL adapter (Kang, An, Yoo, FSE 2024) in a private venv.
#
#   framework/tools/autofl/.venv/   CPython (PYTHON_VERSION in versions.env) + openai client +
#                                   tree-sitter (C and Go parsers), exactly requirements.lock
#
# AutoFL itself is an LLM agent; the prompts/protocol of the upstream tool
# (coinse/autofl, commit in versions.env) are ported to C and Go in autofl_agent.py, so
# nothing else is downloaded. At run time an OpenAI-compatible chat endpoint is
# needed — configure it with environment variables (see run --help):
#   OPENAI_BASE_URL   e.g. https://api.openai.com/v1 (default), http://127.0.0.1:8080/v1 (llama.cpp)
#   OPENAI_API_KEY    the key (any non-empty string for local servers)
#   AUTOFL_MODEL      model name (default gpt-4o-mini)
#
# Requirements: network (uv, the interpreter and the packages are fetched by framework/lib/pyenv.sh).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../../lib/pyenv.sh"
VENV="$HERE/.venv"

pyenv_ensure "$HERE"
"$VENV/bin/python" "$HERE/autofl_agent.py" --selftest
echo "[+] autofl ready: $VENV  (set OPENAI_BASE_URL / OPENAI_API_KEY / AUTOFL_MODEL before 'cvebench fl -t autofl')"
