#!/usr/bin/env bash
# prepare.sh — set up the MagentLess APR adapter.
#
#   framework/tools/magentless/upstream/   MagentLess @ MAGENTLESS_COMMIT (versions.env)
#   framework/tools/magentless/.venv/      CPython (PYTHON_VERSION in versions.env) + its Python
#                                          dependencies, exactly requirements.lock
#
# At run time it needs
#   * an OpenAI-compatible chat endpoint: OPENAI_BASE_URL, OPENAI_API_KEY [, MAGENTLESS_MODEL;
#     default: the first model the server lists];
#   * for the embedding-retrieval stage, an OpenAI-compatible embeddings endpoint
#     (default Ollama, http://127.0.0.1:11434/v1, model nomic-embed-text: `ollama pull
#     nomic-embed-text`; see run --embed-url/--embed-model); `run --no-retrieval` skips it;
#   * docker and the bug's buggy image (the failing test is run there once to obtain its
#     output for the bug report).
# Requirements: git, network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"
source "$HERE/../../lib/pyenv.sh"
UP="$HERE/upstream"; VENV="$HERE/.venv"

if [[ ! -d "$UP/.git" ]]; then
  echo "[+] cloning $MAGENTLESS_REPO"
  git clone -q "$MAGENTLESS_REPO" "$UP"
fi
git -C "$UP" fetch -q origin 2>/dev/null || true
git -C "$UP" checkout -q "$MAGENTLESS_COMMIT"
echo "[+] MagentLess $(git -C "$UP" log -1 --format='%h %ci')"

pyenv_ensure "$HERE"
# llama-index downloads NLTK data (stopwords, punkt_tab) on first use; fetch it now into a
# private dir (NLTK_DATA; run uses the same one — NLTK refuses group/world-writable data dirs)
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cvebench-magentless"
mkdir -p "$CACHE" && chmod 700 "$CACHE"
(umask 077 && NLTK_DATA="$CACHE/nltk" "$VENV/bin/python" -W ignore -c 'from llama_index.core.utils import globals_helper as g
g.stopwords; g.punkt_tokenizer; print("[+] NLTK data in", g._nltk_data_dir)')
(cd "$UP" && SWEBENCH_LANG=c "$VENV/bin/python" -c '
import agentless.fl.localize, agentless.fl.retrieve, agentless.repair.repair, agentless.repair.rerank
from get_repo_structure.get_repo_structure import create_structure
print("[+] MagentLess modules import OK")')
command -v docker >/dev/null || echo "[!] docker not found — needed to run the failing test once" >&2
echo "[+] magentless ready (set OPENAI_BASE_URL / OPENAI_API_KEY before 'cvebench apr -t magentless')"
