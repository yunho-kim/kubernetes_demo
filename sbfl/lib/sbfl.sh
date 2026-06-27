# Sourced by the SBFL harness. Self-contained (own kind cluster) so it never
# collides with the demo lab's cve-lab cluster.
# shellcheck shell=bash
set -euo pipefail

SBFL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$SBFL_ROOT/.." && pwd)"
export PATH="$REPO_ROOT/bin:$PATH"
export KIND_CLUSTER="${KIND_CLUSTER:-sbfl}"
KCTX="kind-${KIND_CLUSTER}"

log()  { printf '\033[1;34m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

k() { kubectl --context "$KCTX" "$@"; }

cluster_exists() { kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; }

cluster_up() {
  if cluster_exists; then log "kind cluster '$KIND_CLUSTER' exists"; else
    log "creating kind cluster '$KIND_CLUSTER'"; kind create cluster --name "$KIND_CLUSTER"
  fi
  k cluster-info >/dev/null
}

cluster_down() { cluster_exists && kind delete cluster --name "$KIND_CLUSTER" || true; }

# version string (e.g. 2.8.1) from cves/<CVE>/cvebench.properties vulnerable.image
cve_version() {
  local pid="$1" line
  line="$(grep -E '^vulnerable\.image=' "$REPO_ROOT/cves/$pid/cvebench.properties")"
  printf '%s' "${line#vulnerable.image=haproxy:}"
}
# fault file from classes.modified (first entry)
cve_fault_file() {
  local pid="$1" line
  line="$(grep -E '^classes\.modified=' "$REPO_ROOT/cves/$pid/cvebench.properties")"
  printf '%s' "${line#classes.modified=}" | cut -d';' -f1
}
# fixed release version (e.g. 2.8.2) — the minimal pair is built from this
cve_fixed_version() {
  local pid="$1" line
  line="$(grep -E '^fixed\.image=' "$REPO_ROOT/cves/$pid/cvebench.properties")"
  printf '%s' "${line#fixed.image=haproxy:}"
}
