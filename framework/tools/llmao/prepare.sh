#!/usr/bin/env bash
# prepare.sh — set up LLMAO (Yang et al., ICSE 2024) in a private venv.
#
#   framework/tools/llmao/.venv/       CPython (PYTHON_VERSION in versions.env) + torch +
#                                      transformers 4.x, exactly requirements.lock
#   framework/tools/llmao/upstream/    squaresLab/LLMAO at a pinned commit
#                                      (adapter checkpoints devign_{350M,6B,16B} are in the repo)
# The CodeGen backbone (Salesforce/codegen-<size>-multi) is fetched from the
# Hugging Face hub on first use into the venv-local cache (.venv/hf-cache, kept when
# framework/lib/pyenv.sh rebuilds the venv).
#
# Requirements: git and network. A CUDA GPU is strongly recommended (350M: ~3 GB VRAM,
# 6B: ~15 GB, 16B: ~38 GB; the locked torch is a CUDA 13 build, so an NVIDIA driver
# that supports CUDA 13); CPU works for 350M.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"
source "$HERE/../../lib/pyenv.sh"
VENV="$HERE/.venv"

command -v git >/dev/null || { echo "[x] git not found" >&2; exit 1; }
pyenv_ensure "$HERE"

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
echo "[+] llmao ready: $VENV (python $("$VENV/bin/python" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])'), torch $("$VENV/bin/python" -c 'import torch;print(torch.__version__)'), cuda=$("$VENV/bin/python" -c 'import torch;print(torch.cuda.is_available())'))"
