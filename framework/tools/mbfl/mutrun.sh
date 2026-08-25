#!/usr/bin/env bash
# mutrun.sh <src_dir> <timeout_s> — runs INSIDE the Mull image (one container per mutant).
# The mutant to enable comes from MULL_MUTANT in the container environment
# (docker run -e MULL_MUTANT=<id>); the haproxy wrapper applies it. Unset = baseline.
# Reads reg-test paths on stdin, prints "<id> <pass|fail|skip|timeout> <sig> <vtc>" per
# test, where <sig> is a short hash of the failure signature (which expectation /
# assertion vtest reported) so that "fails differently" is observable, or "-".
set -u
# HAProxy sizes its fd table from RLIMIT_NOFILE; container runtimes may hand out
# a ~2^31 limit (kind/containerd), which makes haproxy allocate >60 GB and get
# OOM-killed. Cap the soft limit for everything vtest spawns.
ulimit -n 65536 2>/dev/null || ulimit -n 4096 2>/dev/null || true
SRC="$1"; T="${2:-30}"
cd "$SRC" || exit 2
while IFS= read -r vtc; do
  [ -z "$vtc" ] && continue
  id="$(printf '%s' "$vtc" | sed 's#reg-tests/##; s#[/.]#_#g')"
  HAPROXY_PROGRAM=/usr/local/bin/haproxy timeout -s KILL "$T" vtest "$vtc" >/tmp/v.log 2>&1
  case "$?" in 0) oc=pass ;; 77) oc=skip ;; 124|137) oc=timeout ;; *) oc=fail ;; esac
  sig="-"
  if [ "$oc" = fail ] || [ "$oc" = timeout ]; then
    # vtest reports each failed expectation as "---- <client> EXPECT resp.status (200) == "400" failed";
    # hash the messages (observed value included, temp paths removed).
    if [ "$oc" = timeout ]; then sig=timeout
    else
      sig="$(grep -E '^---- ' /tmp/v.log | sed -E 's/^---- +[^ ]+ +//; s#/tmp/vtc[^ ]*##g' | head -3 | md5sum | cut -c1-8)"
      grep -qE '^---- ' /tmp/v.log || sig="fail"
    fi
  fi
  echo "$id $oc $sig $vtc"
done
