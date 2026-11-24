#!/usr/bin/env bash
# prepare.sh — set up the Darjeeling APR adapter.
#
#   framework/tools/darjeeling/.venv/   CPython (PYTHON_VERSION in versions.env) + darjeeling
#                                       (pinned git commit) + kaskara (pinned git commit, "0.2.1"),
#                                       exactly requirements.lock
#   docker image christimperley/kaskara:cpp + docker volume kaskara-clang
#       Kaskara's clang backend (Darjeeling indexes C statements with it), built from the
#       Dockerfile that ships inside the kaskara package (FROM christimperley/llvm18,
#       ~1.5 GB download + a C++ build, one-off). Documented exception to the "no
#       dedicated containers" rule: the backend is only distributed as a Docker build, and
#       Darjeeling mounts it as a volume into the program container (docs/INTEGRATION.md 6).
#
# Requirements: git (uv builds the two git pins), docker, network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"
source "$HERE/../../lib/pyenv.sh"
VENV="$HERE/.venv"

command -v git >/dev/null || { echo "[x] git not found" >&2; exit 1; }
command -v docker >/dev/null || { echo "[x] docker not found" >&2; exit 1; }
echo "[+] darjeeling @ ${DARJEELING_COMMIT:0:8} + kaskara @ ${KASKARA_COMMIT:0:8} (+ bugzoo, dockerblade)"
pyenv_ensure "$HERE"
# The pinned kaskara commit and the PyPI release share the version "0.2.1". requirements.in
# names the commit as a direct URL, so the lock and `uv pip sync` install it (pip needed a
# forced --no-deps reinstall for that); check that the commit is what is installed.
"$VENV/bin/python" - "$KASKARA_COMMIT" <<'PY' || { echo "[x] kaskara is not the pinned commit" >&2; exit 1; }
import json, sys
from importlib.metadata import distribution
d = json.loads(distribution("kaskara").read_text("direct_url.json") or "{}")
got = d.get("vcs_info", {}).get("commit_id")
print("[+] kaskara", got)
sys.exit(got != sys.argv[1])
PY
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
