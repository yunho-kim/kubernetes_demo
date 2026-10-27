#!/usr/bin/env python3
"""gosuite.py — run a Kubernetes bug's Go tests one leaf test at a time, inside a
minimal-pair image (cvebench:kubernetes-<bid>-<variant>).

  gosuite.py --src DIR --tests DIR --out DIR [--owner UID:GID] [--jobs N] [--timeout S] list
  gosuite.py ... outcome --list FILE
  gosuite.py ... cov     --list FILE

--tests is projects/kubernetes/tests/<bid> (mounted read-only):
  packages.txt   the suite: package dirs relative to the source root
  coverpkg.txt   import-path patterns instrumented in cov mode (go test -coverpkg)
  overlay/       files copied into the source tree first (crafted trigger tests)

A test id is "<package dir>::<Test>[/<subtest>...]", a leaf of the package's test tree
(Defects4J's "class::method").
  list     enumerate the leaves with one `go test -json` run per package -> OUT/list.txt
  outcome  run each listed test alone -> OUT/outcomes.txt ("<id> <pass|fail|skip> <package dir>")
  cov      the same with coverage -> OUT/coverage/<n>.json ({"test": id, "covered": {file: [lines]}});
           Go coverage blocks are mapped to the source lines they span.
A listed test that reports no result counts as fail when its binary failed (e.g. the
parent test stopped before reaching it), else as skip.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys

WORK = "/tmp/gosuite"
MODULE = "k8s.io/kubernetes/"


def log(msg):
    print(f"[gosuite] {msg}", file=sys.stderr, flush=True)


def read_list(path):
    if not os.path.exists(path):
        return []
    return [l.strip() for l in open(path) if l.strip() and not l.lstrip().startswith("#")]


def events(text):
    for line in text.splitlines():
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except ValueError:
                pass


def quote_meta(s):
    """Go's regexp.QuoteMeta."""
    return re.sub(r"([\\.+*?()|\[\]{}^$])", r"\\\1", s)


def run_pattern(name):
    # `-run` splits on '/' and matches each level of the test tree separately
    return "/".join("^" + quote_meta(part) + "$" for part in name.split("/"))


def enumerate_leaves(src, pkgs, timeout):
    ids = []
    for pkg in pkgs:
        r = subprocess.run(["go", "test", "-count=1", "-json", f"-timeout={timeout}s", "./" + pkg],
                           cwd=src, capture_output=True, text=True)
        names = []
        for ev in events(r.stdout):
            t = ev.get("Test")
            if t and ev.get("Action") in ("pass", "fail", "skip") and t not in names:
                names.append(t)
        parents = {"/".join(n.split("/")[:i]) for n in names for i in range(1, n.count("/") + 1)}
        leaves = [n for n in names if n not in parents]
        log(f"{pkg}: {len(leaves)} leaf tests ({len(names)} with parents)")
        if not names:
            log(f"{pkg}: no test results; go test said:\n{r.stderr[-2000:]}{r.stdout[-2000:]}")
        ids += [f"{pkg}::{n}" for n in leaves]
    return ids


def build_binary(src, pkg, coverpkg):
    out = os.path.join(WORK, "bin", pkg.replace("/", "_") + ".test")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = ["go", "test", "-c", "-o", out]
    if coverpkg:
        cmd += ["-cover", "-covermode=set", "-coverpkg=" + ",".join(coverpkg)]
    r = subprocess.run(cmd + ["./" + pkg], cwd=src, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        log(f"cannot build the test binary of {pkg}:\n{r.stderr[-3000:]}")
        return None
    return out


def repo_path(imp, src):
    """Coverage-profile file (Go import path) -> source-relative path, or None (third-party)."""
    if imp.startswith(MODULE):
        return imp[len(MODULE):]
    if imp.startswith("k8s.io/"):                       # staging/src/k8s.io/<repo>/...
        p = "staging/src/" + imp
        if os.path.exists(os.path.join(src, p)):
            return p
    return None


def covered_lines(profile, src):
    cov = {}
    with open(profile) as fh:
        for line in fh:
            m = re.match(r"(.+\.go):(\d+)\.\d+,(\d+)\.\d+ \d+ (\d+)$", line.strip())
            if not m or m.group(4) == "0":
                continue
            f = repo_path(m.group(1), src)
            if f is not None:
                cov.setdefault(f, set()).update(range(int(m.group(2)), int(m.group(3)) + 1))
    return {f: sorted(v) for f, v in sorted(cov.items())}


def run_one(src, tid, binary, timeout, profile):
    pkg, name = tid.split("::", 1)
    cmd = ["go", "tool", "test2json", "-t", "-p", pkg, binary, "-test.v", "-test.count=1",
           "-test.run=" + run_pattern(name), f"-test.timeout={timeout}s"]
    if profile:
        cmd.append("-test.coverprofile=" + profile)
    try:
        r = subprocess.run(cmd, cwd=os.path.join(src, pkg), capture_output=True, text=True, timeout=timeout + 60)
    except subprocess.TimeoutExpired:
        return "fail"
    outcome = None
    for ev in events(r.stdout):
        if ev.get("Test") == name and ev.get("Action") in ("pass", "fail", "skip"):
            outcome = ev["Action"]
    return outcome or ("fail" if r.returncode != 0 else "skip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("list", "outcome", "cov"))
    ap.add_argument("--src", required=True)
    ap.add_argument("--tests", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--list", help="test ids to run (outcome / cov)")
    ap.add_argument("--owner", help="chown the outputs to UID:GID")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=300, help="seconds per test")
    a = ap.parse_args()

    overlay = os.path.join(a.tests, "overlay")
    if os.path.isdir(overlay):
        shutil.copytree(overlay, a.src, dirs_exist_ok=True)
    os.makedirs(a.out, exist_ok=True)

    try:
        if a.mode == "list":
            ids = enumerate_leaves(a.src, read_list(os.path.join(a.tests, "packages.txt")), 1800)
            open(os.path.join(a.out, "list.txt"), "w").write("".join(i + "\n" for i in ids))
            log(f"{len(ids)} tests -> list.txt")
            return

        ids = read_list(a.list)
        coverpkg = read_list(os.path.join(a.tests, "coverpkg.txt")) if a.mode == "cov" else []
        binaries = {}
        for pkg in sorted({i.split("::", 1)[0] for i in ids}):
            log(f"building {pkg} test binary" + (" (coverage)" if coverpkg else ""))
            binaries[pkg] = build_binary(a.src, pkg, coverpkg)
        covdir = os.path.join(a.out, "coverage")
        if a.mode == "cov":
            os.makedirs(covdir, exist_ok=True)
            os.makedirs(os.path.join(WORK, "prof"), exist_ok=True)

        def job(n_tid):
            n, tid = n_tid
            binary = binaries.get(tid.split("::", 1)[0])
            if binary is None:
                return tid, "fail"
            profile = os.path.join(WORK, "prof", f"{n}.out") if a.mode == "cov" else None
            oc = run_one(a.src, tid, binary, a.timeout, profile)
            if profile and oc != "skip":
                if os.path.exists(profile):
                    json.dump({"test": tid, "covered": covered_lines(profile, a.src)},
                              open(os.path.join(covdir, f"{n:05d}.json"), "w"))
                else:
                    log(f"no coverage profile from {tid} ({oc}; it died outside its own goroutine?)")
            if profile and os.path.exists(profile):
                os.remove(profile)
            return tid, oc

        results = {}
        with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
            for k, (tid, oc) in enumerate(ex.map(job, enumerate(ids)), 1):
                results[tid] = oc
                if k % 50 == 0 or k == len(ids):
                    log(f"{k}/{len(ids)} tests run")
        with open(os.path.join(a.out, "outcomes.txt"), "w") as fh:
            for tid in ids:
                fh.write(f"{tid} {results[tid]} {tid.split('::', 1)[0]}\n")
        counts = {o: list(results.values()).count(o) for o in ("pass", "fail", "skip")}
        log(f"done: {counts} (mode={a.mode})")
    finally:
        if a.owner:
            subprocess.run(["chown", "-R", a.owner, a.out])


if __name__ == "__main__":
    main()
