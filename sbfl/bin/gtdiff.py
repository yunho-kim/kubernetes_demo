#!/usr/bin/env python3
"""gtdiff.py <buggy_src> <fixed_src> <relpath>  -> ground_truth.json on stdout

Defects4J-style ground truth: the BUGGY-side lines the fix changes. Computed by
diffing the buggy and fixed source files (the true minimal pair), so it needs no
hand-authored anchors and matches gcov's line numbers in the buggy build.

- replace/delete hunks -> the buggy lines themselves are faulty
- insert hunks (fault of omission, e.g. a missing check) -> the buggy lines
  bracketing the insertion point are the candidates
"""
import sys, json, difflib

buggy, fixed, rel = sys.argv[1], sys.argv[2], sys.argv[3]
b = open(buggy, errors="replace").read().splitlines()
f = open(fixed, errors="replace").read().splitlines()

lines = set()
omission = False
for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=b, b=f, autojunk=False).get_opcodes():
    if tag in ("replace", "delete"):
        for ln in range(i1 + 1, i2 + 1):       # 0-based -> 1-based buggy line numbers
            lines.add(ln)
    elif tag == "insert":
        omission = True
        if i1 >= 1:
            lines.add(i1)                       # line before the insertion
        if i1 + 1 <= len(b):
            lines.add(i1 + 1)                   # line after the insertion

json.dump({
    "files": [rel],
    "lines": [{"file": rel, "line": ln} for ln in sorted(lines)],
    "fault_of_omission": omission,
}, sys.stdout)
