# projects/haproxy/project.sh — HAProxy (C). Sourced by lib/cvebench.sh (load_project).
# Release tarballs from haproxy.org; build/Dockerfile.minpair builds a gcov-instrumented
# minimal pair with vtest; tests are HAProxy's reg-tests/*.vtc (tests/<bid>/regtests.txt
# globs) plus the crafted tests/<bid>/trigger.vtc. Coverage runs in kind pods.
# shellcheck shell=bash

P_DIFF_EXCLUDES=('*.o' '*.d' '*.a' '*.so' haproxy '.*')

p_srcname() { printf 'haproxy-%s' "$1"; }

p_fetch_src() { # <ver> <dest>   download (cached under results/.cache) + extract
  local v="$1" d="$2" br="${1%.*}" cache="$RESULTS_ROOT/.cache" tgz="haproxy-$1.tar.gz"
  mkdir -p "$d" "$cache"
  [[ -s "$cache/$tgz" ]] || wget -q -O "$cache/$tgz" "https://www.haproxy.org/download/${br}/src/$tgz" || die "download failed: $tgz"
  tar xzf "$cache/$tgz" -C "$d"
}

p_build() { # <variant> <tag> [candidate]
  local ctx; ctx="$(proj_dir "$PID")"
  [[ -f "$ctx/tests/$BID/build.env" ]] && source "$ctx/tests/$BID/build.env"
  docker build --build-arg "HAPROXY_VERSION=$(bug_fixedver "$PID" "$BID")" --build-arg "VARIANT=$1" \
    --build-arg "FIX_PATCH=patches/$BID.fix.patch" --build-arg "EXTRA_MAKE=${EXTRA_MAKE:-}" \
    ${3:+--build-arg "CANDIDATE_PATCH=$3"} -t "$2" -f "$ctx/build/Dockerfile.minpair" "$ctx"
}

p_trigger() { # <image>
  docker run --rm -i --cap-add=SYS_PTRACE "$1" bash -c \
    "cat > /t.vtc; cd $(bug_srcdir "$PID" "$BID") && HAPROXY_PROGRAM=/usr/local/bin/haproxy timeout 90 vtest /t.vtc >/dev/null 2>&1" \
    < "$(proj_dir "$PID")/tests/$BID/trigger.vtc"
}

p_trigger_ids() { echo _sbfl_trigger_vtc; }

p_coverage_suite() { # <results-dir>   runs the reg-tests via vtest in kind pods against the minimal pair
  local R="$1" PDIR TDIR SRC B F PB=cvebench-buggy PF=cvebench-fixed GLOBS COV_TICK=""
  PDIR="$(proj_dir "$PID")"; TDIR="$PDIR/tests/$BID"; SRC="$(bug_srcdir "$PID" "$BID")"
  B="$(bug_image "$PID" "$BID" buggy)"; F="$(bug_image "$PID" "$BID" fixed)"
  cluster_up
  log "loading images into kind"
  kind load docker-image "$B" --name "$KIND_CLUSTER" >/dev/null
  kind load docker-image "$F" --name "$KIND_CLUSTER" >/dev/null
  cp_in() { k exec -i "$1" -- sh -c "cat > $3" < "$2"; }
  deploy() { local pod="$1" img="$2" cap="${3:-}"
    k delete pod "$pod" --ignore-not-found --now >/dev/null 2>&1 || true
    k apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: { name: $pod }
spec:
  containers:
  - name: haproxy
    image: $img
    imagePullPolicy: Never
    command: ["sleep","infinity"]
$( [[ "$cap" == ptrace ]] && printf '    securityContext: { capabilities: { add: ["SYS_PTRACE"] } }' )
EOF
  }
  deploy "$PB" "$B" ptrace; deploy "$PF" "$F"
  k wait --for=condition=Ready "pod/$PB" "pod/$PF" --timeout=180s >/dev/null

  GLOBS="$(grep -vE '^\s*#|^\s*$' "$TDIR/regtests.txt" | tr '\n' ' ')"
  for pod in "$PB" "$PF"; do
    cp_in "$pod" "$PDIR/build/runsuite.sh" /runsuite.sh
    cp_in "$pod" "$TDIR/trigger.vtc" "$SRC/reg-tests/_sbfl_trigger.vtc"
    k exec "$pod" -- sh -c "cd $SRC && ls $GLOBS reg-tests/_sbfl_trigger.vtc 2>/dev/null" > "/tmp/list_$pod" || true
    cp_in "$pod" "/tmp/list_$pod" /tmp/list.txt
  done
  log "suite: $(wc -l < "/tmp/list_$PB") candidate tests"

  [[ -f "$TDIR/build.env" ]] && source "$TDIR/build.env"
  log "running suite on BUGGY (coverage${COV_TICK:+, COV_TICK=$COV_TICK})"
  k exec "$PB" -- env ${COV_TICK:+COV_TICK=$COV_TICK} bash /runsuite.sh "$SRC" /tmp/list.txt /tmp/out cov 2>&1 | tail -1
  log "running suite on FIXED (outcomes)"
  k exec "$PF" -- bash /runsuite.sh "$SRC" /tmp/list.txt /tmp/out outcome 2>&1 | tail -1
  kubectl --context "$KCTX" cp "$PB:/tmp/out/." "$R/buggy/" >/dev/null
  kubectl --context "$KCTX" cp "$PF:/tmp/out/." "$R/fixed/" >/dev/null
}

p_validate_run() { # <image> <out-dir> <relevant|full>
  local ctx out="$2"; ctx="$(proj_dir "$PID")"
  grep -vE '^\s*#|^\s*$' "$ctx/tests/$BID/regtests.txt" > "$out/globs.txt"
  docker run --rm --cap-add=SYS_PTRACE \
    -v "$ctx/build/runsuite.sh:/runsuite.sh:ro" -v "$ctx/build/validate.sh:/validate.sh:ro" \
    -v "$ctx/tests/$BID/trigger.vtc:/trigger.vtc:ro" -v "$out/globs.txt:/globs.txt:ro" -v "$out/relevant.txt:/relevant.txt:ro" \
    "$1" bash /validate.sh "$(bug_srcdir "$PID" "$BID")" "$3"
}
