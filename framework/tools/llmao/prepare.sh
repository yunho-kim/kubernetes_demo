#!/usr/bin/env bash
# prepare.sh — set up LLMAO (Yang et al., ICSE 2024) in a private venv.
#
#   framework/tools/llmao/.venv/       python + torch + transformers (pinned)
#   framework/tools/llmao/upstream/    squaresLab/LLMAO at a pinned commit
#                                      (adapter checkpoints devign_{350M,6B,16B} are in the repo)
# The CodeGen backbone (Salesforce/codegen-<size>-multi) is fetched from the
# Hugging Face hub on first use into the venv-local cache (.venv/hf-cache).
#
# Requirements: a Python 3.10–3.13 interpreter (transformers 4.x / tokenizers
# wheels exist for these) — or `uv`, which downloads a managed 3.12 —, git and
# network. A CUDA GPU is strongly recommended (350M: ~3 GB VRAM, 6B: ~15 GB,
# 16B: ~38 GB); CPU works for 350M.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"
VENV="$HERE/.venv"

command -v git >/dev/null || { echo "[x] git not found" >&2; exit 1; }
if [[ ! -x "$VENV/bin/python" ]]; then
  PY=""
  for c in "${PYTHON:-}" python3.12 python3.11 python3.13 python3.10; do
    [[ -n "$c" ]] && command -v "$c" >/dev/null && PY="$c" && break
  done
  if [[ -n "$PY" ]]; then
    echo "[+] creating venv $VENV with $PY ($("$PY" --version))"
    "$PY" -m venv "$VENV" || { echo "[x] '$PY -m venv' failed (apt install python3-venv)" >&2; exit 1; }
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
  elif command -v uv >/dev/null; then
    echo "[+] no python3.10–3.13 on PATH; creating venv with uv-managed Python 3.12"
    uv venv --quiet --python 3.12 --seed "$VENV"
  else
    echo "[x] need python3.10–3.13 (or uv: https://docs.astral.sh/uv/) to install transformers 4.x" >&2; exit 1
  fi
fi
echo "[+] installing $(grep -v '^#' "$HERE/requirements.txt" | tr '\n' ' ')"
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"

if [[ ! -d "$HERE/upstream/.git" ]]; then
  echo "[+] cloning LLMAO @ $LLMAO_COMMIT"
  git clone --quiet "$LLMAO_REPO" "$HERE/upstream"
fi
git -C "$HERE/upstream" fetch --quiet origin "$LLMAO_COMMIT" 2>/dev/null || true
git -C "$HERE/upstream" checkout --quiet "$LLMAO_COMMIT"
for ck in devign_350M devign_6B devign_16B; do
  [[ -s "$HERE/upstream/model_checkpoints/$ck" ]] || { echo "[x] missing checkpoint $ck" >&2; exit 1; }
done

# smoke test: the adapter head loads with the pinned transformers
HF_HOME="$VENV/hf-cache" "$VENV/bin/python" "$HERE/llmao_infer.py" --selftest
echo "[+] llmao ready: $VENV (python $("$VENV/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])'), torch $("$VENV/bin/python" -c 'import torch;print(torch.__version__)'), cuda=$("$VENV/bin/python" -c 'import torch;print(torch.cuda.is_available())'))"
