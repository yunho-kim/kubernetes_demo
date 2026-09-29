"""flcommon — shared helpers for fault-localization tool adapters.

Every FL tool integrated into cvebench (the built-in SBFL ranker and the
adapters under framework/tools/<tool>/) produces the same artifact,

    results/<pid>-<bid>/fl/<tool>/ranking.json

so `cvebench info` / `cvebench summary` can compare tools uniformly. This
module holds the pieces they all need: path normalisation, loading the
classified test suite + per-test coverage, the ground truth, scoring a
ranking against it (rank / EXAM with tie handling) and writing ranking.json.

Standard library only — adapters run outside any tool-specific venv.

ranking.json schema
-------------------
{
  "tool":     "<tool>",
  "failing":  [test, ...],           "passing": [test, ...],
  "n_lines":  <components in the spectrum>,
  "formulas": {                       # one entry per ranking variant the tool produced
     "<name>": {"fault_rank": [best, worst, total] | null,
                "exam": <float> | null,
                "top": [{"file","line","susp", ...}, ...]}
  }
}
"""
import json
import os
from collections import defaultdict


# ---------------------------------------------------------------- paths
def norm(p):
    """Normalise a coverage path to a src-relative one (src/h1.c)."""
    p = p.replace("\\", "/")
    for marker in ("/build/", "./"):
        if p.startswith(marker):
            p = p[len(marker):]
    parts = p.split("/")
    if parts and parts[0].startswith("haproxy-"):     # strip haproxy-x.y.z/
        parts = parts[1:]
    return "/".join(parts)


# ---------------------------------------------------------------- inputs
def load_coverage(path):
    """{(file, line)} covered by one test.

    Accepts the compact format written by the coverage collector
    ({"covered": {file: [lines]}}) and raw gcovr JSON as a fallback.
    """
    with open(path) as fh:
        data = json.load(fh)
    covered = set()
    if "covered" in data:
        for fname, lines in data["covered"].items():
            for ln in lines:
                covered.add((norm(fname), int(ln)))
        return covered
    for f in data.get("files", []):
        fname = norm(f.get("file", ""))
        for ln in f.get("lines", []):
            if ln.get("count", 0) and ln.get("line_number"):
                covered.add((fname, int(ln["line_number"])))
    return covered


def load_tests(results_dir):
    """(failing, passing) test records from tests.json (assemble.py output)."""
    with open(os.path.join(results_dir, "tests.json")) as fh:
        tests = json.load(fh)
    fail = [t for t in tests if t["outcome"] == "fail"]
    pas = [t for t in tests if t["outcome"] == "pass"]
    if not fail:
        raise SystemExit("error: need at least one failing test")
    if not pas:
        raise SystemExit("error: need at least one passing test")
    return fail, pas


def build_spectrum(results_dir):
    """Per-line spectrum {(file, line): (ef, ep, nf, np)} plus test names."""
    fail, pas = load_tests(results_dir)
    ef, ep = defaultdict(int), defaultdict(int)
    for grp, counter in ((fail, ef), (pas, ep)):
        for t in grp:
            for elem in load_coverage(os.path.join(results_dir, t["coverage"])):
                counter[elem] += 1
    F, P = len(fail), len(pas)
    spectrum = {e: (ef[e], ep[e], F - ef[e], P - ep[e]) for e in set(ef) | set(ep)}
    return spectrum, [t["name"] for t in fail], [t["name"] for t in pas]


def load_ground_truth(results_dir):
    p = os.path.join(results_dir, "ground_truth.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        gt = json.load(fh)
    return {"lines": {(norm(l["file"]), int(l["line"])) for l in gt.get("lines", [])},
            "files": {norm(f) for f in gt.get("files", [])},
            "raw": gt}


def load_gzoltar_elements(results_dir):
    """[(file, line)] in column order of results/<bug>/gzoltar/{spectra,matrix}."""
    elems = []
    with open(os.path.join(results_dir, "gzoltar", "spectra")) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            f, ln = line.rsplit("#", 1)
            elems.append((norm(f), int(ln)))
    return elems


# ---------------------------------------------------------------- scoring
def score_against_gt(scored, gt):
    """(best_rank, worst_rank, total, exam) of the first ground-truth hit.

    `scored` is [((file, line), susp), ...] already in rank order; consecutive
    equal suspiciousness values form a tie block. best = optimistic (top of the
    block), worst = pessimistic (bottom). EXAM = (best-1)/total.
    """
    if not gt or not gt["lines"]:
        return None
    total = len(scored)
    seen, i = 0, 0
    while i < len(scored):
        j = i
        while j < len(scored) and scored[j][1] == scored[i][1]:
            j += 1
        tie = scored[i:j]
        if any(e in gt["lines"] for e, _ in tie):
            best, worst = seen + 1, seen + len(tie)
            return best, worst, total, (best - 1) / total if total else 0.0
        seen += len(tie)
        i = j
    return None


def report(name, scored, gt, top, extra=None):
    """Print one ranking the way sbfl-rank always has; return its JSON entry."""
    sc = score_against_gt(scored, gt)
    print(f"=== {name.upper()} ===")
    if sc:
        best, worst, total, exam = sc
        print(f"  fault rank: {best}" + (f"–{worst} (tie)" if worst != best else "")
              + f"  of {total}   EXAM={exam:.4f}")
    elif gt:
        print("  fault rank: NOT FOUND in ranking "
              "(ground-truth lines never executed by any test)")
    topn = scored[:top]
    for k, (e, s) in enumerate(topn, 1):
        mark = "  <== FAULT" if gt and e in gt["lines"] else ""
        detail = f"  {extra(e)}" if extra else ""
        print(f"  {k:>3}. {s:12.4f}  {e[0]}:{e[1]}{detail}{mark}")
    print()
    entry = {"fault_rank": list(sc[:3]) if sc else None,
             "exam": sc[3] if sc else None,
             "top": []}
    for e, s in topn:
        row = {"file": e[0], "line": e[1], "susp": s}
        if extra:
            row["detail"] = extra(e)
        entry["top"].append(row)
    return entry


def write_ranking(out_dir, tool, failing, passing, n_lines, formulas):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "ranking.json")
    with open(path, "w") as fh:
        json.dump({"tool": tool, "failing": failing, "passing": passing,
                   "n_lines": n_lines, "formulas": formulas}, fh, indent=2)
    print(f"wrote {path}")
    return path
