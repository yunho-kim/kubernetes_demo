# cvebench-fangelix: the FAngelix image + VTest. Built by prepare.sh.
ARG FANGELIX_IMAGE
FROM ${FANGELIX_IMAGE}
RUN apt-get update -q && apt-get install -y -q --no-install-recommends libpcre2-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
COPY VTest /opt/VTest
RUN make -C /opt/VTest -j"$(nproc)" >/dev/null && cp /opt/VTest/vtest /usr/local/bin/vtest
