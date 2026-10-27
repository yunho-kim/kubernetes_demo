#!/usr/bin/env bash
# prepare.sh — set up the Darjeeling APR adapter.
#
#   framework/tools/darjeeling/.venv/   python 3.11–3.13 + darjeeling (pinned git commit) + kaskara 0.2.1
#   docker image christimperley/kaskara:cpp + docker volume kaskara-clang
#       Kaskara's clang backend (Darjeeling indexes C statements with it), built from the
#       Dockerfile that ships inside the kaskara package (FROM christimperley/llvm18,
#       ~1.5 GB download + a C++ build, one-off). Documented exception to the "no
#       dedicated containers" rule: the backend is only distributed as a Docker build, and
#       Darjeeling mounts it as a volume into the program container (docs/INTEGRATION.md 6).
#
# Requirements: python3.11–3.13 (or uv), git, docker, network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"
VENV="$HERE/.venv"

command -v git >/dev/null || { echo "[x] git not found" >&2; exit 1; }
command -v docker >/dev/null || { echo "[x] docker not found" >&2; exit 1; }
if [[ ! -x "$VENV/bin/python" ]]; then
  PY=""
  for c in "${PYTHON:-}" python3.12 python3.13 python3.11; do
    [[ -n "$c" ]] && command -v "$c" >/dev/null && PY="$c" && break
  done
  if [[ -n "$PY" ]]; then
    echo "[+] creating venv $VENV with $PY ($("$PY" --version))"
    "$PY" -m venv "$VENV" || { echo "[x] '$PY -m venv' failed (apt install python3-venv)" >&2; exit 1; }
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
  elif command -v uv >/dev/null; then
    echo "[+] no python3.11–3.13 on PATH; creating venv with uv-managed Python 3.12"
    uv venv --quiet --python 3.12 --seed "$VENV"
  else
    echo "[x] need python3.11–3.13 (or uv: https://docs.astral.sh/uv/)" >&2; exit 1
  fi
fi
echo "[+] installing darjeeling @ ${DARJEELING_COMMIT:0:8} + kaskara @ ${KASKARA_COMMIT:0:8} (+ bugzoo, dockerblade)"
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
# the pinned kaskara commit and the PyPI release share the version "0.2.1": make sure
# the commit is what is installed
"$VENV/bin/python" -m pip install --quiet --no-deps --force-reinstall \
  "kaskara @ git+https://github.com/ChrisTimperley/Kaskara@${KASKARA_COMMIT}"
"$VENV/bin/darjeeling" --version 2>/dev/null | sed 's/^/[+] darjeeling /' || { echo "[x] darjeeling CLI broken" >&2; exit 1; }

if docker image inspect "$KASKARA_IMAGE" >/dev/null 2>&1; then
  echo "[+] $KASKARA_IMAGE present"
else
  BACKEND="$("$VENV/bin/python" -W ignore -c 'import os, kaskara.clang as k; print(os.path.join(os.path.dirname(k.__file__), "backend"))')"
  echo "[+] building $KASKARA_IMAGE from $BACKEND (one-off; pulls christimperley/llvm18)"
  docker build -t "$KASKARA_IMAGE" "$BACKEND"
fi
# kaskara's own post-install: (re)tags the image (build cache) and fills the
# kaskara-clang volume from it
echo "[+] kaskara post-install (docker volume kaskara-clang)"
"$VENV/bin/python" -W ignore -c 'from kaskara.post_install import post_install; post_install()'
echo "[+] darjeeling ready: $VENV"
