#!/usr/bin/env bash
# runsuite.sh <src_dir> <list_file> <out_dir> [cov|outcome]
# Runs every vtc in <list_file> against the instrumented HAProxy INSIDE the pod.
# Writes <out_dir>/outcomes.txt ("<id> <pass|fail|skip> <vtc>") and, in cov mode,
# <out_dir>/coverage/<id>.json (compact per-test line coverage). vtest starts and
# cleanly stops HAProxy, which flushes .gcda — no gdb needed for reg-tests.
set -u
SRC="$1"; LIST="$2"; OUT="$3"; MODE="${4:-cov}"
cd "$SRC" || exit 2
mkdir -p "$OUT/coverage"
: > "$OUT/outcomes.txt"

# Take COV_TICK out of the inherited environment so it reaches ONLY the trigger
# (set inline below). Otherwise every test's HAProxy would tick and the gdb
# pauses would flake the reg-tests.
WANT_TICK="${COV_TICK:-}"
unset COV_TICK

while IFS= read -r vtc; do
  [ -z "$vtc" ] && continue
  id="$(printf '%s' "$vtc" | sed 's#reg-tests/##; s#[/.]#_#g')"
  find . -name '*.gcda' -delete
  # COV_TICK (periodic gcov dump for a self-aborting hang) is applied ONLY to the
  # crafted trigger — ticking normal reg-tests would pause them via gdb and flake
  # them to "fail", which would misclassify them as triggers.
  tick=""
  case "$vtc" in *_sbfl_trigger*) [ -n "$WANT_TICK" ] && tick="COV_TICK=$WANT_TICK" ;; esac
  env $tick HAPROXY_PROGRAM=/usr/local/bin/haproxy timeout 60 vtest "$vtc" >/tmp/v.log 2>&1
  case "$?" in 0) oc=pass ;; 77) oc=skip ;; *) oc=fail ;; esac
  echo "$id $oc $vtc" >> "$OUT/outcomes.txt"
  if [ "$MODE" = cov ] && [ "$oc" != skip ]; then
    gcovr -r "$SRC" --gcov-ignore-errors=no_working_dir_found --json "$SRC/.cov.tmp.json" 2>/dev/null
    python3 - "$SRC/.cov.tmp.json" "$OUT/coverage/$id.json" "$id" <<'PY'
import json, sys
src, out, name = sys.argv[1], sys.argv[2], sys.argv[3]
try: d = json.load(open(src))
except Exception: d = {"files": []}
cov = {}
for f in d.get("files", []):
    fp = f["file"].replace("\\", "/")
    if not (fp.startswith("src/") or fp.startswith("include/")): continue
    lines = sorted(l["line_number"] for l in f["lines"] if l.get("count", 0) > 0)
    if lines: cov[fp] = lines
json.dump({"test": name, "covered": cov}, open(out, "w"))
PY
    rm -f "$SRC/.cov.tmp.json"
  fi
done < "$LIST"
echo "runsuite: done ($(wc -l < "$OUT/outcomes.txt") tests, mode=$MODE)"
