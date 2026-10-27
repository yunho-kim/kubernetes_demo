#!/usr/bin/env bash
# /cvebench/run-test <vtc> [timeout-s] — one Darjeeling "shell" test: exit 0 = pass.
# Runs the tree's own build ($HAPROXY_SRC/haproxy) under vtest; vtest's skip (77) counts as pass.
ulimit -n 65536 2>/dev/null || ulimit -n 4096 2>/dev/null || true
export GCOV_PREFIX=/tmp/cvebench-gcov
cd "$HAPROXY_SRC" || exit 2
HAPROXY_PROGRAM="$HAPROXY_SRC/haproxy" timeout -s KILL "${2:-60}" vtest "$1" >/tmp/v.log 2>&1
rc=$?
[ $rc -eq 77 ] && exit 0
exit $rc
