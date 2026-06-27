#!/usr/bin/env bash
# build-minpair.sh <CVE-ID> [...]  — build coverage-instrumented FIXED + BUGGY
# images for each CVE. BUGGY = the FIXED release with the upstream fix
# reverse-applied (a true minimal pair). Needs sbfl/patches/<CVE>.fix.patch.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../lib/sbfl.sh"

CVES=("$@"); [[ ${#CVES[@]} -eq 0 ]] && CVES=(CVE-2021-40346 CVE-2022-0711 CVE-2023-45539)
for pid in "${CVES[@]}"; do
  fixver="$(cve_fixed_version "$pid")"
  patch="patches/$pid.fix.patch"
  [[ -f "$SBFL_ROOT/$patch" ]] || die "missing $SBFL_ROOT/$patch"
  EXTRA_MAKE=""
  [[ -f "$SBFL_ROOT/cves/$pid/build.env" ]] && source "$SBFL_ROOT/cves/$pid/build.env"
  for variant in fixed buggy; do
    tag="haproxy-sbfl:$pid-$variant"
    if [[ "${FORCE:-0}" != 1 ]] && docker image inspect "$tag" >/dev/null 2>&1; then
      log "$tag exists, skipping (FORCE=1 to rebuild)"; continue
    fi
    log "building $tag (haproxy $fixver, $variant${EXTRA_MAKE:+, $EXTRA_MAKE})"
    docker build \
      --build-arg "HAPROXY_VERSION=$fixver" \
      --build-arg "VARIANT=$variant" \
      --build-arg "FIX_PATCH=$patch" \
      --build-arg "EXTRA_MAKE=$EXTRA_MAKE" \
      -t "$tag" -f "$HERE/Dockerfile.minpair" "$SBFL_ROOT" >/dev/null \
      || die "build failed: $tag"
  done
done
log "done"; docker images | grep haproxy-sbfl | sort
