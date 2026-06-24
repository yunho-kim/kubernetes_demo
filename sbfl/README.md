# sbfl — a faithful Defects4J-style SBFL benchmark (C / HAProxy)

A Spectrum-Based Fault Localization benchmark built the way **Defects4J** is,
but targeting **C / HAProxy** instead of Java. Each CVE is a "bug" with a
minimal buggy↔fixed pair, the project's own test suite, exact ground-truth
lines, and standard SBFL artifacts.

## Faithfulness to Defects4J

| Defects4J | This benchmark |
| --- | --- |
| Minimal buggy↔fixed pair (differ by *only* the fix) | **buggy = fixed release with the upstream fix reverse-applied** (`patch -R`) — built from one source tree, so the only delta is the fix |
| Ground-truth faulty lines = the fixing diff | **`diff` of the buggy vs fixed source** of the modified file(s) — exact line numbers, no hand anchors (`gtdiff.py`); detects faults of omission |
| Project's own test suite; `trigger_tests` vs `relevant_tests` | **HAProxy's own `reg-tests/` run via `vtest`**; trigger = *fail-on-buggy ∧ pass-on-fixed* (auto-discovered, e.g. `normalize_uri.vtc`), passing = *pass-on-both*, the rest excluded |
| `coverage` → per-test statement coverage | **per-test line coverage** of a coverage-instrumented build, collected in kind pods |
| GZoltar `spectra`/`matrix`/`tests` | emitted verbatim (`gzoltar/`) |
| `framework/projects/<P>/` metadata | `projects/<CVE>/` with `commit-db`, `patches/`, `trigger_tests/`, `relevant_tests/`, `modified_classes/`, `loaded_classes/` |
| Suspiciousness + EXAM | Ochiai / Tarantula / Jaccard / DStar + EXAM, scored vs ground truth |

## Why a coverage build, and how coverage is flushed

The official `haproxy:X.Y.Z` images carry no instrumentation, so each bug builds
HAProxy from source with `gcc --coverage` (`-O0 -g3 -fno-inline`). reg-tests run
HAProxy under `vtest`, which starts and cleanly stops it per test — the clean
stop flushes `.gcda`, so no special handling is needed. (The infinite-loop CVE
is the exception: there a `gdb`-driven `__gcov_dump()` is used because HAProxy
never exits.)

## Layout

```
sbfl/
├── images/
│   ├── Dockerfile.minpair    # build FIXED release; reverse-apply fix for BUGGY; +vtest +gcovr +gdb
│   └── runsuite.sh           # in-pod: run each vtc, record outcome + per-test coverage
├── patches/<CVE>.fix.patch   # upstream fix (forward diff); reverse-applied to make buggy
├── cves/<CVE>/
│   ├── regtests.txt          # reg-test globs that form the suite
│   └── trigger.vtc           # optional crafted trigger (in addition to auto-discovered ones)
├── bin/
│   ├── sbfl                  # driver: run [CVE...] | summary | clean
│   ├── sbfl-coverage         # build minimal pair's coverage, classify, emit artifacts, rank
│   ├── gtdiff.py             # buggy↔fixed diff -> exact ground-truth lines
│   ├── assemble.py           # classify + emit GZoltar spectra/matrix/tests
│   └── sbfl-rank             # spectrum -> Ochiai/Tarantula/Jaccard/DStar + EXAM scoring
├── projects/<CVE>/           # Defects4J-style per-bug metadata
└── results/<CVE>/            # tests.json, gzoltar/, ground_truth.json, ranking.json, classification.json
```

## Build & run

```bash
# build the minimal-pair coverage images for a CVE (FIXED + BUGGY)
sbfl/images/build-minpair.sh CVE-2023-45539

sbfl/bin/sbfl run CVE-2023-45539   # coverage + classify + rank for one CVE
sbfl/bin/sbfl run                  # all CVEs, then a summary table
sbfl/bin/sbfl summary              # reprint fault-rank table
sbfl/bin/sbfl clean                # delete the 'sbfl' kind cluster
```

## Pipeline (per CVE)

1. **Minimal pair** — build FIXED from the release tarball; BUGGY = same tree with
   `patches/<CVE>.fix.patch` reverse-applied. Both coverage-instrumented.
2. **Ground truth** — diff buggy vs fixed for each file the patch touches → exact
   faulty/candidate lines (`gtdiff.py`).
3. **Suite** — run the `regtests.txt` globs (+ optional `trigger.vtc`) under `vtest`
   on both pods; classify trigger / passing / excluded.
4. **Coverage** — per-test line coverage on the buggy build (`gcovr`).
5. **Artifacts** — GZoltar `spectra`/`matrix`/`tests` + Defects4J metadata.
6. **Rank & score** — Ochiai/Tarantula/Jaccard/DStar, EXAM vs ground truth.

## Results

_(reference CVE first; table filled after the run — see `results/<CVE>/ranking.json`)_

Line granularity, Ochiai; `rank` = best–worst tie bounds over total instrumented
lines; EXAM = (rank−1)/lines. Ground-truth lines come from the buggy↔fixed diff,
snapped to the nearest executable (covered) line.

| CVE | fault file | tests (f/p) | lines | fault rank | EXAM | character |
| --- | --- | --- | --- | --- | --- | --- |
| CVE-2022-0711 | `src/http_ana.c` | 1 / 19 | 19716 | 1–86 | 0.0000 | **easy** — the cookie loop is entered only by the trigger (fail-only) |
| CVE-2023-45539 | `src/h1.c` | 2 / 36 | 27188 | 673–719 | 0.0247 | **medium** — missing `#` check; partly on hot URI-parse lines |
| CVE-2021-40346 | `include/haproxy/htx.h` | 1 / 19 | 19465 | 3405–8049 | 0.1749 | **hard** — overflow line runs on every header of every request |

Trigger tests are auto-discovered (fail-on-buggy ∧ pass-on-fixed): for 45539
HAProxy's own `normalize_uri.vtc` is one (plus a crafted `trigger.vtc`); 40346 and
0711 use crafted triggers (a 270-byte header name; a `Set-Cookie2` backend). The
spread is the point — SBFL localizes a fault well only when failing and passing
tests cover it differently, so the infinite-loop bug tops the ranking while the
always-executed overflow line is buried. Per-CVE detail (triggers, relevant/loaded
tests, exclusions) is in `results/<CVE>/classification.json`; raw vs snapped ground
truth in `results/<CVE>/ground_truth*.json`.

Because the suite is now HAProxy's real reg-tests (dozens of diverse passing
tests), the spectrum is far richer than a handful of hand-written requests, so
suspiciousness ties shrink and the ranking is meaningful — the point of using
the project's own suite, exactly as Defects4J does.
