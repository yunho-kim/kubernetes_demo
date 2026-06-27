# Kubernetes CVE Reproduction Lab

Self-contained environment for reproducing Kubernetes-ecosystem CVEs against a
local `kind` cluster. Each CVE lives in its own directory under `cves/` with
a uniform set of scripts so reproductions are deterministic and disposable.

## Prerequisites

- Docker (running, current user in the `docker` group)
- Bash, `curl`, `nc` (or `/dev/tcp` redirection — used by some exploits)

`kind` and `kubectl` are vendored into `./bin/` so the lab is self-contained.
Every CVE script sources `lib/common.sh`, which prepends `./bin/` to `PATH`.

## Layout

```
.
├── bin/                  # pinned kind + kubectl + cvebench harness
├── cluster/
│   └── kind-config.yaml  # single-node, NodePort 30080 mapped to host
├── lib/
│   └── common.sh         # cluster up/down, kubectl helpers
├── cves/
│   └── <CVE-ID>/
│       ├── README.md             # vuln summary, mechanism, expected output
│       ├── manifests/            # k8s YAML
│       ├── setup.sh              # bring up cluster + deploy vulnerable stack
│       ├── exploit.sh            # demonstrate the vulnerability
│       ├── verify-fix.sh         # patch to a fixed version, re-run probe
│       ├── cleanup.sh            # tear down the workloads (cluster optional)
│       └── cvebench.properties   # Defects4J-style metadata for the harness
└── sbfl/                 # faithful Defects4J-style SBFL benchmark (see sbfl/README.md)
    ├── images/           # minimal-pair coverage build + in-pod suite runner + gcov-flush wrapper
    ├── patches/          # upstream fix patches (reverse-applied to make the buggy build)
    ├── cves/<CVE-ID>/    # reg-test globs + crafted trigger.vtc + build.env
    ├── bin/              # sbfl CLI, coverage collector, gtdiff, assemble, ranking engine
    ├── projects/<CVE>/   # Defects4J-style metadata (commit-db, trigger/relevant tests, ...)
    └── results/<CVE>/    # tests.json, gzoltar/, ground_truth.json, ranking.json
```

## Reproducing a CVE

```bash
cd cves/CVE-2023-45539
./setup.sh         # creates kind cluster (if needed) + deploys vulnerable stack
./exploit.sh       # sends the malicious request, asserts misbehavior
./verify-fix.sh    # rolls forward to a patched version, asserts safe behavior
./cleanup.sh       # removes workloads (pass --cluster to also delete kind)
```

## Adding a new CVE

Copy an existing `cves/<CVE-ID>/` directory as a template. Keep the four-script
contract (`setup` / `exploit` / `verify-fix` / `cleanup`) so every CVE is
reproduced the same way. Add a `cvebench.properties` file so the harness can
checkout / test / export the CVE alongside the others.

## Available CVEs

| ID | Component | Class | Status |
| --- | --- | --- | --- |
| [CVE-2021-40346](cves/CVE-2021-40346/README.md) | HAProxy 2.0–2.4.3 | HTX header length overflow → HTTP smuggling | ready |
| [CVE-2022-0711](cves/CVE-2022-0711/README.md)   | HAProxy 2.2–2.5.1 | Infinite loop on `Set-Cookie2` → DoS | ready |
| [CVE-2023-45539](cves/CVE-2023-45539/README.md) | HAProxy < 2.8.2  | URI fragment misparse → backend confusion | ready |

## cvebench — Defects4J-style harness

`bin/cvebench` is a small CLI that drives the three reproductions through
the same checkout / setup / test / cleanup contract Defects4J uses, so the
CVEs can be consumed as a fault-localization / automated-patching
benchmark. Each `cves/<CVE-ID>/cvebench.properties` declares the vulnerable
image, the fixed image, the trigger script, and the upstream files modified
by the fix (`classes.modified` — the fault-localization ground truth).

```bash
./bin/cvebench pids                       # list CVE project IDs
./bin/cvebench info -p CVE-2022-0711      # show metadata for one CVE
./bin/cvebench export -p CVE-2022-0711 -q vulnerable.image

# Defects4J-style buggy / fixed workflow
BUGGY=/tmp/cvebench-buggy
FIXED=/tmp/cvebench-fixed

./bin/cvebench checkout -p CVE-2022-0711 -v buggy -w "$BUGGY"
./bin/cvebench setup    -w "$BUGGY"
./bin/cvebench test     -w "$BUGGY"             # rc=1 FAIL (CVE reproduced)
./bin/cvebench cleanup  -w "$BUGGY" --cluster   # recreate cluster between runs

./bin/cvebench checkout -p CVE-2022-0711 -v fixed -w "$FIXED"
./bin/cvebench setup    -w "$FIXED"
./bin/cvebench test     -w "$FIXED"             # rc=0 PASS (patched)
./bin/cvebench cleanup  -w "$FIXED" --cluster
```

**Always pass `--cluster` to `cleanup` between runs.** The buggy HAProxy
pod crash-loops during the exploit, which leaves stale kernel conntrack
entries that misroute traffic to the next pod even after the Service is
recreated. Tearing the kind cluster down (~30s) avoids the flakiness.

Test semantics (matches Defects4J intent):

| Workdir version | Trigger result        | `cvebench test` rc |
| --- | --- | --- |
| `buggy`         | CVE reproduces        | `1` (FAIL)         |
| `fixed`         | CVE does not reproduce| `0` (PASS)         |

The harness only operates on **one CVE at a time** — all three CVEs use the
same `default` namespace and the same `haproxy` Service, so they collide if
you try to setup two at once. Always `cvebench cleanup` between CVEs.

## sbfl — a faithful Defects4J-style SBFL benchmark

`sbfl/` is a Spectrum-Based Fault Localization benchmark built like **Defects4J**,
targeting C / HAProxy. Each CVE is a bug with a **minimal buggy↔fixed pair**, the
project's **own test suite**, **exact line-level ground truth**, and standard SBFL
artifacts:

- **Minimal pair** — buggy = the fixed release with the upstream fix **reverse-applied**
  (`patch -R`), so buggy and fixed differ by *only* the fix.
- **Ground truth** — the buggy↔fixed source `diff`, snapped to the nearest executable
  line (no hand anchors; handles faults of omission).
- **Real suite** — HAProxy's own `reg-tests/` run via **vtest**; triggers are
  auto-discovered the Defects4J way (*fail-on-buggy ∧ pass-on-fixed*), passing tests
  *pass on both*.
- **Coverage** — per-test line coverage of a `gcc --coverage` build, collected in
  `kind` pods (flushed via a `gdb __gcov_dump()` wrapper — works even for the
  infinite-loop CVE).
- **Artifacts** — GZoltar `spectra`/`matrix`/`tests` + Defects4J `projects/<CVE>/`
  metadata, ranked with Ochiai/Tarantula/Jaccard/DStar and scored by EXAM.

```bash
cvebench sbfl compile           # build the minimal-pair coverage images (all CVEs)
cvebench sbfl run               # coverage + classify + rank for all CVEs, then summary
cvebench sbfl coverage -p CVE-2022-0711   # one CVE
cvebench sbfl info    -p CVE-2022-0711    # metadata + result
cvebench sbfl summary           # fault-rank table        (sbfl/bin/sbfl is the same CLI)
```

Results (Ochiai, line granularity, `rank` = best–worst tie bounds):

| CVE | tests (f/p) | lines | fault rank | EXAM | character |
| --- | --- | --- | --- | --- | --- |
| CVE-2022-0711 | 1 / 19 | 19716 | 1–86 | 0.0000 | easy — cookie loop is fail-only |
| CVE-2023-45539 | 2 / 36 | 27188 | 673–719 | 0.0247 | medium — missing `#` check |
| CVE-2021-40346 | 1 / 19 | 19465 | 3405–8049 | 0.1749 | hard — overflow on an always-run line |

SBFL discriminates a line only when failing and passing tests cover it differently,
so these CVEs span a natural difficulty range. See [`sbfl/README.md`](sbfl/README.md)
for the full design (minimal pairs, vtest, gcov-flush wrapper, ground-truth snapping)
and interpretation.
