#!/bin/bash
# in-container.sh — runs inside cvebench-fangelix (FAngelix image + VTest), /fa mounted.
#
#   /fa/src/          buggy HAProxy tree (clean; FAngelix copies it)
#   /fa/tests.txt     "<test-id> <vtc path>" for every trigger and relevant test
#   /fa/files.txt     the files to repair, in order (src/....c)
#   /fa/triggers.txt  the trigger test ids (the failing tests)
#   /fa/oracle        Angelix test script: oracle <test-id> -> exit 0 iff the reg-test passes
#   /fa/build.sh      the build command (one word: Angelix runs `bear <build>`, and the Bear
#                     2.1.4 in the image passes only the first word of a command to the shell)
# Environment: TIME_LIMIT (s, whole run), TEST_TIMEOUT (s), DEFECTS, ALL_FILES (1: keep going
# after a patch), ANGELIX_EXTRA (extra angelix options), HOST_UID/HOST_GID (chown at the end).
# Output: /fa/pre.txt (each test's outcome on a plain build of the buggy tree),
#         /fa/work/<n>/ (one Angelix run per file; *.patch when it found one),
#         /fa/runs.txt  ("<n> <file> <status> <seconds>").
# (no set -u: /fangelix/activate reads unset variables)
cleanup() { chown -R "${HOST_UID:-0}:${HOST_GID:-0}" /fa 2>/dev/null; }
trap cleanup EXIT
source /fangelix/activate >/dev/null 2>&1
start=$(date +%s)
: > /fa/runs.txt

# 1. plain build of the buggy tree + every test once: Angelix needs passing tests to pass and
#    failing tests to fail on the program as it builds it.
cp -a /fa/src /tmp/plain && cd /tmp/plain
if ! CC=gcc /fa/build.sh > /fa/plain-build.log 2>&1; then echo "plain build failed" > /fa/pre.txt; exit 3; fi
: > /fa/pre.txt
while read -r id vtc; do
  (cd /tmp/plain && /fa/oracle "$id") && echo "$id pass" >> /fa/pre.txt || echo "$id fail" >> /fa/pre.txt
done < /fa/tests.txt
cd / && rm -rf /tmp/plain
# negative tests = the triggers that fail; positive = the other tests that pass (a relevant
# test failing here — flaky, or a config this build rejects — cannot constrain the repair)
negs=$(while read -r t; do grep -qx "$t fail" /fa/pre.txt && echo "$t"; done < /fa/triggers.txt)
pos=$(awk '$2=="pass"{print $1}' /fa/pre.txt | grep -v -x -F -f /fa/triggers.txt)
if [ -z "$negs" ]; then echo "no trigger test fails on the plain build" >> /fa/pre.txt; exit 4; fi

# 2. one Angelix run per file, within the remaining time
n=0
while read -r f; do
  n=$((n + 1))
  left=$(( TIME_LIMIT - ($(date +%s) - start) ))
  [ "$left" -lt 120 ] && { echo "$n $f skipped-no-time 0" >> /fa/runs.txt; continue; }
  mkdir -p "/fa/work/$n" && cd "/fa/work/$n"
  t0=$(date +%s)
  timeout -s KILL $((left + 60)) angelix /fa/src "$f" /fa/oracle $negs $pos \
      --use-gcc --build /fa/build.sh --defect $DEFECTS \
      --timeout "$left" --test-timeout "$TEST_TIMEOUT" $ANGELIX_EXTRA > angelix.log 2>&1
  rc=$?
  dt=$(( $(date +%s) - t0 ))
  if ls ./*.patch >/dev/null 2>&1; then st=patch          # Angelix prints SUCCESS
  elif grep -qx "TIMEOUT" angelix.log || [ $rc -eq 137 ]; then st=timeout
  elif grep -qx "FAIL" angelix.log; then st=no-patch
  else st="error-rc$rc"; fi
  echo "$n $f $st $dt" >> /fa/runs.txt
  [ -d .angelix ] && { cp .angelix/statistics.json statistics.json 2>/dev/null; rm -rf .angelix; }
  [ "$st" = patch ] && [ "${ALL_FILES:-0}" != 1 ] && break
done < /fa/files.txt
exit 0
