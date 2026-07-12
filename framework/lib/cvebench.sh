# Sourced by framework/bin/cvebench. Resolves bugs from each project's commit-db
# and provides the kind-cluster helpers. Defects4J-style: a project (pid) has
# numbered bugs (bid); buggy = the fixed release with patches/<bid>.fix.patch
# reverse-applied (a minimal pair).
# shellcheck shell=bash
set -euo pipefail

FW_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # .../framework
REPO_ROOT="$(cd "$FW_ROOT/.." && pwd)"
export PATH="$REPO_ROOT/bin:$PATH"                            # vendored kind + kubectl
export KIND_CLUSTER="${KIND_CLUSTER:-cvebench}"
KCTX="kind-${KIND_CLUSTER}"
RESULTS_ROOT="$REPO_ROOT/results"

log()  { printf '\033[1;34m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

k() { kubectl --context "$KCTX" "$@"; }

# ---- project / bug resolution (commit-db) ----
proj_dir()  { printf '%s/projects/%s' "$FW_ROOT" "$1"; }
proj_db()   { printf '%s/projects/%s/commit-db' "$FW_ROOT" "$1"; }

list_pids() { find "$FW_ROOT/projects" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort; }
list_bids() { grep -vE '^\s*#|^\s*$' "$(proj_db "$1")" | cut -d, -f1; }

# db_field <pid> <bid> <col 1..7>   cols: bid,cve,fixed_version,fix_commit,cwe,cvss,summary
db_field() {
  local row; row="$(grep -E "^$2," "$(proj_db "$1")" || true)"
  [[ -n "$row" ]] || die "no bug $1-$2 in $(proj_db "$1")"
  printf '%s' "$row" | cut -d, -f"$3"
}
bug_cve()       { db_field "$1" "$2" 2; }
bug_fixedver()  { db_field "$1" "$2" 3; }
bug_fixcommit() { db_field "$1" "$2" 4; }
bug_cwe()       { db_field "$1" "$2" 5; }
bug_cvss()      { db_field "$1" "$2" 6; }
bug_summary()   { db_field "$1" "$2" 7; }

# files the fix touches (the modified "classes")
bug_modified_files() { sed 's#^+++ b/##' <(grep '^+++ b/' "$(proj_dir "$1")/patches/$2.fix.patch"); }

# image tag for a built variant, and the in-image source dir
bug_image()   { printf 'cvebench:%s-%s-%s' "$1" "$2" "$3"; }   # <pid> <bid> <buggy|fixed>
bug_srcdir()  { printf '/build/haproxy-%s' "$(bug_fixedver "$1" "$2")"; }

# ---- kind cluster ----
cluster_exists() { kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; }
cluster_up() {
  if cluster_exists; then log "kind cluster '$KIND_CLUSTER' exists"
  else log "creating kind cluster '$KIND_CLUSTER'"; kind create cluster --name "$KIND_CLUSTER"; fi
  k cluster-info >/dev/null
}
cluster_down() { cluster_exists && kind delete cluster --name "$KIND_CLUSTER" || true; }
