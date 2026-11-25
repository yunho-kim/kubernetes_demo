# Program image for Darjeeling on a Go bug (Kubernetes): the bug's buggy image (tree built,
# warm Go build cache) plus the crafted trigger tests, /cvebench/build (test binaries of the
# suite packages) and /cvebench/run-test (one test). Built by `run` (tag carries a recipe hash).
ARG BUGGY_IMAGE
FROM ${BUGGY_IMAGE}
ARG SRC
RUN rm -f /tmp/fix.patch /tmp/candidate.patch
# crafted trigger tests (tests/<bid>/overlay), where cvebench's own validation puts them
COPY overlay/ ${SRC}/
COPY test_pkgs.txt /cvebench/test_pkgs.txt
COPY build-go.sh /cvebench/build
COPY run-test-go.sh /cvebench/run-test
RUN chmod 755 /cvebench/build /cvebench/run-test
WORKDIR ${SRC}
