#!/usr/bin/env bash
# prepare.sh — build the Mull base image used by the MBFL adapter.
#
# The MBFL tool itself (mutant selection, scheduling, kill matrix, Metallaxis /
# MUSE scoring) runs on the host in standard-library Python. What needs a
# toolchain is the *program under test*: HAProxy has to be compiled with clang
# and Mull's IR plugin so that every mutant is baked into one binary and can be
# switched on with an environment variable. That build happens in the same
# kind of image cvebench already uses for coverage — this script prepares the
# base layer (Debian + clang + Mull + vtest) once so per-bug builds are fast.
#
# Requirements on the machine: docker, network access (GitHub release + apt).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"           # LLVM, MULL_VERSION, MULL_LLVM_FULL, BASE_IMAGE

command -v docker >/dev/null || { echo "[x] docker not found" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "[x] docker daemon not reachable (is the user in the docker group?)" >&2; exit 1; }

if docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 && [[ "${1:-}" != "--rebuild" ]]; then
  echo "[+] base image $BASE_IMAGE already present (use --rebuild to force)"
else
  echo "[+] building $BASE_IMAGE (debian:trixie + clang-$LLVM + Mull $MULL_VERSION + vtest)"
  docker build --build-arg "LLVM=$LLVM" --build-arg "MULL_VERSION=$MULL_VERSION" \
    --build-arg "MULL_LLVM_FULL=$MULL_LLVM_FULL" -t "$BASE_IMAGE" -f "$HERE/Dockerfile.base" "$HERE"
fi
docker run --rm "$BASE_IMAGE" sh -c "clang-$LLVM --version | head -1; mull-runner-$LLVM --version; vtest -h 2>&1 | head -1 || true; ls /usr/lib/mull-ir-frontend-$LLVM"
mkdir -p "$HERE/.venv" && date > "$HERE/.venv/prepared"   # marker for `cvebench tools`
echo "[+] mbfl ready ($BASE_IMAGE)"
