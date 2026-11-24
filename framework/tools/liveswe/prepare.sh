#!/usr/bin/env bash
# prepare.sh — set up the Live-SWE-agent APR adapter.
#
# Live-SWE-agent is a mini-swe-agent configuration (upstream ships no code of its own),
# so this reuses the miniswe adapter: it runs framework/tools/miniswe/prepare.sh unless
# that venv is prepared (framework/lib/venvcheck.py) and links it as .venv here. The
# configuration is config.yaml (upstream version in versions.env). LLM endpoint: as for
# miniswe (OPENAI_BASE_URL, OPENAI_API_KEY [, MSWEA_MODEL]).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MINISWE="$(cd "$HERE/../miniswe" && pwd)"
[[ "$(python3 "$HERE/../../lib/venvcheck.py" "$MINISWE" | cut -f1)" == prepared ]] || "$MINISWE/prepare.sh"
ln -sfn "$MINISWE/.venv" "$HERE/.venv"
"$HERE/.venv/bin/python" -c 'import yaml, sys; c = yaml.safe_load(open(sys.argv[1])); assert c["agent"]["step_limit"] == 250' "$HERE/config.yaml"
echo "[+] liveswe ready (mini-swe-agent venv $MINISWE/.venv; set OPENAI_BASE_URL / OPENAI_API_KEY before 'cvebench apr -t liveswe')"
