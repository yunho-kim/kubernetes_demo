#!/usr/bin/env python3
"""assemble.py <results_dir>

Classify the suite the Defects4J way and emit the SBFL artifacts:
  buggy/outcomes.txt + fixed/outcomes.txt + buggy/coverage/*.json (+ ground_truth.json)
   -> tests.json            (trigger=fail / passing=pass, with coverage paths)
   -> gzoltar/{spectra,matrix,tests}   (GZoltar format)
   -> classification.json   (triggers, relevant, loaded, excluded)

Rules: fail-on-buggy & pass-on-fixed => trigger; pass-on-both => passing;
everything else (fail-on-fixed, skip, no coverage) => excluded.
"""
import sys, os, json, shutil

R = sys.argv[1]
SNAP_WINDOW = 8   # max lines to snap a faulty line to the nearest executable line


def outcomes(path):
    d = {}
    for line in open(path):
        p = line.split()
        if len(p) >= 2:
            d[p[0]] = p[1]
    return d


def covered(tid):
    p = os.path.join(R, "buggy", "coverage", f"{tid}.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p))["covered"]


bug = outcomes(os.path.join(R, "buggy/outcomes.txt"))
fix = outcomes(os.path.join(R, "fixed/outcomes.txt"))

tests, triggers, passing, excluded = [], [], [], []
for tid, boc in bug.items():
    foc = fix.get(tid, "?")
    cov = covered(tid)
    if boc == "fail" and foc == "pass" and cov is not None:
        tests.append({"name": tid, "outcome": "fail", "coverage": f"buggy/coverage/{tid}.json"})
        triggers.append(tid)
    elif boc == "pass" and foc == "pass" and cov is not None:
        tests.append({"name": tid, "outcome": "pass", "coverage": f"buggy/coverage/{tid}.json"})
        passing.append(tid)
    else:
        excluded.append({"test": tid, "buggy": boc, "fixed": foc})

json.dump(tests, open(os.path.join(R, "tests.json"), "w"), indent=2)

# --- GZoltar spectra / matrix / tests ---
covsets, comp = {}, set()
for t in tests:
    c = json.load(open(os.path.join(R, t["coverage"])))["covered"]
    s = {(f, ln) for f, lns in c.items() for ln in lns}
    covsets[t["name"]] = s
    comp |= s
comp = sorted(comp)
cidx = {c: i for i, c in enumerate(comp)}

gz = os.path.join(R, "gzoltar")
os.makedirs(gz, exist_ok=True)
with open(os.path.join(gz, "spectra"), "w") as fh:
    for f, ln in comp:
        fh.write(f"{f}#{ln}\n")
with open(os.path.join(gz, "tests"), "w") as fh:
    fh.write("name,outcome,runtime,stacktrace\n")
    for t in tests:
        fh.write(f"{t['name']},{'PASS' if t['outcome']=='pass' else 'FAIL'},0,\n")
with open(os.path.join(gz, "matrix"), "w") as fh:        # GZoltar matrix: bits + +/-
    for t in tests:
        row = ["0"] * len(comp)
        for c in covsets[t["name"]]:
            row[cidx[c]] = "1"
        fh.write(" ".join(row) + (" +" if t["outcome"] == "pass" else " -") + "\n")

# --- resolve ground truth to executable (covered) lines, line-level ---
# The patch diff can land faulty lines on non-executable text (a replaced
# comment, a brace, an inserted check). gcov only has executable lines, so we
# snap each raw faulty line to the nearest covered line in the same file within
# SNAP_WINDOW; lines with no covered line nearby are dropped as unlocalizable
# (no test exercises them). The raw diff is kept in ground_truth_raw.json.
gt_path = os.path.join(R, "ground_truth.json")
raw_path = os.path.join(R, "ground_truth_raw.json")
if not os.path.exists(raw_path):
    shutil.copy(gt_path, raw_path)
raw_gt = json.load(open(raw_path))

cov_by_file = {}
for (f, ln) in comp:
    cov_by_file.setdefault(f, []).append(ln)
covered_pairs = set(comp)

resolved, dropped, seen = [], [], set()
for l in raw_gt.get("lines", []):
    f, ln = l["file"], int(l["line"])
    if (f, ln) in covered_pairs:
        tgt = ln
    else:
        cands = sorted(cov_by_file.get(f, []), key=lambda x: (abs(x - ln), x))
        tgt = cands[0] if cands and abs(cands[0] - ln) <= SNAP_WINDOW else None
    if tgt is None:
        dropped.append({"file": f, "line": ln}); continue
    if (f, tgt) not in seen:
        seen.add((f, tgt))
        resolved.append({"file": f, "line": tgt, "snapped_from": ln})

json.dump({"files": raw_gt.get("files", []),
           "lines": [{"file": r["file"], "line": r["line"]} for r in resolved],
           "fault_of_omission": raw_gt.get("fault_of_omission", False),
           "resolution": {"window": SNAP_WINDOW, "resolved": resolved, "dropped": dropped}},
          open(gt_path, "w"), indent=2)
print(f"  ground truth: {len(resolved)} executable line(s) "
      f"{[(r['file'], r['line'], '<-'+str(r['snapped_from'])) for r in resolved]}"
      + (f"; dropped {len(dropped)} unlocalizable" if dropped else ""))

# --- relevant (cover a fault file) / loaded (any covered file) ---
gt = json.load(open(gt_path))
fault_files = set(gt.get("files", []))
relevant, loaded = [], set()
for t in tests:
    files = set(json.load(open(os.path.join(R, t["coverage"])))["covered"])
    loaded |= files
    if files & fault_files:
        relevant.append(t["name"])

json.dump({"triggers": triggers, "passing": passing, "relevant": relevant,
           "loaded": sorted(loaded), "excluded": excluded},
          open(os.path.join(R, "classification.json"), "w"), indent=2)

print(f"  triggers={len(triggers)} passing={len(passing)} "
      f"excluded={len(excluded)} components={len(comp)} relevant={len(relevant)}")
print(f"  trigger tests: {triggers}")
