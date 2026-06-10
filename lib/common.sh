# Sourced by every CVE script. Defines $REPO_ROOT, puts ./bin on PATH,
# and provides cluster up/down + readiness helpers.

# shellcheck shell=bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$REPO_ROOT/bin:$PATH"
export KIND_CLUSTER="cve-lab"

log()  { printf '\033[1;34m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

cluster_exists() {
  kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"
}

cluster_up() {
  if cluster_exists; then
    log "kind cluster '$KIND_CLUSTER' already exists"
  else
    log "creating kind cluster '$KIND_CLUSTER'"
    kind create cluster --config "$REPO_ROOT/cluster/kind-config.yaml"
  fi
  kubectl cluster-info --context "kind-${KIND_CLUSTER}" >/dev/null
}

cluster_down() {
  if cluster_exists; then
    log "deleting kind cluster '$KIND_CLUSTER'"
    kind delete cluster --name "$KIND_CLUSTER"
  fi
}

wait_rollout() {
  # wait_rollout <namespace> <kind/name>
  kubectl -n "$1" rollout status "$2" --timeout=120s
}
