#!/usr/bin/env bash
# /cvebench/build — Darjeeling's build step on a Go bug: compile the test binary of every suite
# package (/cvebench/test_pkgs.txt) into /tmp/cvebench-bin/; a compile error fails the build.
cd "$K8S_SRC" || exit 2
mkdir -p /tmp/cvebench-bin
while read -r p; do
  [ -n "$p" ] || continue
  go test -c -o "/tmp/cvebench-bin/$(printf '%s' "$p" | tr / _).test" "./$p" || exit 1
done < /cvebench/test_pkgs.txt
