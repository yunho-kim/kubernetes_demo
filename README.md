# Kubernetes CVE Reproduction Lab

Self-contained environment for reproducing Kubernetes-ecosystem CVEs against a
local `kind` cluster. Each CVE lives in its own directory under `cves/` with
a uniform set of scripts so reproductions are deterministic and disposable.

## Prerequisites

- Docker (running, current user in the `docker` group)
- Bash, `curl`, `nc` (or `/dev/tcp` redirection — used by some exploits)

`kind` and `kubectl` are vendored into `./bin/` so the lab is self-contained.
Every CVE script sources `lib/common.sh`, which prepends `./bin/` to `PATH`.

## Layout

```
.
├── bin/                  # pinned kind + kubectl + cvebench harness
├── cluster/
│   └── kind-config.yaml  # single-node, NodePort 30080 mapped to host
├── lib/
│   └── common.sh         # cluster up/down, kubectl helpers
└── cves/
    └── <CVE-ID>/
        ├── README.md             # vuln summary, mechanism, expected output
        ├── manifests/            # k8s YAML
        ├── setup.sh              # bring up cluster + deploy vulnerable stack
        ├── exploit.sh            # demonstrate the vulnerability
        ├── verify-fix.sh         # patch to a fixed version, re-run probe
        ├── cleanup.sh            # tear down the workloads (cluster optional)
        └── cvebench.properties   # Defects4J-style metadata for the harness
```

## Reproducing a CVE

```bash
cd cves/CVE-2023-45539
./setup.sh         # creates kind cluster (if needed) + deploys vulnerable stack
./exploit.sh       # sends the malicious request, asserts misbehavior
./verify-fix.sh    # rolls forward to a patched version, asserts safe behavior
./cleanup.sh       # removes workloads (pass --cluster to also delete kind)
```

## Adding a new CVE

Copy an existing `cves/<CVE-ID>/` directory as a template. Keep the four-script
contract (`setup` / `exploit` / `verify-fix` / `cleanup`) so every CVE is
reproduced the same way. Add a `cvebench.properties` file so the harness can
checkout / test / export the CVE alongside the others.

## Available CVEs

| ID | Component | Class | Status |
| --- | --- | --- | --- |
| [CVE-2021-40346](cves/CVE-2021-40346/README.md) | HAProxy 2.0–2.4.3 | HTX header length overflow → HTTP smuggling | ready |
| [CVE-2022-0711](cves/CVE-2022-0711/README.md)   | HAProxy 2.2–2.5.1 | Infinite loop on `Set-Cookie2` → DoS | ready |
| [CVE-2023-45539](cves/CVE-2023-45539/README.md) | HAProxy < 2.8.2  | URI fragment misparse → backend confusion | ready |

## cvebench — Defects4J-style harness

`bin/cvebench` is a small CLI that drives the three reproductions through
the same checkout / setup / test / cleanup contract Defects4J uses, so the
CVEs can be consumed as a fault-localization / automated-patching
benchmark. Each `cves/<CVE-ID>/cvebench.properties` declares the vulnerable
image, the fixed image, the trigger script, and the upstream files modified
by the fix (`classes.modified` — the fault-localization ground truth).

```bash
./bin/cvebench pids                       # list CVE project IDs
./bin/cvebench info -p CVE-2022-0711      # show metadata for one CVE
./bin/cvebench export -p CVE-2022-0711 -q vulnerable.image

# Defects4J-style buggy / fixed workflow
BUGGY=/tmp/cvebench-buggy
FIXED=/tmp/cvebench-fixed

./bin/cvebench checkout -p CVE-2022-0711 -v buggy -w "$BUGGY"
./bin/cvebench setup    -w "$BUGGY"
./bin/cvebench test     -w "$BUGGY"             # rc=1 FAIL (CVE reproduced)
./bin/cvebench cleanup  -w "$BUGGY" --cluster   # recreate cluster between runs

./bin/cvebench checkout -p CVE-2022-0711 -v fixed -w "$FIXED"
./bin/cvebench setup    -w "$FIXED"
./bin/cvebench test     -w "$FIXED"             # rc=0 PASS (patched)
./bin/cvebench cleanup  -w "$FIXED" --cluster
```

**Always pass `--cluster` to `cleanup` between runs.** The buggy HAProxy
pod crash-loops during the exploit, which leaves stale kernel conntrack
entries that misroute traffic to the next pod even after the Service is
recreated. Tearing the kind cluster down (~30s) avoids the flakiness.

Test semantics (matches Defects4J intent):

| Workdir version | Trigger result        | `cvebench test` rc |
| --- | --- | --- |
| `buggy`         | CVE reproduces        | `1` (FAIL)         |
| `fixed`         | CVE does not reproduce| `0` (PASS)         |

The harness only operates on **one CVE at a time** — all three CVEs use the
same `default` namespace and the same `haproxy` Service, so they collide if
you try to setup two at once. Always `cvebench cleanup` between CVEs.
