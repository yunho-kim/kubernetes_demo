# cvebench — a Defects4J-style CVE benchmark (HAProxy)

A reproducible benchmark of real CVEs, organized like **[Defects4J](https://github.com/rjust/defects4j)**
but targeting **C / HAProxy** instead of Java. A *project* is the software under
test; a *bug* is a CVE. Reproducing a CVE is just **running its trigger test**,
and fault-localization / automated-program-repair techniques apply on top of the
same `checkout → compile → test → coverage` contract — exactly as researchers use
Defects4J.

## Faithfulness to Defects4J

| Defects4J | cvebench |
| --- | --- |
| project (Lang, Chart, …) | `haproxy` (under `framework/projects/`) |
| bug `<PID>-<bid>` (Lang-1) | `haproxy-1`, `haproxy-2`, `haproxy-3` (a CVE each) |
| `commit-db` maps bid → revisions | `commit-db` maps bid → CVE, fixed release, fix commit, CWE, CVSS |
| minimal buggy↔fixed pair | **buggy = fixed release with `patches/<bid>.fix.patch` reverse-applied** |
| project's own test suite | HAProxy's `reg-tests/` run via `vtest` |
| trigger tests expose the bug | `trigger_tests/<bid>` — *fail on buggy, pass on fixed* (= the CVE reproduction) |
| `checkout / compile / test / coverage` | same verbs (`framework/bin/cvebench`) |
| `modified_classes`, `relevant_tests`, `loaded_classes` | same per-bug metadata files |

## Layout

```
framework/
├── bin/
│   ├── cvebench              # the CLI
│   ├── cvebench-coverage     # per-test coverage collector (kind + vtest)
│   ├── gtdiff.py             # buggy↔fixed diff → exact line-level ground truth
│   ├── assemble.py           # classify suite → GZoltar spectra/matrix/tests
│   └── sbfl-rank             # built-in SBFL: Ochiai/Tarantula/Jaccard/D*/Op2/Barinel/GP13/Kulczynski2/Ample + EXAM
├── lib/cvebench.sh           # commit-db + kind-cluster helpers
├── lib/flcommon.py           # shared FL helpers: spectrum, ground truth, rank/EXAM scoring, ranking.json
├── tools/<tool>/             # external FL/APR tool adapters (prepare.sh + run), host-native, no docker
│   ├── flitsr/               # FLITSR / FLITSR* (ISSTA 2023) over the GZoltar spectrum
│   ├── mbfl/                 # mutation-based FL: Mull mutants + Metallaxis / MUSE
│   ├── llmao/                # LLMAO (ICSE 2024): test-free LLM fault localization
│   ├── autofl/               # AutoFL (FSE 2024): LLM agent with code-navigation tools, ported to C
│   └── oracle/               # reference APR adapter (upstream fix + empty patch)
└── projects/haproxy/
    ├── commit-db             # bid,cve,fixed_version,fix_commit,cwe,cvss,summary
    ├── patches/<bid>.fix.patch
    ├── tests/<bid>/          # trigger.vtc, regtests.txt, build.env
    ├── build/                # Dockerfile.minpair, cov-flush-wrapper.sh, runsuite.sh
    ├── trigger_tests/<bid>  relevant_tests/<bid>  modified_classes/<bid>  loaded_classes/<bid>
bin/                          # vendored kind + kubectl
```

## Prerequisites

Docker (running, user in the `docker` group). `kind` and `kubectl` are vendored
in `./bin/`. Coverage builds compile HAProxy from source and run its `reg-tests`
under `vtest` inside a `kind` pod.

## CLI

```bash
cvebench=framework/bin/cvebench

$cvebench pids                                   # haproxy
$cvebench bids   -p haproxy                      # 1 2 3
$cvebench info   -p haproxy -b 3                 # metadata + result
$cvebench export -p haproxy -b 3 -q fix_commit
```

### Reproduce a CVE (= run its trigger test)
```bash
$cvebench compile -p haproxy -b 3                # build buggy+fixed coverage images
$cvebench test    -p haproxy -b 3                # buggy: trigger FAILS (reproduced); fixed: PASSES
```

### Fault localization
```bash
$cvebench coverage -p haproxy -b 3               # per-test coverage + classify + built-in SBFL rank
$cvebench fl       -p haproxy -b 3               # re-rank with the built-in SBFL (= cvebench sbfl)
$cvebench fl       -p haproxy -b 3 -f op2        # a single formula
$cvebench tools                                  # external FL tools and whether they are prepared
$cvebench prepare  -t flitsr                     # one-off: venv + pip under framework/tools/flitsr/
$cvebench fl       -p haproxy -b 3 -t flitsr     # FLITSR / FLITSR* on the same spectrum
$cvebench prepare  -t mbfl                       # one-off: Debian + clang + Mull + vtest base image
$cvebench fl       -p haproxy -b 3 -t mbfl -- --top 300   # mutation-based FL (Metallaxis, MUSE)
$cvebench prepare  -t llmao                      # one-off: venv with torch/transformers + LLMAO checkpoints
$cvebench fl       -p haproxy -b 3 -t llmao -- --model 6B  # LLM-based, test-free FL (GPU recommended)
$cvebench prepare  -t autofl                     # one-off: venv with openai client + tree-sitter
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 OPENAI_API_KEY=none AUTOFL_MODEL=<name> \
$cvebench fl       -p haproxy -b 3 -t autofl     # LLM agent FL (any OpenAI-compatible endpoint), R=5 runs
$cvebench summary  [-t flitsr] [-f ochiai]       # bug x tool x formula: fault rank + EXAM
```

Every FL tool writes `results/<pid>-<bid>/fl/<tool>/ranking.json` (schema in
`framework/lib/flcommon.py`), scored against `ground_truth.json` with the same
rank / EXAM code, so tools are directly comparable. External tools live under
`framework/tools/<tool>/` with a `prepare.sh` that installs everything they need
into a private, git-ignored venv — no host-specific dependencies and no
dedicated container; see `framework/tools/README.md` for the adapter contract.

### Automated program repair (patch generation)
```bash
$cvebench checkout -p haproxy -b 3 -v buggy -w /tmp/wd   # buggy source in a workdir
#   ... an APR tool edits /tmp/wd/haproxy-2.8.2/... ...
$cvebench diff     -w /tmp/wd > cand.patch               # export the edits as a patch -p1 diff
$cvebench validate -p haproxy -b 3 --patch cand.patch -t mytool   # buggy+candidate: trigger + relevant tests
#   -> results/haproxy-3/apr/mytool/cand/validation.json  (plausible = compiles & triggers pass & no relevant fails)
$cvebench apr      -p haproxy -b 3 -t oracle             # adapter-driven: checkout -> tool emits candidates -> validate each
$cvebench summary                                        # FL table + APR table
```

## Integrating your own FL / APR tool

The tool contract (adapter layout, `cvebench` verbs as an API, file schemas,
validation semantics, portability rules) is specified in
**[docs/INTEGRATION.md](docs/INTEGRATION.md)**. Reference adapters:
`framework/tools/flitsr` (FL) and `framework/tools/oracle` (APR).

## Bugs

| bug | CVE | CWE | CVSS | fixed in | fault file |
| --- | --- | --- | --- | --- | --- |
| haproxy-1 | CVE-2021-40346 | CWE-190 | 8.6 | 2.4.4 | `include/haproxy/htx.h` |
| haproxy-2 | CVE-2022-0711 | CWE-835 | 7.5 | 2.4.13 | `src/http_ana.c` |
| haproxy-3 | CVE-2023-45539 | CWE-436 | 8.2 | 2.8.2 | `src/h1.c` |

## Fault-localization results (Ochiai, line-level)

| bug | tests (f/p) | lines | fault rank | EXAM | character |
| --- | --- | --- | --- | --- | --- |
| haproxy-2 | 1 / 19 | 19716 | 1–86 | 0.0000 | easy — the loop is entered only by the trigger |
| haproxy-3 | 2 / 36 | 27188 | 673–719 | 0.0247 | medium — missing `#` check |
| haproxy-1 | 1 / 19 | 19465 | 3405–8049 | 0.1749 | hard — overflow on an always-executed line |

`results/` (per-test coverage, GZoltar `spectra`/`matrix`/`tests`, rankings) is
generated by `cvebench coverage` and git-ignored; the benchmark *definition*
(`commit-db`, patches, tests, metadata) is committed.

## How it works

- **Minimal pair** — `cvebench compile` builds the fixed release from source with
  `gcc --coverage`; the buggy image reverse-applies the fix patch. Only the fix differs.
- **Ground truth** — the buggy↔fixed source `diff`, snapped to the nearest executable
  (covered) line; handles faults of omission.
- **Coverage flush** — HAProxy is wrapped so a `gdb __gcov_dump()` fires on stop (and
  periodically for the CVE-2022-0711 infinite loop, which the watchdog would otherwise
  abort before flushing).
- **Classification** — each reg-test runs on both buggy and fixed; *fail-on-buggy ∧
  pass-on-fixed* ⇒ trigger, *pass-on-both* ⇒ passing test, else excluded.

## Adding a bug

Add a `commit-db` row, drop the upstream fix at `patches/<bid>.fix.patch`, add
`tests/<bid>/trigger.vtc` (+ `regtests.txt`), then `cvebench compile/coverage`.
