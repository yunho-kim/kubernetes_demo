# projects/kubernetes/project.sh — Kubernetes (Go). Sourced by lib/cvebench.sh (load_project).
# Release-tag archives from GitHub; build/Dockerfile.minpair builds the minimal pair on
# golang:<GO_VERSION> (tests/<bid>/build.env) with the bug's binaries and test binaries
# compiled. Tests are Go tests run one leaf at a time by build/gosuite.py: a test id is
# "<package dir>::<Test>[/<subtest>...]", the suite is tests/<bid>/packages.txt, the
# declared triggers are tests/<bid>/trigger.txt (upstream regression tests, or crafted
# ones under tests/<bid>/overlay/), coverage is `go test -coverpkg` (tests/<bid>/coverpkg.txt).
# shellcheck shell=bash

P_DIFF_EXCLUDES=('.*' '_output' '*.test')

p_srcname() { printf 'kubernetes-%s' "$1"; }

p_fetch_src() { # <ver> <dest>   download (cached under results/.cache) + extract
  local d="$2" cache="$RESULTS_ROOT/.cache" tgz="kubernetes-$1.tar.gz"
  mkdir -p "$d" "$cache"
  [[ -s "$cache/$tgz" ]] || curl -fsSL -o "$cache/$tgz" "https://github.com/kubernetes/kubernetes/archive/refs/tags/v$1.tar.gz" \
    || { rm -f "$cache/$tgz"; die "download failed: $tgz"; }
  tar xzf "$cache/$tgz" -C "$d"
}

k8s_list() { grep -vE '^\s*#|^\s*$' "$1" | tr '\n' ' '; }

p_build() { # <variant> <tag> [candidate]
  local ctx t GO_VERSION BUILD_PKGS; ctx="$(proj_dir "$PID")"; t="$ctx/tests/$BID"
  source "$t/build.env"
  docker build --build-arg "GO_VERSION=$GO_VERSION" --build-arg "K8S_VERSION=$(bug_fixedver "$PID" "$BID")" \
    --build-arg "BUILD_PKGS=$BUILD_PKGS" --build-arg "TEST_PKGS=$(k8s_list "$t/packages.txt")" \
    --build-arg "VARIANT=$1" --build-arg "FIX_PATCH=patches/$BID.fix.patch" \
    ${3:+--build-arg "CANDIDATE_PATCH=$3"} -t "$2" -f "$ctx/build/Dockerfile.minpair" "$ctx"
}

# k8s_gosuite <image> <out-dir> <gosuite args...>   build/gosuite.py in a throwaway container
k8s_gosuite() {
  local img="$1" out="$2" ctx; shift 2; ctx="$(proj_dir "$PID")"
  mkdir -p "$out"
  docker run --rm --network none \
    -v "$ctx/build/gosuite.py:/cvebench/gosuite.py:ro" -v "$ctx/tests/$BID:/cvebench/tests:ro" \
    -v "$(cd "$out" && pwd):/out" "$img" \
    python3 /cvebench/gosuite.py --src "$(bug_srcdir "$PID" "$BID")" --tests /cvebench/tests \
      --out /out --owner "$(id -u):$(id -g)" "$@"
}

p_trigger_ids() { grep -vE '^\s*#|^\s*$' "$(proj_dir "$PID")/tests/$BID/trigger.txt"; }

p_trigger() { # <image>
  local tmp rc=0; tmp="$(mktemp -d)"
  p_trigger_ids > "$tmp/list.txt"
  k8s_gosuite "$1" "$tmp" outcome --list /out/list.txt 2>/dev/null || rc=$?
  [[ -s "$tmp/outcomes.txt" ]] && sed 's/^/    /' "$tmp/outcomes.txt" >&2
  [[ $rc -eq 0 && -s "$tmp/outcomes.txt" ]] && ! grep -qv ' pass ' "$tmp/outcomes.txt" || rc=1
  rm -rf "$tmp"; return $rc
}

p_coverage_suite() { # <results-dir>   enumerate on FIXED, outcomes on FIXED, coverage on BUGGY
  local R="$1" B F; B="$(bug_image "$PID" "$BID" buggy)"; F="$(bug_image "$PID" "$BID" fixed)"
  log "enumerating leaf tests on FIXED ($(k8s_list "$(proj_dir "$PID")/tests/$BID/packages.txt"))"
  k8s_gosuite "$F" "$R/fixed" list
  cp "$R/fixed/list.txt" "$R/buggy/list.txt"
  log "suite: $(wc -l < "$R/fixed/list.txt") tests"
  log "running suite on FIXED (outcomes)"
  k8s_gosuite "$F" "$R/fixed" outcome --list /out/list.txt
  log "running suite on BUGGY (coverage)"
  k8s_gosuite "$B" "$R/buggy" cov --list /out/list.txt
}

p_validate_run() { # <image> <out-dir> <relevant|full>
  local run="$2/run"; mkdir -p "$run"
  if [[ "$3" == full ]]; then k8s_gosuite "$1" "$run" list >&2
  else cp "$2/relevant.txt" "$run/list.txt"; fi
  k8s_gosuite "$1" "$run" outcome --list /out/list.txt >&2
  cat "$run/outcomes.txt"
}
