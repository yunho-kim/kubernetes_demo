#!/usr/bin/env python3
"""gtdiff.py <buggy_src> <fixed_src> <relpath>  -> ground_truth.json on stdout

Defects4J-style ground truth: the BUGGY-side lines the fix changes. Computed by
diffing the buggy and fixed source files (the true minimal pair), so it needs no
hand-authored anchors and matches gcov's line numbers in the buggy build.

- replace/delete hunks -> the buggy lines themselves are faulty
- insert hunks (fault of omission, e.g. a missing check) -> the buggy lines
  bracketing the insertion point are the candidates, tagged "side": "before" /
  "after" so assemble.py snaps them backward / forward to the executable
  statement on that side of the missing code
"""
import sys, json, difflib

buggy, fixed, rel = sys.argv[1], sys.argv[2], sys.argv[3]
b = open(buggy, errors="replace").read().splitlines()
f = open(fixed, errors="replace").read().splitlines()

lines = {}          # buggy line -> side (None = a changed line itself)
omission = False
for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=b, b=f, autojunk=False).get_opcodes():
    if tag in ("replace", "delete"):
        for ln in range(i1 + 1, i2 + 1):       # 0-based -> 1-based buggy line numbers
            lines[ln] = None
    elif tag == "insert":
        omission = True
        if i1 >= 1:
            lines.setdefault(i1, "before")      # line before the insertion
        if i1 + 1 <= len(b):
            lines.setdefault(i1 + 1, "after")   # line after the insertion

json.dump({
    "files": [rel],
    "lines": [dict({"file": rel, "line": ln}, **({"side": s} if s else {})) for ln, s in sorted(lines.items())],
    "fault_of_omission": omission,
}, sys.stdout)
