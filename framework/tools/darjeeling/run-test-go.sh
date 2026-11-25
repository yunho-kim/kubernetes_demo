#!/usr/bin/env bash
# /cvebench/run-test <pkg dir> <Test[/subtest...]> [timeout-s] — one Darjeeling "shell" test on a
# Go bug: run the package's test binary (built by /cvebench/build) on that one leaf test.
# exit 0 = pass. A pattern that matches no test counts as a failure.
pkg="$1"; name="$2"; to="${3:-60}"
cd "$K8S_SRC/$pkg" || exit 2
bin="/tmp/cvebench-bin/$(printf '%s' "$pkg" | tr / _).test"
[ -x "$bin" ] || exit 2
# -test.run splits on '/' and matches each level separately (as framework/projects/kubernetes/build/gosuite.py)
pat=$(python3 -c 'import re, sys; print("/".join("^" + re.sub(r"([\\.+*?()|\[\]{}^$])", r"\\\1", p) + "$" for p in sys.argv[1].split("/")))' "$name")
timeout -s KILL "$to" "$bin" -test.run "$pat" -test.count=1 -test.v >/tmp/t.log 2>&1
rc=$?
# passing means this very test reported PASS (or SKIP), not just that its parent ran
if [ $rc -eq 0 ]; then
  grep -qF -e "--- PASS: $name (" -e "--- SKIP: $name (" /tmp/t.log || exit 1
fi
exit $rc
