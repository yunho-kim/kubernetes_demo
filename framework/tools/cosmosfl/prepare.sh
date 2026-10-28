#!/usr/bin/env bash
# prepare.sh [--pull] — set up the COSMosFL adapter (Cho, Kang, An, Yoo, LLM4Code 2025).
#
# COSMosFL runs AutoFL with several small models and combines their votes, so it reuses the
# AutoFL adapter (framework/tools/autofl: venv with the OpenAI client + tree-sitter-c); this
# runs that adapter's prepare.sh when its venv is missing and links it as .venv here.
# The member models are served at run time by an OpenAI-compatible endpoint, by default
# Ollama as upstream (http://127.0.0.1:11434/v1). --pull fetches the paper's four 4-bit
# models into Ollama (COSMOSFL_MODELS in versions.env; ~22 GB):
#   ollama pull llama3; ollama pull llama3.1; ollama pull mistral-nemo; ollama pull qwen2.5-coder
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOFL="$(cd "$HERE/../autofl" && pwd)"
source "$HERE/versions.env"

[[ -x "$AUTOFL/.venv/bin/python" ]] || "$AUTOFL/prepare.sh"
ln -sfn "$AUTOFL/.venv" "$HERE/.venv"
"$AUTOFL/.venv/bin/python" "$AUTOFL/autofl_agent.py" --selftest

if [[ "${1:-}" == --pull ]]; then
  command -v ollama >/dev/null || { echo "[x] --pull needs the ollama CLI (https://ollama.com)" >&2; exit 1; }
  for m in ${COSMOSFL_MODELS//,/ }; do echo "[+] ollama pull $m"; ollama pull "$m"; done
fi
echo "[+] cosmosfl ready (members: $COSMOSFL_MODELS; serve them, e.g. with Ollama, before 'cvebench fl -t cosmosfl')"
