#!/usr/bin/env python3
"""cover-on-panic.py — make Go's testing package write the coverage profile of a
panicking test (run once in the image, on $GOROOT/src/testing/testing.go).

testing's tRunner recovers a test's panic, reports it and re-panics, which kills
the test binary before -test.coverprofile is written: a buggy version that panics
(e.g. a fake exec running out of scripted calls) would leave its failing trigger
with no coverage. The Go counterpart of the HAProxy images' gcov-flush wrapper:
just before the re-panic, write the profile, only in binaries built with -cover
and run with -test.coverprofile (cvebench coverage), so test outcomes are unchanged.
A panic in a goroutine other than the test's own still loses that test's coverage.
"""
import os
import subprocess

goroot = subprocess.run(["go", "env", "GOROOT"], capture_output=True, text=True, check=True).stdout.strip()
path = os.path.join(goroot, "src", "testing", "testing.go")
anchor = "\t\t\tdidPanic = true\n\t\t\tpanic(err)\n"
src = open(path).read()
assert src.count(anchor) == 1, f"tRunner's re-panic not found in {path}"
hook = ('\t\t\tif CoverMode() != "" && *coverProfile != "" {\n'
        '\t\t\t\tcoverReport() // cvebench: keep the coverage of a panicking test\n'
        '\t\t\t}\n')
open(path, "w").write(src.replace(anchor, hook + anchor))
print(f"patched {path}")
