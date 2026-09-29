"""aprsandbox — shared pieces of the APR adapters that build and test the program in the
bug's image (framework/tools/miniswe, openhands, darjeeling). Stdlib only.

An agent's commands run in a sandbox container of the bug's buggy image
(cvebench:<pid>-<bid>-buggy: the source tree already built with the benchmark's
flags, vtest installed) — docs/INTEGRATION.md 5.7. This module provides

  * the helper commands mounted read-only at /cvebench (cvebench-build,
    cvebench-test) and the failing test as the agent sees it (comments stripped,
    CVE ids redacted),
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
        self.src = f"/build/haproxy-{self.version}"          # the built tree inside the image
        self.image = f"cvebench:{self.pid}-{self.bid}-buggy"

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


def write_helpers(bug, sbx, keep_comments=False, redact=True):
    """Populate <sbx> (mounted read-only at /cvebench): bin/, trigger.vtc, relevant ids,
    reg-test globs. Returns the failing test's text as the agent sees it."""
    os.makedirs(os.path.join(sbx, "bin"), exist_ok=True)
    for name, body in (("cvebench-build", BUILD_SH.replace("__MAKE_ARGS__", make_args(bug))), ("cvebench-test", TEST_SH)):
        p = os.path.join(sbx, "bin", name)
        with open(p, "w") as fh:
            fh.write(body)
        os.chmod(p, 0o755)
    trig = open(os.path.join(bug.workdir, "cvebench-tests", "trigger.vtc")).read()
    if not keep_comments:  # the crafted triggers' comments explain buggy vs fixed behaviour (and name functions)
        trig = "".join(l for l in trig.splitlines(keepends=True) if not l.lstrip().startswith("#"))
    if redact:
        trig = re.sub(r"CVE-\d{4}-\d+", "CVE-XXXX-XXXXX", trig)
    with open(os.path.join(sbx, "trigger.vtc"), "w") as fh:
        fh.write(trig)
    ids = set()  # related regression tests = trigger + relevant ids (resolved to paths by setup_cmd)
    for f in ("trigger_tests", "relevant_tests"):
        p = os.path.join(bug.pdir, f, bug.bid)
        if os.path.exists(p):
            ids |= {l.strip() for l in open(p) if l.strip()}
    with open(os.path.join(sbx, "relevant_ids.txt"), "w") as fh:
        fh.write("\n".join(sorted(ids)) + "\n")
    globs = [l.strip() for l in open(os.path.join(bug.pdir, "tests", bug.bid, "regtests.txt"))
             if l.strip() and not l.lstrip().startswith("#")]
    with open(os.path.join(sbx, "globs.txt"), "w") as fh:
        fh.write("\n".join(globs) + "\n")
    return trig


def setup_cmd(src):
    """Shell command (run as root in the sandbox before the agent starts): delete the
    upstream fix patch left by the image build and the fixed release's CHANGELOG,
    resolve the relevant reg-tests, and mark the start time for export_patch."""
    return (f"rm -f /tmp/fix.patch /tmp/candidate.patch {src}/CHANGELOG && "
            f"cd {src} && ls $(cat /cvebench/globs.txt) 2>/dev/null | sort -u | while read -r v; do "
            "id=$(printf '%s' \"$v\" | sed 's#reg-tests/##; s#[/.]#_#g'); "
            "grep -qx \"$id\" /cvebench/relevant_ids.txt && echo \"$v\"; done > /tmp/relevant_vtc.txt; "
            "chmod 644 /tmp/relevant_vtc.txt; touch /tmp/.cvebench-start")


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


def bug_report(trig, failure, results=None, fl=None, fl_top=10):
    """The bug report every agent gets: the failing test, its output [, FL hints]."""
    task = ("The HAProxy regression test below fails on the current source tree.\n\n"
            f"<failing_test path=\"/cvebench/trigger.vtc\">\n{trig.strip()}\n</failing_test>\n\n"
            f"<test_output>\n{failure.strip()}\n</test_output>")
    if fl:
        fname, hints = fl_hints(results, fl, fl_top)
        task += ("\n\nA fault-localization tool ranks these source lines as the most suspicious "
                 f"({fl.split(':')[0]}, {fname}); they may or may not be the right place to fix:\n{hints}")
    return task


def export_patch(cid, tree, workdir, out_patch):
    """Copy the *.c / *.h under src/ and include/ that the agent created or changed in
    container <cid> (tree <tree>) onto a copy of <workdir> and export them with
    `cvebench diff`. Writes <out_patch> if the diff is non-empty; returns (files, diff)."""
    r = subprocess.run(["docker", "exec", cid, "bash", "-c",
                        f"cd {tree} && find src include \\( -name '*.c' -o -name '*.h' \\) -newer /tmp/.cvebench-start -print0 "
                        "| tar --null -T - -cf -"], capture_output=True)
    names, diff = [], ""
    with tempfile.TemporaryDirectory() as tmp:
        wd = os.path.join(tmp, "wd")
        shutil.copytree(workdir, wd, symlinks=True)
        dst = os.path.join(wd, os.path.basename(tree))
        if r.stdout:
            with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
                members = [m for m in tf.getmembers() if m.isfile() and not m.name.startswith(("/", ".."))]
                names = [m.name for m in members]
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
