#!/usr/bin/env bash
# validate.sh <src_dir> <relevant|full> — runs INSIDE a candidate image (cvebench validate).
# Expects bind-mounted: /runsuite.sh, /trigger.vtc, /globs.txt (reg-test globs),
# /relevant.txt (test ids = trigger + relevant tests). Prints outcomes.txt lines
# ("<id> <pass|fail|skip> <vtc>") on stdout.
set -u
# HAProxy sizes its fd table from RLIMIT_NOFILE; container runtimes may hand out
# a ~2^31 limit (kind/containerd), which makes haproxy allocate >60 GB and get
# OOM-killed. Cap the soft limit for everything vtest spawns.
ulimit -n 65536 2>/dev/null || ulimit -n 4096 2>/dev/null || true
SRC="$1"; MODE="${2:-relevant}"
cd "$SRC" || exit 2
cp /trigger.vtc reg-tests/_sbfl_trigger.vtc
GLOBS="$(tr '\n' ' ' < /globs.txt)"
# shellcheck disable=SC2086
ls $GLOBS reg-tests/_sbfl_trigger.vtc 2>/dev/null | sort -u | while IFS= read -r vtc; do
  id="$(printf '%s' "$vtc" | sed 's#reg-tests/##; s#[/.]#_#g')"
  if [ "$MODE" = full ] || grep -qx "$id" /relevant.txt; then echo "$vtc"; fi
done > /tmp/list.txt
bash /runsuite.sh "$SRC" /tmp/list.txt /tmp/out outcome >&2
cat /tmp/out/outcomes.txt
