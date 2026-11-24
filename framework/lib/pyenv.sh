#!/usr/bin/env bash
# pyenv.sh — the benchmark's own Python for the tool adapters (framework/tools/<tool>/).
#
# A Python adapter runs in a private venv built only from things the benchmark owns:
#   * the interpreter — a uv-managed CPython (python-build-standalone) at the exact version in
#     the tool's versions.env (PYTHON_VERSION=3.12.12), installed into framework/.python/:
#     never the system Python, never the user's own uv Python store;
#   * the dependencies — the tool's committed requirements.lock (the full tree, with hashes),
#     resolved from its requirements.in by `pyenv.sh lock` and installed exactly
#     (`uv pip sync`: missing packages added, others removed).
# The one host requirement is uv. A uv on PATH is used when it is at least UV_MIN_VERSION
# (pyenv.env); otherwise the pinned static release is downloaded into framework/.uv/<version>/.
# uv reads no pip.conf, and here no uv.toml either (UV_NO_CONFIG) and none of the caller's
# index/resolver UV_* variables, so host Python configuration cannot change what is installed.
#
# In a tool's prepare.sh:
#     source "$HERE/../../lib/pyenv.sh"
#     pyenv_ensure "$HERE"      # $HERE/.venv: pinned interpreter, synced to requirements.lock, stamped
#
# On the command line:
#     framework/lib/pyenv.sh ensure <tooldir>
#     framework/lib/pyenv.sh lock   <tooldir> [uv pip compile options]
#         re-resolve <tooldir>/requirements.in into requirements.lock for the tool's
#         PYTHON_VERSION / PYTHON_LOCK_PLATFORM; pins already in the lock are kept unless
#         --upgrade or --upgrade-package <name> is given. Commit both files.
#     framework/lib/pyenv.sh python <X.Y.Z>   install that CPython (once) and print its path
#     framework/lib/pyenv.sh uv [args]        the benchmark's uv with the settings used here
#
# .venv/.cvebench-stamp (python version, lock sha256, uv version) is written after a successful
# sync; framework/lib/venvcheck.py compares it with the tool directory (`cvebench tools`, and
# every adapter's run before it uses the venv).
# shellcheck shell=bash

PYENV_FW="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"          # .../framework
# shellcheck source=pyenv.env
source "$PYENV_FW/lib/pyenv.env"
PYENV_PYTHON_DIR="$PYENV_FW/.python"
PYENV_STAMP=.cvebench-stamp
PYENV_UV="${PYENV_UV:-}"

_pyenv_die() { echo "[x] $*" >&2; exit 1; }
_pyenv_ver_ge() { [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" == "$2" ]]; }   # $1 >= $2
_pyenv_var() {   # <versions.env> <NAME>: the value of NAME=... (the file is not sourced)
  [[ -f "$1" ]] && sed -n "s/^$2=\([^[:space:]#]*\).*/\1/p" "$1" | tail -1; true; }

# ---- uv ----
_pyenv_fetch_uv() {   # <dest>: download the pinned static uv, check its sha256, install it at <dest>
  local dest="$1" tmp
  [[ "$(uname -s)/$(uname -m)" == Linux/x86_64 ]] ||
    _pyenv_die "no pinned uv for $(uname -s)/$(uname -m): install uv >= $UV_MIN_VERSION (https://docs.astral.sh/uv/)"
  command -v curl >/dev/null || _pyenv_die "curl not found (needed once, to download uv $UV_VERSION)"
  echo "[+] downloading uv $UV_VERSION into ${dest%/*}" >&2
  tmp="$(mktemp -d)"
  if ! curl -fsSL --retry 3 -o "$tmp/uv.tar.gz" "$UV_URL"; then rm -rf "$tmp"; _pyenv_die "download failed: $UV_URL"; fi
  if ! echo "$UV_SHA256  $tmp/uv.tar.gz" | sha256sum -c --status -; then
    rm -rf "$tmp"; _pyenv_die "sha256 mismatch: $UV_URL (expected $UV_SHA256, pyenv.env)"; fi
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp" --strip-components=1        # uv-x86_64-unknown-linux-musl/{uv,uvx}
  mkdir -p "${dest%/*}" && install -m 0755 "$tmp/uv" "$dest.part" && mv -f "$dest.part" "$dest"
  rm -rf "$tmp"
}

pyenv_uv() {   # sets PYENV_UV: $CVEBENCH_UV, else the host's uv if new enough, else the pinned download
  [[ -n "$PYENV_UV" ]] && return 0
  local u v
  if [[ -n "${CVEBENCH_UV:-}" ]]; then PYENV_UV="$CVEBENCH_UV"; return 0; fi
  u="$(command -v uv || true)"
  if [[ -n "$u" ]]; then
    v="$("$u" --version 2>/dev/null | awk '{print $2}')"
    if [[ -n "$v" ]] && _pyenv_ver_ge "$v" "$UV_MIN_VERSION"; then PYENV_UV="$u"; return 0; fi
    echo "[+] uv on PATH ($u, ${v:-unknown version}) is older than $UV_MIN_VERSION; using the pinned uv $UV_VERSION" >&2
  fi
  u="$PYENV_FW/.uv/$UV_VERSION/uv"
  [[ -x "$u" ]] || _pyenv_fetch_uv "$u"
  PYENV_UV="$u"
}

pyenv_uv_run() {   # uv <args> with the benchmark's settings and without the caller's UV_* configuration
  pyenv_uv
  (
    for v in $(compgen -e); do
      case "$v" in
        UV_CACHE_DIR|UV_PYTHON_INSTALL_MIRROR|UV_HTTP_TIMEOUT|UV_NATIVE_TLS|UV_OFFLINE|UV_NO_PROGRESS|UV_LINK_MODE|UV_CONCURRENT_*) ;;
        UV_*|VIRTUAL_ENV|CONDA_PREFIX|PYTHONPATH|PYTHONHOME) unset "$v";;
      esac
    done
    export UV_NO_CONFIG=1 UV_PYTHON_INSTALL_DIR="$PYENV_PYTHON_DIR" UV_PYTHON_PREFERENCE=only-managed \
           UV_PYTHON_DOWNLOADS=manual PYTHONNOUSERSITE=1
    exec "$PYENV_UV" "$@"
  )
}

# ---- interpreter ----
pyenv_python() {   # <X.Y.Z>: install that CPython under framework/.python (once); print its path
  local ver="$1" py out
  [[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || _pyenv_die "PYTHON_VERSION must be an exact X.Y.Z version (got '$ver')"
  pyenv_uv_run python install --no-bin --quiet "cpython@$ver" >&2 || _pyenv_die "uv could not install CPython $ver"
  py="$(cd / && pyenv_uv_run python find --no-project --system "cpython@$ver")" || _pyenv_die "CPython $ver not found after install"
  out="$("$py" -I -c 'import os, sys; print("%d.%d.%d" % sys.version_info[:3]); print(os.path.realpath(sys.prefix))')" ||
    _pyenv_die "$py does not run"
  [[ "${out%%$'\n'*}" == "$ver" && "${out#*$'\n'}" == "$PYENV_PYTHON_DIR"/* ]] ||
    _pyenv_die "uv returned $py ($(echo $out)), not CPython $ver under $PYENV_PYTHON_DIR"
  echo "$py"
}

_pyenv_venv_ok() {   # <venv> <X.Y.Z>: its interpreter runs, is <X.Y.Z> and lives under framework/.python
  local out
  out="$("$1/bin/python" -I -c 'import os, sys; print("%d.%d.%d" % sys.version_info[:3]); print(os.path.realpath(sys.base_prefix))' 2>/dev/null)" || return 1
  [[ "${out%%$'\n'*}" == "$2" && "${out#*$'\n'}" == "$PYENV_PYTHON_DIR"/* ]]
}

# ---- venv ----
pyenv_ensure() {   # <tooldir>: <tooldir>/.venv with the pinned CPython, synced exactly to requirements.lock
  local dir tool lock venv ver py uvver keep="" e n
  dir="$(cd "$1" && pwd -P)"; tool="${dir##*/}"; lock="$dir/requirements.lock"; venv="$dir/.venv"
  ver="$(_pyenv_var "$dir/versions.env" PYTHON_VERSION)"
  [[ -n "$ver" ]] || _pyenv_die "$tool: no PYTHON_VERSION in $dir/versions.env"
  [[ -f "$lock" ]] || _pyenv_die "$tool: no $lock (framework/lib/pyenv.sh lock $dir)"
  [[ -L "$venv" ]] && _pyenv_die "$venv is a link to another tool's venv; prepare that tool"
  export PYTHONNOUSERSITE=1
  pyenv_uv; uvver="$("$PYENV_UV" --version | awk '{print $2}')"
  py="$(pyenv_python "$ver")"
  echo "[+] $tool: CPython $ver (framework/${py#"$PYENV_FW"/}), uv $uvver ($PYENV_UV)"

  if [[ -e "$venv" ]] && ! _pyenv_venv_ok "$venv" "$ver"; then
    # built from another interpreter (system python3, the user's uv store, another version) or
    # broken: rebuild it, carrying over data the tool keeps inside .venv (llmao's hf-cache)
    echo "[+] $tool: rebuilding $venv (its interpreter is not the pinned CPython $ver)"
    keep="$dir/.venv.keep.$$"; mkdir -p "$keep"
    for e in "$venv"/* "$venv"/.[!.]*; do
      [[ -e "$e" || -L "$e" ]] || continue
      case "${e##*/}" in
        bin|include|lib|lib64|share|pyvenv.cfg|.gitignore|CACHEDIR.TAG|.lock|"$PYENV_STAMP") ;;
        *) mv "$e" "$keep/";;
      esac
    done
    rm -rf "$venv"
  fi
  if [[ ! -e "$venv" ]]; then
    pyenv_uv_run venv --quiet --no-project --prompt "$tool" --python "$py" "$venv" ||
      _pyenv_die "$tool: uv venv failed"
  fi
  if [[ -n "$keep" ]]; then
    for e in "$keep"/* "$keep"/.[!.]*; do [[ -e "$e" || -L "$e" ]] && mv "$e" "$venv/"; done
    rmdir "$keep"
  fi

  rm -f "$venv/$PYENV_STAMP"
  local hashes=(--require-hashes)          # VCS pins (darjeeling) have no hash; their commit is the pin
  grep -qE '^[A-Za-z0-9._-]+ @ git\+' "$lock" && hashes=()
  pyenv_uv_run pip sync --quiet --strict --python "$venv/bin/python" "${hashes[@]}" "$lock" ||
    _pyenv_die "$tool: uv pip sync $lock failed"
  _pyenv_venv_ok "$venv" "$ver" || _pyenv_die "$tool: $venv/bin/python is not CPython $ver after the sync"
  n="$(grep -cE '^[A-Za-z0-9._-]+(==| @ )' "$lock")"
  {
    echo "# written by framework/lib/pyenv.sh after a successful sync; checked by framework/lib/venvcheck.py"
    echo "python=$ver"
    echo "lock_sha256=$(sha256sum "$lock" | cut -d' ' -f1)"
    echo "uv=$uvver"
    echo "interpreter=$(realpath --relative-to="$PYENV_FW" "$py")"
    echo "synced=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$venv/$PYENV_STAMP"
  echo "[+] $tool: $venv = requirements.lock ($n packages)"
}

pyenv_lock() {   # <tooldir> [uv pip compile options]: requirements.in -> requirements.lock
  local dir tool ver plat py
  dir="$(cd "$1" && pwd -P)"; shift; tool="${dir##*/}"
  ver="$(_pyenv_var "$dir/versions.env" PYTHON_VERSION)"
  [[ -n "$ver" ]] || _pyenv_die "$tool: no PYTHON_VERSION in $dir/versions.env"
  [[ -f "$dir/requirements.in" ]] || _pyenv_die "$tool: no $dir/requirements.in"
  plat="$(_pyenv_var "$dir/versions.env" PYTHON_LOCK_PLATFORM)"; plat="${plat:-$PYTHON_LOCK_PLATFORM_DEFAULT}"
  pyenv_uv
  py="$(pyenv_python "$ver")"
  echo "[+] $tool: resolving requirements.in for CPython $ver on $plat"
  (cd "$dir" && pyenv_uv_run pip compile requirements.in --output-file requirements.lock --quiet \
      --python "$py" --python-version "$ver" --python-platform "$plat" --generate-hashes \
      --index-url "$PYPI_INDEX_URL" --emit-index-url \
      --custom-compile-command "framework/lib/pyenv.sh lock framework/tools/$tool" "$@") ||
    _pyenv_die "$tool: uv pip compile failed"
  echo "[+] $tool: $dir/requirements.lock ($(grep -cE '^[A-Za-z0-9._-]+(==| @ )' "$dir/requirements.lock") packages)"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  cmd="${1:-}"; shift || true
  case "$cmd" in
    ensure) [[ $# -eq 1 ]] || _pyenv_die "usage: pyenv.sh ensure <tooldir>"; pyenv_ensure "$1";;
    lock)   [[ $# -ge 1 ]] || _pyenv_die "usage: pyenv.sh lock <tooldir> [uv pip compile options]"; pyenv_lock "$@";;
    python) [[ $# -eq 1 ]] || _pyenv_die "usage: pyenv.sh python <X.Y.Z>"; pyenv_python "$1";;
    uv)     pyenv_uv_run "$@";;
    *) sed -n '2,/^# shellcheck/{/^# shellcheck/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}"; exit 2;;
  esac
fi
