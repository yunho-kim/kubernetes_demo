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
bug_srcdir()  { printf '/build/%s' "$(p_srcname "$(bug_fixedver "$1" "$2")")"; }

# ---- per-project module (Defects4J's framework/core/Project/<Name>.pm) ----
# projects/<pid>/project.sh defines how the project's sources are fetched, built
# into minimal-pair images and tested; PID / BID are set when its hooks run:
#   p_srcname <ver>                  source dir name inside a checkout / image
#   p_fetch_src <ver> <dest>         extract the release source into <dest>/<srcname>
#   p_build <variant> <tag> [cand]   docker build (cand: candidate patch, relative to the project dir)
#   p_trigger <image>                run the bug's trigger test(s); rc 0 = all pass
#   p_trigger_ids                    ids of the bug's crafted / declared trigger tests
#   p_coverage_suite <results-dir>   run the suite on both variants -> buggy/{outcomes.txt,coverage/}, fixed/outcomes.txt
#   p_validate_run <image> <out-dir> <relevant|full>   outcomes.txt lines on stdout
#                                    (<out-dir>/relevant.txt = trigger + relevant test ids)
#   P_DIFF_EXCLUDES=(...)            diff -x patterns for build products (cvebench diff)
load_project() {
  local f; f="$(proj_dir "$1")/project.sh"
  [[ -f "$f" ]] || die "no project module: $f"
  # shellcheck source=/dev/null
  source "$f"
}

# ---- kind cluster ----
cluster_exists() { kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; }
cluster_up() {
  if cluster_exists; then log "kind cluster '$KIND_CLUSTER' exists"
  else log "creating kind cluster '$KIND_CLUSTER'"; kind create cluster --name "$KIND_CLUSTER"; fi
  k cluster-info >/dev/null
}
cluster_down() { cluster_exists && kind delete cluster --name "$KIND_CLUSTER" || true; }
