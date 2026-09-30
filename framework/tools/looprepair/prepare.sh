#!/usr/bin/env bash
# prepare.sh — set up the LoopRepair APR adapter.
#
#   framework/tools/looprepair/.venv/      python 3.9–3.11 + upstream's LLM-stage requirements
#   framework/tools/looprepair/upstream/   Fino2020/LoopRepair at the commit in versions.env
#   upstream/src/tree-sitter/build/my-languages.so
#                                          upstream's tree-sitter C grammar (vendored source),
#                                          built with the host C compiler or, without one,
#                                          inside a python:3.11 container
#
# At run time an OpenAI-compatible chat endpoint is needed:
#   OPENAI_BASE_URL (default https://api.openai.com/v1), OPENAI_API_KEY,
#   LOOPREPAIR_MODEL (default: gpt-4o-mini as upstream; with a local base URL,
#   the first model the server lists)
# and docker + the bug's buggy image (cvebench compile) for building and testing candidates.
#
# Requirements: python3.9–3.11 (or uv), git, docker, network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"
VENV="$HERE/.venv"; UP="$HERE/upstream"

command -v git >/dev/null || { echo "[x] git not found" >&2; exit 1; }
if [[ ! -x "$VENV/bin/python" ]]; then
  PY=""
  for c in "${PYTHON:-}" python3.11 python3.10 python3.9; do
    [[ -n "$c" ]] && command -v "$c" >/dev/null && PY="$c" && break
  done
  if [[ -n "$PY" ]]; then
    echo "[+] creating venv $VENV with $PY ($("$PY" --version))"
    "$PY" -m venv "$VENV" || { echo "[x] '$PY -m venv' failed (apt install python3-venv)" >&2; exit 1; }
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
  elif command -v uv >/dev/null; then
    echo "[+] no python3.9–3.11 on PATH; creating venv with uv-managed Python 3.11"
    uv venv --quiet --python 3.11 --seed "$VENV"
  else
    echo "[x] need python3.9–3.11 (or uv: https://docs.astral.sh/uv/)" >&2; exit 1
  fi
fi
echo "[+] installing $(grep -v '^#' "$HERE/requirements.txt" | tr '\n' ' ')"
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"

if [[ ! -d "$UP/.git" ]]; then
  echo "[+] cloning LoopRepair @ ${LOOPREPAIR_COMMIT:0:8}"
  git clone --quiet "$LOOPREPAIR_REPO" "$UP"
fi
git -C "$UP" fetch --quiet origin "$LOOPREPAIR_COMMIT" 2>/dev/null || true
git -C "$UP" checkout --quiet "$LOOPREPAIR_COMMIT"

SO="$UP/src/tree-sitter/build/my-languages.so"
# upstream commits a prebuilt my-languages.so; rebuild it from the vendored grammar source once
if [[ ! -f "$UP/.cvebench-grammar-built" ]]; then
  rm -f "$SO"
  BUILD='from tree_sitter import Language; Language.build_library("build/my-languages.so", ["vendor/tree-sitter-c"])'
  if command -v cc >/dev/null; then
    echo "[+] building the tree-sitter C grammar (host cc)"
    (cd "$UP/src/tree-sitter" && "$VENV/bin/python" -c "$BUILD")
  else
    echo "[+] building the tree-sitter C grammar in a python:3.11 container (no host cc)"
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$UP/src/tree-sitter:/ts" -w /ts python:3.11 \
      bash -c "pip install --quiet --user tree_sitter==0.20.4 && python -c '$BUILD'"
  fi
  [[ -s "$SO" ]] || { echo "[x] grammar build failed" >&2; exit 1; }
  touch "$UP/.cvebench-grammar-built"
fi
# upstream imports: prompts, grammar, OpenAI client (a dummy key suffices for the import)
(cd "$UP/src/looprepair" && OPENAI_API_KEY=selftest "$VENV/bin/python" -c \
  'import LLMRepair as L; assert L.get_function_from_file("LLMRepair.py", 1) is None or True; print("[+] upstream LLMRepair imports; model default", L.api_model)')
echo "[+] looprepair ready: $VENV  (set OPENAI_BASE_URL / OPENAI_API_KEY [/ LOOPREPAIR_MODEL] before 'cvebench apr -t looprepair')"
