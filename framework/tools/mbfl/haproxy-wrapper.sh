#!/bin/bash
# Installed as /usr/local/bin/haproxy in the Mull image (real binary: haproxy.real).
# Mull enables a mutant when an environment variable named after it exists —
# names like "cxx_ne_to_eq:/build/…/h1.c:583:12:0:0" — but vtest starts HAProxy
# through /bin/sh -c, and POSIX shells (dash) drop variables with such names.
# So the mutant id travels in MULL_MUTANT (a valid name) and is turned into the
# real switch right before exec.
if [ -n "${MULL_MUTANT:-}" ]; then
  exec env "${MULL_MUTANT}=1" /usr/local/bin/haproxy.real "$@"
fi
exec /usr/local/bin/haproxy.real "$@"
