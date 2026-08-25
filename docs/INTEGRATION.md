# Integrating fault-localization and program-repair tools with cvebench

This document is the contract between **cvebench** (the Defects4J-style CVE
benchmark in this repository) and any external **fault localization (FL)** or
**automated program repair (APR)** tool. It is written for researchers and
developers who want to evaluate their tool on the benchmark without changing
the harness.

There are two ways to integrate, and you can mix them:

| style | what you write | when to use |
| --- | --- | --- |
| **Adapter** (recommended) | `framework/tools/<tool>/{tool.json, prepare.sh, run}` | your tool can be driven from a script; the harness runs it for every bug and scores it |
| **Driver** | nothing in this repo — you call the `cvebench` verbs as an API | your tool has its own orchestration; cvebench provides checkouts, tests, coverage and scoring |

Both rest on the same file-based data contract (section 3), so a tool
integrated one way can always be scored like a tool integrated the other way.

---

## 1. Vocabulary (Defects4J terms)

| term | meaning in cvebench |
| --- | --- |
| project `pid` | the software under test; currently `haproxy` (`framework/projects/haproxy/`) |
| bug `bid` | one CVE; `haproxy-1`, `haproxy-2`, `haproxy-3`. Metadata: `framework/projects/<pid>/commit-db` |
| fixed version | the upstream release that contains the fix |
| buggy version | the fixed release with `patches/<bid>.fix.patch` **reverse-applied** — buggy and fixed differ by exactly the fix (a *minimal pair*) |
| trigger tests | tests that **fail on buggy and pass on fixed** (= the CVE reproduction). `trigger_tests/<bid>` |
| relevant tests | tests that execute at least one modified file. `relevant_tests/<bid>` |
| modified classes | files touched by the fix. `modified_classes/<bid>` |
| ground truth | the executable lines the fix touched, line-level (`results/<pid>-<bid>/ground_truth.json`) |
| test | one HAProxy `reg-tests/**/*.vtc` run under `vtest`; ids are the path with `reg-tests/` stripped and `/`,`.` → `_` (e.g. `http-rules_normalize_uri_vtc`). The crafted trigger is `_sbfl_trigger_vtc` |

All tests are **behavioural network tests** (HTTP request/response
expectations), not unit tests or crash reproducers. Plan for a few seconds per
test and for the whole suite of a bug to take minutes.

---

## 2. The harness API: `cvebench` verbs

`framework/bin/cvebench` is the only entry point. Every verb is idempotent and
scriptable; paths are absolute or relative to the current directory.
Non-zero exit = failure (message on stderr).

### Discovery
| verb | output |
| --- | --- |
| `cvebench pids` | project ids, one per line |
| `cvebench bids -p <pid>` | bug ids, one per line |
| `cvebench info -p <pid> -b <bid>` | human-readable bug card incl. FL results so far |
| `cvebench export -p <pid> -b <bid> -q <field>` | one metadata field: `cve`, `fixed_version`, `fix_commit`, `cwe`, `cvss`, `summary`, `modified_classes` |
| `cvebench tools` | integrated tools: name, kind (`fl`/`apr`), prepared or not |

### Building blocks
| verb | effect |
| --- | --- |
| `cvebench checkout -p <pid> -b <bid> -v buggy\|fixed -w <workdir>` | source tree at `<workdir>/haproxy-<ver>/`, plus `<workdir>/cvebench.bug` (`<pid> <bid> <variant>`) and `<workdir>/cvebench-tests/` (trigger.vtc, regtests.txt). Downloads are cached in `results/.cache/` |
| `cvebench compile -p <pid> -b <bid>` | builds the coverage-instrumented `buggy` and `fixed` images (`cvebench:<pid>-<bid>-{buggy,fixed}`) |
| `cvebench test -p <pid> -b <bid> [-v buggy\|fixed]` | runs the trigger test; on buggy it must fail (CVE reproduced), on fixed pass |
| `cvebench coverage -p <pid> -b <bid>` | runs the bug's suite on both variants, collects per-test line coverage, classifies tests, writes the GZoltar spectrum, ground truth and the Defects4J metadata files, and runs the built-in SBFL |

### Fault localization
| verb | effect |
| --- | --- |
| `cvebench fl -p <pid> -b <bid> [-t <tool>] [-f <formula>] [-- <tool args>]` | runs an FL tool (default `sbfl`) over `results/<pid>-<bid>/`, writes `fl/<tool>/ranking.json`, prints ranks + EXAM |
| `cvebench sbfl -p <pid> -b <bid>` | alias for `fl -t sbfl` |
| `cvebench prepare -t <tool>` | runs the tool's `prepare.sh` (one-off per machine) |

### Program repair
| verb | effect |
| --- | --- |
| `cvebench diff -w <workdir>` | unified diff (`patch -p1` format) of your edits in a checkout vs. the pristine variant, on stdout. Build artifacts are excluded |
| `cvebench validate -p <pid> -b <bid> (--patch <diff> \| -w <workdir>) [-t <tool>] [--id <name>] [--full]` | builds **buggy + candidate**, runs trigger + relevant tests (`--full`: the bug's whole suite), writes `apr/<tool>/<id>/validation.json`. Verdict `plausible` = compiles ∧ all trigger tests pass ∧ no relevant test fails |
| `cvebench apr -p <pid> -b <bid> -t <tool> [-w <workdir>] [-- <tool args>]` | adapter driver: checkout → `tools/<tool>/run` emits candidates → `validate` each |

### Reporting
| verb | effect |
| --- | --- |
| `cvebench summary [-t <tool>] [-f <formula>]` | table of every FL ranking (bug × tool × formula: fault rank, EXAM) and every APR validation (bug × tool × candidate: compiles, trigger pass, relevant fail, verdict) |
| `cvebench clean` | deletes the kind cluster used for coverage collection |

Environment knobs: `CVEBENCH_KEEP_IMAGES=1` keeps candidate images after
validation; `KIND_CLUSTER` names the kind cluster (default `cvebench`).

---

## 3. Data contract: `results/<pid>-<bid>/`

Everything a tool consumes or produces is a plain file under
`results/<pid>-<bid>/`. Paths inside files are **source-relative**
(`src/h1.c`, `include/haproxy/htx.h`). Only `src/` and `include/` are
instrumented.

```
results/<pid>-<bid>/
├── tests.json                # classified suite (see below)
├── classification.json       # {"triggers":[id], "passing":[id], "relevant":[id], "loaded":[file], "excluded":[...]}
├── ground_truth.json         # {"files":[...], "lines":[{"file","line"}], "fault_of_omission":bool, "resolution":{...}}
├── ground_truth_raw.json     # the same before snapping to executable lines
├── buggy/outcomes.txt        # "<id> <pass|fail|skip> <vtc>" per test on buggy
├── buggy/coverage/<id>.json  # {"test": id, "covered": {"src/h1.c": [577, 578, ...], ...}}
├── fixed/outcomes.txt        # outcomes on fixed
├── gzoltar/                  # spectrum in GZoltar format
│   ├── spectra               #   one component per line:  "<file>#<line>"
│   ├── matrix                #   one row per test: "0 1 1 ... +|-"  (+ = pass, - = fail), columns = spectra order
│   └── tests                 #   "name,outcome,runtime,stacktrace" header, then "<id>,PASS|FAIL,0,"
├── fl/<tool>/ranking.json    # FL output (section 4.4)
└── apr/<tool>/               # APR output (section 5.4)
    ├── workdir/              #   buggy checkout used by `cvebench apr`
    ├── candidates/<name>.patch
    └── <name>/{candidate.patch, build.log, test.log, outcomes.txt, validation.json}
```

`tests.json` — the suite after Defects4J classification (trigger = fail on
buggy ∧ pass on fixed; passing = pass on both; everything else excluded):

```json
[
  {"name": "_sbfl_trigger_vtc",        "outcome": "fail", "coverage": "buggy/coverage/_sbfl_trigger_vtc.json"},
  {"name": "http-rules_normalize_uri_vtc", "outcome": "fail", "coverage": "buggy/coverage/http-rules_normalize_uri_vtc.json"},
  {"name": "connection_dispatch_vtc",  "outcome": "pass", "coverage": "buggy/coverage/connection_dispatch_vtc.json"}
]
```

`ground_truth.json` — the fix's lines, **snapped to the nearest executable
(covered) line within 8 lines** so that a line-level ranking can hit them;
`fault_of_omission` is true when the fix only *adds* code. The raw diff lines
are in `ground_truth_raw.json`. Tools that rank functions or files should map
their output to lines (section 4.5).

Per-bug Defects4J metadata also lives under the project, independent of any
run: `framework/projects/<pid>/{trigger_tests,relevant_tests,modified_classes,loaded_classes}/<bid>`
and `patches/<bid>.fix.patch`.

---

## 4. FL tool interface

### 4.1 Layout

```
framework/tools/<tool>/
├── tool.json          # {"name","kind":"fl","description","reference","url","granularity":"line|function|file"}
├── prepare.sh         # optional; one-off setup on a fresh machine
├── requirements.txt   # optional; pinned Python deps installed by prepare.sh
├── run                # required; executable
└── .venv/             # created by prepare.sh, git-ignored
```

### 4.2 `prepare.sh`

Installs everything the tool needs **into the tool directory** (a venv, a
downloaded model, a compiled binary). It must not assume anything about the
host beyond `python3`, `bash`, `git`, network access, and must be safe to run
twice. Nothing is installed system-wide. If your tool has no dependencies,
omit the file (the harness reports it as `ready`).

### 4.3 `run`

```
run <results-dir> [-o <out-dir>] [--metric <name>]... [tool-specific args]
```

* `<results-dir>` — `results/<pid>-<bid>/` as left by `cvebench coverage`.
  Read whatever you need: `tests.json` + `buggy/coverage/*.json` (per-test
  line coverage), `gzoltar/` (matrix form), `classification.json`,
  `ground_truth.json` (**for scoring only — never as input to the technique**).
  If the tool needs source code, run `cvebench checkout -v buggy -w <dir>`
  yourself (or read `results/<pid>-<bid>/apr/*/workdir` if present).
* `-o <out-dir>` — where to write; defaults to `<results-dir>/fl/<tool>/`.
  `cvebench fl` always passes it.
* `--metric` — `cvebench fl -f X` is forwarded as `--metric X`; ignore it if
  meaningless for your tool. Anything after `--` on the `cvebench fl` command
  line is appended verbatim.
* Must be runnable from any current directory and must not depend on
  host-installed third-party packages: call your venv explicitly
  (`$HERE/.venv/bin/python`, `$HERE/.venv/bin/<tool>`), keep the `run`
  script itself standard-library Python or bash.
* Exit non-zero on failure, with a message on stderr.

### 4.4 Output: `ranking.json`

```json
{
  "tool": "flitsr",
  "failing": ["_sbfl_trigger_vtc", "http-rules_normalize_uri_vtc"],
  "passing": ["connection_dispatch_vtc", "..."],
  "n_lines": 27104,
  "formulas": {
    "flitsr_ochiai": {
      "fault_rank": [556, 602, 27104],
      "exam": 0.0205,
      "top": [{"file": "include/haproxy/bug.h", "line": 258, "susp": 0.7071}, "..."]
    }
  }
}
```

* `formulas` holds one entry per ranking variant the tool produced (a formula,
  a configuration, a model). Keys are free-form `[a-z0-9_]+`; a key containing
  `ochiai` is used as the headline in `cvebench info`.
* `fault_rank` = `[best, worst, total]`: rank of the first ground-truth line,
  with ties counted optimistically (`best`) and pessimistically (`worst`);
  `null` if no ground-truth line appears in the ranking.
  `exam` = `(best − 1) / total`.
* `top` is informational (the first N entries); the harness scores from the
  full ranking at run time, not from `top`.
* Each `cvebench fl` run **replaces** `fl/<tool>/ranking.json`; a run
  restricted with `-f` therefore yields a file with only that formula. Use a
  different `-o` (via `-- -o <dir>` for adapters that accept it) to keep
  several configurations side by side.

The easiest way to produce this is the helper module
`framework/lib/flcommon.py` (standard library only):

```python
import sys, os; sys.path.insert(0, os.path.join(HERE, "..", "..", "lib"))
import flcommon as fl

spectrum, failing, passing = fl.build_spectrum(results_dir)   # {(file,line): (ef,ep,nf,np)}
elements = fl.load_gzoltar_elements(results_dir)              # [(file,line)] in gzoltar column order
gt       = fl.load_ground_truth(results_dir)
scored   = [((file, line), susp), ...]      # YOUR ranking, most suspicious first; equal susp = tie
entry    = fl.report("mytool_default", scored, gt, top=15)    # prints, returns the formulas entry
fl.write_ranking(out_dir, "mytool", failing, passing, len(scored), {"mytool_default": entry})
```

`score_against_gt(scored, gt)` is the exact function the built-in SBFL uses,
so reported ranks are comparable across tools.

### 4.5 Granularity

Ground truth is line-level. If your tool ranks **functions** or **files**,
expand each ranked unit to its executable lines (all lines of that unit that
appear in `gzoltar/spectra`), giving them the unit's score; ties within a unit
are then reported as a `best–worst` range, which is the standard
optimistic/pessimistic treatment. Say so in `tool.json` (`"granularity"`).

### 4.6 Checklist

1. `cvebench prepare -t <tool>` succeeds on a clean machine.
2. `cvebench fl -p haproxy -b 3 -t <tool>` writes `results/haproxy-3/fl/<tool>/ranking.json`.
3. `cvebench summary -t <tool>` shows a row per formula.
4. The technique never reads `ground_truth*.json`, `fixed/`, `patches/`, or `trigger_tests/` (label leakage). LLM-based tools should additionally state how they control for the CVE being in training data.

Reference implementations:

* `framework/tools/flitsr/` — a **spectrum consumer**: converts `gzoltar/` to
  the GZoltar-CLI layout, runs FLITSR/FLITSR* per metric, maps results back.
* `framework/tools/mbfl/` — a **dynamic technique that re-executes tests**:
  selects candidate lines from the spectrum, builds the buggy program with
  clang + Mull so every mutant is in one binary (switched on by an environment
  variable), runs each mutant against the tests that cover its line in
  throw-away containers, and scores lines with Metallaxis / MUSE. It shows how
  to reuse the bug's test list (`buggy/outcomes.txt` maps test ids to `.vtc`
  paths) and how to keep a tool's toolchain out of the host (`prepare.sh`
  builds a base image; `versions.env` pins LLVM/Mull).
* `framework/tools/llmao/` — a **model-based, test-free technique**: `run`
  (stdlib) picks the files to score from the spectrum and checks out the buggy
  source, then delegates to `llmao_infer.py` inside the tool's venv (torch,
  transformers pinned in `requirements.txt`; upstream LLMAO cloned at a pinned
  commit by `prepare.sh`, Hugging Face cache kept under `.venv/`). It shows how
  to wrap a GPU/ML tool without touching the host Python: `prepare.sh` picks a
  Python 3.10–3.13 interpreter or a `uv`-managed one, and `run` never imports
  third-party packages itself.
* `framework/tools/autofl/` — an **LLM agent that needs an API**: the upstream
  Java protocol (four code-navigation functions, R repetitions, vote scoring)
  is ported to C with a tree-sitter function index. The endpoint is configured
  only through `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `AUTOFL_MODEL`, so the
  same adapter runs against OpenAI, a local llama.cpp/vLLM server, or any
  compatible gateway; `--protocol text` covers servers without tool calling.
  It also shows the **leakage controls** an LLM tool should apply: CVE
  identifiers in the test shown to the model are redacted by default, and the
  full conversation of every run is saved (`runs/rep<k>.json`) for audit.
  Function-level answers are expanded to line ranks as described in 4.5.

---

## 5. APR tool interface

### 5.1 Layout

Same as FL, with `"kind": "apr"` in `tool.json`.

### 5.2 `run`

```
run <results-dir> <workdir> -o <out-dir> [tool-specific args]
```

* `<workdir>` — a **buggy** checkout (`cvebench checkout -v buggy`):
  `<workdir>/haproxy-<ver>/` is the tree to repair, `<workdir>/cvebench.bug`
  identifies the bug, `<workdir>/cvebench-tests/trigger.vtc` is the failing
  test (its expectations describe the intended behaviour). The tool may edit
  the tree in place.
* `<results-dir>` — may contain FL results (`fl/<tool>/ranking.json`) and the
  test classification for your tool to consume; it may be absent or partial,
  so do not require it.
* `-o <out-dir>` — write candidate patches to `<out-dir>/candidates/<name>.patch`.
  Anything else you write there (logs, intermediate states) is kept.
* A candidate patch is a **unified diff applied with `patch -p1` from the
  source root** (`--- a/src/h1.c` / `+++ b/src/h1.c`). If your tool edits
  `<workdir>` directly, export with `cvebench diff -w <workdir> > cand.patch`
  (revert or re-checkout between candidates).
* Do **not** run the tests yourself for the final verdict — the harness does,
  in a clean image. If your tool needs test feedback during search (GenProg,
  conversational LLM repair, …) use the Driver style (5.5) and call
  `cvebench validate` from inside your loop, or build/run HAProxy in
  `<workdir>` yourself for cheap intermediate checks (the harness's verdict is
  what counts).

### 5.3 Validation semantics

For each candidate the harness

1. builds an image from the **buggy** tree with the candidate applied
   (`compiles` = false if `patch` or `make` fails; see `build.log`),
2. runs the trigger test(s) and the bug's relevant tests inside that image
   (with `--full`, the whole suite listed in `tests/<bid>/regtests.txt`),
3. records `plausible = compiles ∧ every trigger test passes ∧ no relevant test fails`.

`plausible` is the Defects4J notion of a *test-adequate* patch. **Correctness
is not decided by the harness** — compare `candidate.patch` with
`patches/<bid>.fix.patch` (the upstream fix) manually or with your own
equivalence check, as the APR literature requires.

Cost: one image build (~1–3 min on a modern machine; the source download,
`vtest` and toolchain layers are cached by Docker) plus a few seconds per
test. Validate the interesting candidates, not every mutant.

### 5.4 Output: `validation.json`

```json
{
  "bug": "haproxy-3", "tool": "oracle", "candidate": "oracle",
  "patch": "candidate.patch", "compiles": true, "suite": "relevant",
  "tests": {"_sbfl_trigger_vtc": {"outcome": "pass", "vtc": "reg-tests/_sbfl_trigger.vtc", "role": "trigger"},
            "http-rules_normalize_uri_vtc": {"outcome": "pass", "vtc": "reg-tests/http-rules/normalize_uri.vtc", "role": "trigger"},
            "connection_dispatch_vtc": {"outcome": "pass", "vtc": "...", "role": "relevant"}},
  "trigger_pass": 2, "trigger_total": 2, "relevant_fail": [], "plausible": true
}
```

### 5.5 Driver style (no adapter)

If you orchestrate from your own code, the loop is:

```bash
cvebench checkout -p haproxy -b 3 -v buggy -w /tmp/wd          # source + trigger test
cvebench fl -p haproxy -b 3 -t sbfl                              # optional: results/haproxy-3/fl/sbfl/ranking.json
# ... your tool edits /tmp/wd/haproxy-2.8.2/ ...
cvebench validate -p haproxy -b 3 -w /tmp/wd -t mytool --id try1  # or: --patch cand.patch
cat results/haproxy-3/apr/mytool/try1/validation.json
```

`validate` is the single evaluation primitive; call it as often as your search
needs. Results land under `apr/<tool>/` exactly as for adapters, so
`cvebench summary` compares both styles.

### 5.6 Checklist

1. `cvebench apr -p haproxy -b 3 -t <tool>` produces at least one `candidates/*.patch`.
2. Each `apr/<tool>/<candidate>/validation.json` exists; `cvebench summary -t <tool>` lists them.
3. The tool never reads `patches/<bid>.fix.patch`, `fixed/` or a fixed checkout (except the reference `oracle` tool, whose whole point is to do so).

Reference implementation: `framework/tools/oracle/` — emits the upstream fix
(must validate as plausible) and an empty patch (must not). Use it to check
the pipeline on a new machine and as a template.

---

## 6. Portability rules (all tools)

* **No host-specific dependencies.** Anything beyond `bash`, `python3`, `git`,
  `docker` is installed by `prepare.sh` into `framework/tools/<tool>/`.
  Pin versions (`requirements.txt`, git tags, checksums).
* **No dedicated containers for the tool itself.** The harness already uses
  Docker for the software under test; tools run on the host. A tool that needs
  the *program under test* built differently (another compiler, an
  instrumentation plugin — e.g. `mbfl` compiles HAProxy with clang + Mull) may
  build such a variant image from its `prepare.sh`/`run`, the way the coverage
  build does; the tool's own logic still runs on the host. (If a tool is only
  distributed as an image, `prepare.sh` may pull it and `run` may `docker run`
  it — document this in `tool.json`.)
* **Stdlib-only entry points.** `run` may be bash or Python without
  third-party imports; it delegates to the venv.
* **Deterministic where possible.** Seed randomness and record the seed in the
  output directory; LLM tools record model id, prompt template and temperature.
* **Write only under `-o <out-dir>`** and never modify `framework/projects/`.

---

## 7. Bug metadata for tool authors

| file | content |
| --- | --- |
| `framework/projects/<pid>/commit-db` | `bid,cve,fixed_version,fix_commit,cwe,cvss,summary` |
| `patches/<bid>.fix.patch` | the upstream fix (ground truth; APR tools must not read it) |
| `tests/<bid>/trigger.vtc` | the crafted CVE reproduction test |
| `tests/<bid>/regtests.txt` | globs of reg-tests that form the bug's suite |
| `tests/<bid>/build.env` | optional build/coverage knobs (`EXTRA_MAKE`, `COV_TICK`) |
| `trigger_tests/<bid>`, `relevant_tests/<bid>`, `modified_classes/<bid>`, `loaded_classes/<bid>` | Defects4J-style lists, regenerated by `cvebench coverage` |

Current bugs: `haproxy-1` CVE-2021-40346 (CWE-190, request smuggling),
`haproxy-2` CVE-2022-0711 (CWE-835, infinite loop), `haproxy-3`
CVE-2023-45539 (CWE-436, URI `#` misrouting). All three are logic bugs
observed through behavioural tests — there is no sanitizer crash to localize
or repair from, which matters for crash-driven techniques.

---

## 8. Known limitations

* Line-level ground truth is snapped to executable lines; fixes that only add
  code (`fault_of_omission`) are credited to the nearest executed line.
* Coverage is line coverage from gcov at `-O0`; there is no branch or
  data-flow information in the spectrum.
* Validation uses the reg-tests of the bug's suite, not HAProxy's entire
  test tree; `--full` widens it to `regtests.txt`.
* A single project (HAProxy, C). The contract is project-agnostic and new
  projects follow the same `framework/projects/<pid>/` layout.
