# cvebench — a Defects4J-style CVE benchmark (HAProxy, Kubernetes)

A reproducible benchmark of real CVEs, organized like **[Defects4J](https://github.com/rjust/defects4j)**
but targeting **C / HAProxy** and **Go / Kubernetes** instead of Java. A *project* is the software under
test; a *bug* is a CVE. Reproducing a CVE is just **running its trigger test**,
and fault-localization / automated-program-repair techniques apply on top of the
same `checkout → compile → test → coverage` contract — exactly as researchers use
Defects4J.

## Faithfulness to Defects4J

| Defects4J | cvebench |
| --- | --- |
| project (Lang, Chart, …) | `haproxy`, `kubernetes` (under `framework/projects/`) |
| `framework/core/Project/<Name>.pm` | `framework/projects/<pid>/project.sh` (fetch, build, test hooks) |
| bug `<PID>-<bid>` (Lang-1) | `haproxy-1..3`, `kubernetes-1..3` (a CVE each) |
| `commit-db` maps bid → revisions | `commit-db` maps bid → CVE, fixed release, fix commit, CWE, CVSS |
| minimal buggy↔fixed pair | **buggy = fixed release with `patches/<bid>.fix.patch` reverse-applied** |
| project's own test suite | HAProxy's `reg-tests/` run via `vtest`; Kubernetes' Go tests, one leaf test (`<pkg>::<Test>/<subtest>`) at a time |
| trigger tests expose the bug | `trigger_tests/<bid>` — *fail on buggy, pass on fixed* (= the CVE reproduction) |
| `checkout / compile / test / coverage` | same verbs (`framework/bin/cvebench`) |
| `modified_classes`, `relevant_tests`, `loaded_classes` | same per-bug metadata files |

## Layout

```
framework/
├── bin/
│   ├── cvebench              # the CLI
│   ├── cvebench-coverage     # per-test coverage + classification (suite run by the project module)
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
│   ├── cosmosfl/             # COSMosFL (LLM4Code 2025): ensemble of small models running AutoFL, votes combined
│   ├── miniswe/              # mini-swe-agent (2025): LLM agent that edits/tests in a sandbox of the buggy image
│   ├── openhands/            # OpenHands (ICLR 2025): CodeAct agent (terminal + file editor) on a sandboxed tree
│   ├── darjeeling/           # Darjeeling: GenProg-style search-based repair (Kaskara/clang statement edits)
│   ├── looprepair/           # LoopRepair (ICSE 2026): location-aware, trace-guided iterative LLM repair
│   └── oracle/               # reference APR adapter (upstream fix + empty patch)
├── projects/haproxy/
│   ├── project.sh            # project module: tarball fetch, gcov build, vtest runners, kind coverage
│   ├── commit-db             # bid,cve,fixed_version,fix_commit,cwe,cvss,summary
│   ├── patches/<bid>.fix.patch
│   ├── tests/<bid>/          # trigger.vtc, regtests.txt, build.env
│   ├── build/                # Dockerfile.minpair, cov-flush-wrapper.sh, runsuite.sh
│   └── trigger_tests/<bid>  relevant_tests/<bid>  modified_classes/<bid>  loaded_classes/<bid>
└── projects/kubernetes/
    ├── project.sh            # project module: GitHub tag archive, Go build, gosuite runners
    ├── commit-db
    ├── patches/<bid>.fix.patch   # source changes only; the fix's test changes stay in both variants
    ├── tests/<bid>/          # packages.txt, coverpkg.txt, trigger.txt, build.env, overlay/ (crafted triggers)
    ├── build/                # Dockerfile.minpair, gosuite.py (leaf-test runner + coverage), cover-on-panic.py
    └── trigger_tests/<bid>  relevant_tests/<bid>  modified_classes/<bid>  loaded_classes/<bid>
bin/                          # vendored kind + kubectl
```

## Prerequisites

Docker (running, user in the `docker` group). `kind` and `kubectl` are vendored
in `./bin/`. HAProxy coverage builds compile HAProxy from source and run its
`reg-tests` under `vtest` inside a `kind` pod. Kubernetes images build the
release on `golang:<ver>` (vendored dependencies, tests run offline) and run Go
tests in throwaway containers; an image is ~2.8 GB and builds in about a minute.

## CLI

```bash
cvebench=framework/bin/cvebench

$cvebench pids                                   # haproxy kubernetes
$cvebench bids   -p kubernetes                   # 1 2 3
$cvebench info   -p haproxy -b 3                 # metadata + result
$cvebench export -p haproxy -b 3 -q fix_commit
```

### Reproduce a CVE (= run its trigger test)
```bash
$cvebench compile -p haproxy -b 3                # build buggy+fixed coverage images
$cvebench test    -p haproxy -b 3                # buggy: trigger FAILS (reproduced); fixed: PASSES
$cvebench compile -p kubernetes -b 1             # same verbs for every project
$cvebench test    -p kubernetes -b 1
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
$cvebench prepare  -t cosmosfl -- --pull         # one-off: reuse the autofl venv; pull the 4 small models into Ollama
$cvebench fl       -p haproxy -b 3 -t cosmosfl   # COSMosFL: AutoFL x {llama3, llama3.1, mistral-nemo, qwen2.5-coder}, 5 runs each
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
$cvebench prepare  -t miniswe                            # one-off: venv with mini-swe-agent (+ litellm)
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 OPENAI_API_KEY=none \
$cvebench apr      -p haproxy -b 3 -t miniswe -- --runs 3 # LLM agent repair in a sandboxed buggy image
$cvebench prepare  -t openhands                          # one-off: venv with the OpenHands SDK + tools
LLM_BASE_URL=http://127.0.0.1:8080/v1 LLM_API_KEY=none \
$cvebench apr      -p haproxy -b 3 -t openhands          # OpenHands CodeAct agent, same sandbox
$cvebench prepare  -t darjeeling                         # one-off: venv + Kaskara's clang backend image
$cvebench apr      -p haproxy -b 2 -t darjeeling -- --time-limit 60   # GenProg-style search (needs cvebench coverage)
$cvebench prepare  -t looprepair                         # one-off: venv + upstream LoopRepair (LLM stage) + C grammar
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 OPENAI_API_KEY=none \
$cvebench apr      -p haproxy -b 3 -t looprepair -- --fl autofl   # iterative LLM repair from an FL ranking
$cvebench summary                                        # FL table + APR table
```

## Integrating your own FL / APR tool

The tool contract (adapter layout, `cvebench` verbs as an API, file schemas,
validation semantics, portability rules) is specified in
**[docs/INTEGRATION.md](docs/INTEGRATION.md)**. Reference adapters:
`framework/tools/flitsr` (FL), `framework/tools/oracle`, `framework/tools/miniswe`, `framework/tools/openhands`, `framework/tools/darjeeling` and `framework/tools/looprepair` (APR).

## Bugs

| bug | CVE | CWE | CVSS | fixed in | fault file |
| --- | --- | --- | --- | --- | --- |
| haproxy-1 | CVE-2021-40346 | CWE-190 | 8.6 | 2.4.4 | `include/haproxy/htx.h` |
| haproxy-2 | CVE-2022-0711 | CWE-835 | 7.5 | 2.4.13 | `src/http_ana.c` |
| haproxy-3 | CVE-2023-45539 | CWE-436 | 8.2 | 2.8.2 | `src/h1.c` |
| kubernetes-1 | CVE-2024-10220 | CWE-22 | 8.1 | 1.30.3 | `pkg/volume/git_repo/git_repo.go` (kubelet) |
| kubernetes-2 | CVE-2025-0426 | CWE-400 | 6.2 | 1.32.2 | `pkg/kubelet/server/server.go` (kubelet) |
| kubernetes-3 | CVE-2025-5187 | CWE-863 | 6.7 | 1.33.4 | `plugin/pkg/admission/noderestriction/admission.go` (kube-apiserver) |

The Kubernetes bugs are the highest-CVSS CVEs of the core components
(`kubernetes/kubernetes`) fixed in the last three years that reproduce on Linux:
Windows-only CVEs, subprojects (ingress-nginx, CSI drivers, image-builder) and
CVEs without a fix were left out. Triggers are the upstream regression tests of
the fix (kubernetes-1, -3) or a crafted Go test (kubernetes-2, whose fix shipped
without one).

## Fault-localization results (Ochiai, line-level)

| bug | tests (f/p) | lines | fault rank | EXAM | character |
| --- | --- | --- | --- | --- | --- |
| haproxy-2 | 1 / 19 | 19716 | 1–86 | 0.0000 | easy — the loop is entered only by the trigger |
| haproxy-3 | 2 / 36 | 27188 | 673–719 | 0.0247 | medium — missing `#` check |
| haproxy-1 | 1 / 19 | 19465 | 3405–8049 | 0.1749 | hard — overflow on an always-executed line |
| kubernetes-3 | 3 / 210 | 511 | 3–4 | 0.0039 | easy — missing ownerReferences check, three focused subtests |
| kubernetes-1 | 1 / 35 | 670 | 1–158 | 0.0000 | tie — the one failing test is the only one that sets up a gitRepo volume |
| kubernetes-2 | 1 / 478 | 1186 | 106–217 | 0.0885 | medium — a route registered in the wrong handler set; every test runs it |

`results/` (per-test coverage, GZoltar `spectra`/`matrix`/`tests`, rankings) is
generated by `cvebench coverage` and git-ignored; the benchmark *definition*
(`commit-db`, patches, tests, metadata) is committed.

## How it works

- **Minimal pair** — `cvebench compile` builds the fixed release from source (HAProxy
  with `gcc --coverage`; Kubernetes with its pinned Go toolchain, build cache warmed
  on the fixed source); the buggy image reverse-applies the fix patch. Only the fix differs.
- **Ground truth** — the buggy↔fixed source `diff`, snapped to the nearest executable
  (covered) line; handles faults of omission.
- **Coverage flush** — HAProxy is wrapped so a `gdb __gcov_dump()` fires on stop (and
  periodically for the CVE-2022-0711 infinite loop, which the watchdog would otherwise
  abort before flushing). In the Kubernetes images Go's `testing` package writes the
  coverage profile before re-panicking, so a buggy version that panics (kubernetes-1)
  keeps its failing test's coverage.
- **Classification** — each reg-test runs on both buggy and fixed; *fail-on-buggy ∧
  pass-on-fixed* ⇒ trigger, *pass-on-both* ⇒ passing test, else excluded.

## Adding a bug

Add a `commit-db` row, drop the upstream fix at `patches/<bid>.fix.patch`, add the
bug's `tests/<bid>/` (haproxy: `trigger.vtc` + `regtests.txt`; kubernetes:
`trigger.txt`, `packages.txt`, `coverpkg.txt`, `build.env`, optional `overlay/`),
then `cvebench compile/test/coverage`. A new project adds `framework/projects/<pid>/`
with a `project.sh` module (hooks documented in `framework/lib/cvebench.sh`).

The external tools `flitsr` (spectrum only) and `oracle` work on every project; the
others under `framework/tools/` are still HAProxy-specific (vtest, gcov, `make`, C
parsers). On the Kubernetes bugs the core harness (`checkout … validate`,
`coverage`, built-in SBFL, `fl -t flitsr`, `apr -t oracle`) works today.
