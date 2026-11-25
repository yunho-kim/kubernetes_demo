"""aprsandbox — shared pieces of the APR adapters that build and test the program in the
bug's image (framework/tools/miniswe, liveswe, openhands, magentless, darjeeling,
looprepair). Stdlib only.

An agent's commands run in a sandbox container of the bug's buggy image
(cvebench:<pid>-<bid>-buggy: the source tree already built with the benchmark's
flags) — docs/INTEGRATION.md 5.7. Per project language (HAProxy = C: make + vtest;
Kubernetes = Go: go build / go test) this module provides

  * the helper commands mounted read-only at /cvebench (cvebench-build,
    cvebench-test, cvebench-setup) and the failing tests as the agent sees them
    (comments stripped, CVE ids redacted),
  * the setup command that hides what would reveal the fix,
  * the bug report text, optional FL hints, and
  * the export of the agent's edits as a candidate patch (`cvebench diff`).
"""
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

FW_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CVEBENCH = os.path.join(FW_ROOT, "bin", "cvebench")

# docker run arguments of every sandbox: no network (upstream sources are online),
# a sane fd limit (HAProxy sizes its fd table from RLIMIT_NOFILE), ptrace for gdb.
DOCKER_ARGS = ["--network", "none", "--ulimit", "nofile=65536:65536", "--cap-add=SYS_PTRACE"]

# PATH inside every sandbox: the helpers first; /usr/local/go/bin for the Go images.
SANDBOX_PATH = "/cvebench/bin:/usr/local/go/bin:/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# project -> (language, display name)
PROJECTS = {"haproxy": ("c", "HAProxy"), "kubernetes": ("go", "Kubernetes")}

# benchmark build flags (framework/projects/haproxy/build/Dockerfile.minpair) — the
# sandbox rebuilds incrementally on top of the image's objects, so they must match.
MAKE_ARGS = ('TARGET=linux-glibc USE_OPENSSL= USE_PCRE= USE_PCRE2= USE_ZLIB= USE_LUA= USE_SYSTEMD= '
             'CPU_CFLAGS="-O0" DEBUG_CFLAGS="-g3 -fno-inline -fprofile-arcs -ftest-coverage" '
             'LDFLAGS="-fprofile-arcs -ftest-coverage"')

# gcov data of the instrumented build is redirected to /tmp (GCOV_PREFIX).
BUILD_SH = r"""#!/usr/bin/env bash
# cvebench-build — rebuild HAProxy after source edits ($HAPROXY_SRC/haproxy).
cd "$HAPROXY_SRC" || exit 2
log=/tmp/cvebench-build.log
if make -j"$(nproc)" __MAKE_ARGS__ >"$log" 2>&1; then
  echo "build OK"
else
  echo "BUILD FAILED:"; grep -E 'error|Error|undefined reference' "$log" | head -40
  exit 1
fi
"""

TEST_SH = r"""#!/usr/bin/env bash
# cvebench-test [trigger|all|<vtc> ...] — rebuild, then run reg-tests with vtest.
ulimit -n 65536 2>/dev/null || ulimit -n 4096 2>/dev/null || true
export GCOV_PREFIX=/tmp/cvebench-gcov
cvebench-build >/tmp/cvebench-build.out || { cat /tmp/cvebench-build.out; exit 1; }
cd "$HAPROXY_SRC" || exit 2
[ $# -eq 0 ] && set -- trigger
list=()
for a in "$@"; do
  case "$a" in
    trigger) list+=(/cvebench/trigger.vtc) ;;
    all) list+=(/cvebench/trigger.vtc)
         while IFS= read -r v; do list+=("$v"); done < /tmp/relevant_vtc.txt ;;
    *) list+=("$a") ;;
  esac
done
fail=0
for vtc in "${list[@]}"; do
  HAPROXY_PROGRAM="$HAPROXY_SRC/haproxy" timeout -s KILL 60 vtest "$vtc" >/tmp/v.log 2>&1
  case "$?" in
    0) echo "PASS $vtc" ;;
    77) echo "SKIP $vtc" ;;
    *) fail=$((fail+1)); echo "FAIL $vtc"
       grep -E '^---- |^\*\*\*  [a-z0-9]+ +debug\|.*(error|alert|Fatal|ALERT)' /tmp/v.log | tail -8 | sed 's/^/    /' ;;
  esac
done
echo "== ${#list[@]} test(s), $fail failed"
[ $fail -eq 0 ]
"""


# cvebench-setup <src> (as root, once, before the agent starts): the project's own setup.
SETUP_C = r"""#!/usr/bin/env bash
# cvebench-setup <src> — resolve the relevant reg-tests (/tmp/relevant_vtc.txt, `cvebench-test all`).
cd "$1" || exit 2
ls $(cat /cvebench/globs.txt) 2>/dev/null | sort -u | while read -r v; do
  id=$(printf '%s' "$v" | sed 's#reg-tests/##; s#[/.]#_#g')
  grep -qx "$id" /cvebench/relevant_ids.txt && echo "$v"
done > /tmp/relevant_vtc.txt
chmod 644 /tmp/relevant_vtc.txt
"""

SETUP_GO = r"""#!/usr/bin/env bash
# cvebench-setup <src> — copy the crafted trigger tests (comments stripped) into the tree,
# owned like the tree, and let a non-root agent use the image's warm Go build cache.
src="$1"; own="$(stat -c %u:%g "$src")"
if [ -d /cvebench/overlay ]; then
  (cd /cvebench/overlay && find . -type f) | while read -r f; do
    mkdir -p "$src/$(dirname "$f")" && cp "/cvebench/overlay/$f" "$src/$f" && chown "$own" "$src/$f"
  done
fi
chmod a+x /root 2>/dev/null; chmod -R a+rwX /root/.cache 2>/dev/null
true
"""

GO_BUILD_SH = r"""#!/usr/bin/env bash
# cvebench-build — compile the bug's component(s) and the test binaries of its suite packages.
src="${CVEBENCH_SRC:-$K8S_SRC}"; cd "$src" || exit 2
export GOCACHE="${GOCACHE:-/root/.cache/go-build}"
log=/tmp/cvebench-build.log; : > "$log"; ok=1
for p in $(cat /cvebench/build_pkgs.txt); do go build -o /dev/null "$p" >>"$log" 2>&1 || ok=0; done
for p in $(cat /cvebench/test_pkgs.txt); do
  go test -c -o /tmp/cvebench-build.test "./$p" >>"$log" 2>&1 || ok=0
done
rm -f /tmp/cvebench-build.test
if [ $ok = 1 ]; then echo "build OK"; else echo "BUILD FAILED:"; grep -E '\.go:[0-9]+|^#' "$log" | head -40; exit 1; fi
"""

GO_TEST_PY = r"""#!/usr/bin/env python3
# cvebench-test [trigger|all|<pkg dir>::<Test>[/<subtest>] ...] — run Go tests (go test -json,
# one run per package) and print PASS/FAIL per test with the failing tests' output.
import json, os, re, subprocess, sys

src = os.environ.get("CVEBENCH_SRC") or os.environ["K8S_SRC"]
os.environ.setdefault("GOCACHE", "/root/.cache/go-build")

def read(f):
    if not os.path.exists(f):
        return []
    return [l.strip() for l in open(f) if l.strip() and not l.lstrip().startswith("#")]

ids = []
for a in sys.argv[1:] or ["trigger"]:
    if a == "trigger":
        ids += read("/cvebench/trigger_ids.txt")
    elif a == "all":
        ids += read("/cvebench/trigger_ids.txt") + read("/cvebench/relevant_ids.txt")
    elif "::" in a:
        ids.append(a)
    else:
        sys.exit("cvebench-test: not a test id (<pkg dir>::<Test>[/<subtest>]): " + a)
ids = list(dict.fromkeys(ids))

def quote_meta(s):
    return re.sub(r"([\\.+*?()|\[\]{}^$])", r"\\\1", s)

bypkg = {}
for tid in ids:
    pkg, name = tid.split("::", 1)
    bypkg.setdefault(pkg, []).append(name)
n = failed = 0
passed = []
for pkg, names in bypkg.items():
    tops = sorted({nm.split("/")[0] for nm in names})
    pat = "^(" + "|".join(quote_meta(t) for t in tops) + ")$"
    r = subprocess.run(["go", "test", "-count=1", "-json", "-timeout=900s", "-run", pat, "./" + pkg],
                       cwd=src, capture_output=True, text=True)
    res, out = {}, {}
    for line in r.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("Test")
        if not t:
            continue
        if e.get("Action") in ("pass", "fail", "skip"):
            res[t] = e["Action"]
        elif e.get("Action") == "output":
            out.setdefault(t, []).append(e.get("Output", ""))
    for nm in names:
        n += 1
        tid = pkg + "::" + nm
        a = res.get(nm)
        if a in ("pass", "skip"):
            passed.append(a.upper() + " " + tid)
            continue
        failed += 1
        print("FAIL " + tid + ("" if a else " (no result: build failure or no such test)"))
        body = [l.rstrip() for l in out.get(nm, []) if l.strip()
                and not l.lstrip().startswith(("=== RUN", "=== PAUSE", "=== CONT"))]
        if not res:
            body = [l for l in (r.stdout + r.stderr).splitlines() if l.strip() and not l.startswith("{")]
        for l in body[-15:]:
            print("    " + l.strip()[:300])
if len(passed) <= 20:
    print("\n".join(passed))
elif passed:
    print("PASS ... %d tests passed" % len(passed))
print("== %d test(s), %d failed" % (n, failed))
sys.exit(1 if failed else 0)
"""


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class Bug:
    """The bug of an APR workdir (`cvebench checkout -v buggy`)."""

    def __init__(self, workdir):
        with open(os.path.join(workdir, "cvebench.bug")) as fh:
            self.pid, self.bid, variant = fh.read().split()
        if variant != "buggy":
            raise SystemExit(f"workdir is a {variant} checkout; APR needs -v buggy")
        self.workdir = workdir
        self.pdir = os.path.join(FW_ROOT, "projects", self.pid)
        self.version = next(l.split(",")[2] for l in open(os.path.join(self.pdir, "commit-db"))
                            if l.startswith(f"{self.bid},"))
        self.name = f"{self.pid}-{self.bid}"
        self.lang, self.project = PROJECTS[self.pid]
        self.src = f"/build/{self.pid}-{self.version}"       # the built tree inside the image
        self.image = f"cvebench:{self.pid}-{self.bid}-buggy"

    def env(self, tree):
        """Environment for commands run in the sandbox on tree <tree> (a copy, for OpenHands)."""
        if self.lang == "go":
            return {"CVEBENCH_SRC": tree, "GOCACHE": "/root/.cache/go-build"}
        return {"HAPROXY_SRC": tree}

    def ensure_image(self, tag="apr"):
        if sh(["docker", "image", "inspect", self.image]).returncode != 0:
            print(f"[{tag}] building {self.image} (sandbox image)", flush=True)
            if subprocess.run([CVEBENCH, "compile", "-p", self.pid, "-b", self.bid]).returncode != 0:
                raise SystemExit(f"cannot build {self.image}")


def make_args(bug):
    """The benchmark's make arguments for this bug (MAKE_ARGS + tests/<bid>/build.env EXTRA_MAKE)."""
    extra = ""
    benv = os.path.join(bug.pdir, "tests", bug.bid, "build.env")
    if os.path.exists(benv):
        m = re.search(r'^EXTRA_MAKE="?([^"\n]*)"?', open(benv).read(), re.M)
        extra = m.group(1) if m else ""
    return f"{MAKE_ARGS} {extra}".strip()


def _install(sbx, scripts):
    os.makedirs(os.path.join(sbx, "bin"), exist_ok=True)
    for name, body in scripts:
        p = os.path.join(sbx, "bin", name)
        with open(p, "w") as fh:
            fh.write(body)
        os.chmod(p, 0o755)


def _relevant_ids(bug, sbx):
    ids = set()  # related regression tests = trigger + relevant ids
    for f in ("trigger_tests", "relevant_tests"):
        p = os.path.join(bug.pdir, f, bug.bid)
        if os.path.exists(p):
            ids |= {l.strip() for l in open(p) if l.strip()}
    with open(os.path.join(sbx, "relevant_ids.txt"), "w") as fh:
        fh.write("\n".join(sorted(ids)) + "\n")


def _clean(text, keep_comments, redact, comment):
    """A trigger test as agents see it: the crafted triggers' comments explain buggy vs fixed
    behaviour (and name functions), so full-line comments go; CVE ids are redacted."""
    if not keep_comments:
        text = "".join(l for l in text.splitlines(keepends=True) if not l.lstrip().startswith(comment))
    if redact:
        text = re.sub(r"CVE-\d{4}-\d+", "CVE-XXXX-XXXXX", text)
    return text


def write_helpers(bug, sbx, keep_comments=False, redact=True):
    """Populate <sbx> (mounted read-only at /cvebench): bin/ (cvebench-build, cvebench-test,
    cvebench-setup) and the failing tests. Returns the failing tests' text as the agent sees it."""
    if bug.lang == "go":
        return _write_helpers_go(bug, sbx, keep_comments, redact)
    _install(sbx, [("cvebench-build", BUILD_SH.replace("__MAKE_ARGS__", make_args(bug))),
                   ("cvebench-test", TEST_SH), ("cvebench-setup", SETUP_C)])
    trig = _clean(open(os.path.join(bug.workdir, "cvebench-tests", "trigger.vtc")).read(), keep_comments, redact, "#")
    with open(os.path.join(sbx, "trigger.vtc"), "w") as fh:
        fh.write(trig)
    _relevant_ids(bug, sbx)    # resolved to reg-test paths by cvebench-setup
    globs = [l.strip() for l in open(os.path.join(bug.pdir, "tests", bug.bid, "regtests.txt"))
             if l.strip() and not l.lstrip().startswith("#")]
    with open(os.path.join(sbx, "globs.txt"), "w") as fh:
        fh.write("\n".join(globs) + "\n")
    return trig


def _go_test_text(bug, tid, keep_comments, redact):
    """(file, source) of a Go trigger test as the agent sees it: a crafted (overlay) test file
    whole; an upstream test's function, or for a table subtest its table entry."""
    pkg, name = tid.split("::", 1)
    top, _, sub = name.partition("/")
    overlay = os.path.join(bug.pdir, "tests", bug.bid, "overlay", pkg)
    tree = os.path.join(bug.workdir, f"{bug.pid}-{bug.version}", pkg)
    for d in (overlay, tree):
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not f.endswith("_test.go"):
                continue
            text = open(os.path.join(d, f), errors="replace").read()
            m = re.search(rf"^func {re.escape(top)}\(", text, re.M)
            if not m:
                continue
            rel = f"{pkg}/{f}"
            if d == overlay:
                return rel, _clean(text, keep_comments, redact, "//")
            lines = text[m.start():].splitlines()
            end = next((i for i, l in enumerate(lines) if l.startswith("}")), len(lines) - 1)
            body = lines[:end + 1]
            if sub:   # the table entry named like the subtest (go test turns spaces into _)
                at = next((i for i, l in enumerate(body) if sub.replace("_", " ") in l or sub in l), None)
                if at is not None:
                    st = next((i for i in range(at, -1, -1) if body[i].strip() == "{"), at)
                    ind = len(body[st]) - len(body[st].lstrip())
                    en = next((i for i in range(at, len(body)) if body[i].strip().startswith("}")
                               and len(body[i]) - len(body[i].lstrip()) == ind), at)
                    return rel, _clean(f"// a table entry of {top}\n" + "\n".join(body[st:en + 1]) + "\n",
                                       keep_comments, redact, "//")
            if len(body) > 150:
                body = body[:150] + [f"\t// ... {len(body) - 150} more lines, see {rel}"]
            return rel, _clean("\n".join(body) + "\n", keep_comments, redact, "//")
    return None, ""


def _write_helpers_go(bug, sbx, keep_comments, redact):
    _install(sbx, [("cvebench-build", GO_BUILD_SH), ("cvebench-test", GO_TEST_PY), ("cvebench-setup", SETUP_GO)])
    tdir = os.path.join(bug.pdir, "tests", bug.bid)
    trig_ids = [l.strip() for l in open(os.path.join(tdir, "trigger.txt")) if l.strip() and not l.startswith("#")]
    with open(os.path.join(sbx, "trigger_ids.txt"), "w") as fh:
        fh.write("\n".join(trig_ids) + "\n")
    _relevant_ids(bug, sbx)
    m = re.search(r'^BUILD_PKGS="?([^"\n]*)"?', open(os.path.join(tdir, "build.env")).read(), re.M)
    with open(os.path.join(sbx, "build_pkgs.txt"), "w") as fh:
        fh.write("\n".join((m.group(1) if m else "").split()) + "\n")
    pkgs = [l.strip() for l in open(os.path.join(tdir, "packages.txt")) if l.strip() and not l.startswith("#")]
    with open(os.path.join(sbx, "test_pkgs.txt"), "w") as fh:
        fh.write("\n".join(pkgs) + "\n")
    ov = os.path.join(tdir, "overlay")
    if os.path.isdir(ov):      # crafted trigger tests, comments stripped like the vtc triggers
        for root, _, files in os.walk(ov):
            for f in files:
                dst = os.path.join(sbx, "overlay", os.path.relpath(os.path.join(root, f), ov))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "w") as fh:
                    fh.write(_clean(open(os.path.join(root, f)).read(), keep_comments, redact, "//"))
    parts, shown = [], set()
    for tid in trig_ids:
        rel, text = _go_test_text(bug, tid, keep_comments, redact)
        parts.append(f"=== {tid}" + (f"  ({rel})" if rel else ""))
        if text and (rel, text) not in shown:
            parts.append(text.rstrip())
            shown.add((rel, text))
    return "\n".join(parts) + "\n"


def hide_cmd(tree):
    """Shell command deleting from a source tree what reveals the fix: the fixed release's
    CHANGELOG (HAProxy: a file; Kubernetes: a directory) and patch backups (*.orig / *.rej —
    a buggy image built before --no-backup-if-mismatch holds <file>.orig = the FIXED file)."""
    return f"rm -rf {tree}/CHANGELOG && find {tree} \\( -name '*.orig' -o -name '*.rej' \\) -type f -delete"


def setup_cmd(src, also=()):
    """Shell command (run as root in the sandbox before the agent starts): delete the
    upstream fix patch left by the image build and hide_cmd() the tree <src> and any other
    copy of it in the sandbox (`also`: e.g. the image's own /build tree when the agent works
    on a copy), run the project's cvebench-setup (HAProxy: resolve the relevant reg-tests;
    Go: crafted trigger tests into the tree, Go cache usable), and mark the start time for
    export_patch."""
    return ("rm -f /tmp/fix.patch /tmp/candidate.patch && "
            + "".join(f"{hide_cmd(t)} && " for t in (src, *also)) +
            f"/cvebench/bin/cvebench-setup {src} && touch /tmp/.cvebench-start")


def fl_hints(results, spec, top):
    tool, _, formula = spec.partition(":")
    p = os.path.join(results, "fl", tool, "ranking.json")
    if not os.path.exists(p):
        raise SystemExit(f"--fl {spec}: {p} not found (cvebench fl -t {tool})")
    fs = json.load(open(p))["formulas"]
    name = formula or next(iter(fs))
    if name not in fs:
        raise SystemExit(f"--fl {spec}: no formula {name} in {p} (have: {', '.join(fs)})")
    rows = fs[name]["top"][:top]
    return name, "\n".join(f"  {i + 1}. {r['file']}:{r['line']}" for i, r in enumerate(rows))


def bug_report(trig, failure, results=None, fl=None, fl_top=10, bug=None):
    """The bug report every agent gets: the failing test(s), the output [, FL hints]."""
    if bug is not None and bug.lang == "go":
        task = (f"The {bug.project} Go tests below fail on the current source tree "
                "(test ids are <package dir>::<Test>[/<subtest>]).\n\n"
                f"<failing_tests>\n{trig.strip()}\n</failing_tests>\n\n"
                f"<test_output>\n{failure.strip()}\n</test_output>")
    else:
        task = ("The HAProxy regression test below fails on the current source tree.\n\n"
                f"<failing_test path=\"/cvebench/trigger.vtc\">\n{trig.strip()}\n</failing_test>\n\n"
                f"<test_output>\n{failure.strip()}\n</test_output>")
    if fl:
        fname, hints = fl_hints(results, fl, fl_top)
        task += ("\n\nA fault-localization tool ranks these source lines as the most suspicious "
                 f"({fl.split(':')[0]}, {fname}); they may or may not be the right place to fix:\n{hints}")
    return task


# where an agent's edits are taken from: HAProxy's C sources; Go non-test sources (no vendor/)
EXPORT_FIND = {
    "c": "find src include \\( -name '*.c' -o -name '*.h' \\)",
    "go": "find . \\( -path ./vendor -o -path ./_output -o -path './.*' \\) -prune -o -name '*.go' ! -name '*_test.go'",
}


def export_patch(cid, tree, workdir, out_patch, bug=None):
    """Copy the source files the agent created or changed in container <cid> (tree <tree>;
    HAProxy: *.c / *.h under src/ and include/; Go: non-test *.go outside vendor/) onto a
    copy of <workdir> and export them with `cvebench diff`. Writes <out_patch> if the diff
    is non-empty; returns (files, diff)."""
    find = EXPORT_FIND[bug.lang if bug is not None else "c"]
    r = subprocess.run(["docker", "exec", cid, "bash", "-c",
                        f"cd {tree} && {find} -newer /tmp/.cvebench-start -type f -print0 "
                        "| tar --null -T - -cf -"], capture_output=True)
    names, diff = [], ""
    with tempfile.TemporaryDirectory() as tmp:
        wd = os.path.join(tmp, "wd")
        shutil.copytree(workdir, wd, symlinks=True)
        dst = os.path.join(wd, os.path.basename(tree))
        if r.stdout:
            with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
                members = [m for m in tf.getmembers() if m.isfile() and not m.name.startswith(("/", ".."))]
                names = [m.name[2:] if m.name.startswith("./") else m.name for m in members]
                tf.extractall(dst, members=members, filter="data")
        if names:
            diff = sh([CVEBENCH, "diff", "-w", wd]).stdout
    if diff.strip():
        with open(out_patch, "w") as fh:
            fh.write(diff)
    return names, diff


def patch_lines(diff):
    return sum(1 for l in diff.splitlines() if l[:1] in ("+", "-") and not l.startswith(("+++", "---")))


def default_model(base_url, prefix="openai/"):
    """First model an OpenAI-compatible server lists, as a litellm model name."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=10) as r:
            return prefix + json.load(r)["data"][0]["id"]
    except Exception as e:
        raise SystemExit(f"no model given and cannot list models at {base_url}: {e}")
