#!/bin/bash
# Coverage flush wrapper installed as /usr/local/bin/haproxy (vtest's
# HAPROXY_PROGRAM). HAProxy flushes gcov only on a graceful exit running atexit
# handlers; vtest stops it with SIGINT/SIGTERM (which skip the flush), and the
# CVE-2022-0711 loop self-aborts via the watchdog (SIGABRT) before any stop
# signal. So we run the real HAProxy as a child and dump coverage with gdb
# (__gcov_dump):
#   - on the stop signal (trap), for normal tests, then kill;
#   - periodically (if COV_TICK set), so a self-aborting hang is captured before
#     it dies. Fast tests finish before the first tick, so there's no interference.
/usr/local/bin/haproxy.real "$@" &
child=$!
dump()  { gdb -p "$child" -batch -ex 'call (void)__gcov_dump()' -ex detach -ex quit >/dev/null 2>&1; }
flush() { dump; kill -9 "$child" 2>/dev/null; exit 0; }
trap flush INT TERM USR1

if [ -n "${COV_TICK:-}" ]; then
    ( while kill -0 "$child" 2>/dev/null; do sleep "$COV_TICK"; dump; done ) &
    ticker=$!
fi
wait "$child"
[ -n "${ticker:-}" ] && kill "$ticker" 2>/dev/null
