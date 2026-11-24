#!/usr/bin/env bash
# prepare.sh — set up the FAngelix APR adapter.
#
# FAngelix ships as a Docker image (LLVM 2.9 + 3.7, KLEE, Z3, Java synthesizer on Ubuntu 18.04;
# building that toolchain from source is not practical), so like darjeeling this adapter is a
# documented exception to "no dedicated docker": prepare.sh pulls the image (~35 GB) and builds
# cvebench-fangelix:<tag> = the image + VTest (HAProxy's reg-test runner, at the commit the
# benchmark images use). The host side (run) needs only python3 and docker.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/versions.env"
TAG="cvebench-fangelix:fa-${FANGELIX_COMMIT}-vt-${VTEST_COMMIT}"
command -v docker >/dev/null || { echo "[x] docker is required" >&2; exit 1; }
if ! docker image inspect "$FANGELIX_IMAGE" >/dev/null 2>&1; then
  echo "[+] pulling $FANGELIX_IMAGE (~35 GB)"; docker pull "$FANGELIX_IMAGE"
fi
got="$(docker image inspect "$FANGELIX_IMAGE" --format '{{join .RepoDigests " "}}')"
[[ "$got" == *"$FANGELIX_IMAGE_DIGEST"* ]] || echo "[!] $FANGELIX_IMAGE digest differs from versions.env ($got)" >&2
if ! docker image inspect "$TAG" >/dev/null 2>&1; then
  ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT
  git clone -q "$VTEST_REPO" "$ctx/VTest" && git -C "$ctx/VTest" checkout -q "$VTEST_COMMIT"
  cp "$HERE/Dockerfile" "$ctx/"
  echo "[+] building $TAG"
  docker build -q --build-arg FANGELIX_IMAGE="$FANGELIX_IMAGE" -t "$TAG" "$ctx" >/dev/null
fi
docker run --rm "$TAG" bash -c 'source /fangelix/activate >/dev/null 2>&1 && command -v angelix vtest' >/dev/null \
  || { echo "[x] $TAG: angelix or vtest missing" >&2; exit 1; }
echo "$TAG" > "$HERE/.prepared"
echo "[+] fangelix ready ($TAG)"
