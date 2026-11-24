#!/usr/bin/env python3
"""venvcheck — is a tool adapter's private venv usable?  (standard library only)

framework/lib/pyenv.sh builds framework/tools/<tool>/.venv from the tool's requirements.lock
with the CPython pinned in its versions.env (PYTHON_VERSION) and records both, after a
successful sync, in .venv/.cvebench-stamp. This module compares that record with the venv
and with the tool directory:

    prepared       the venv matches requirements.lock and PYTHON_VERSION
    stale          the venv runs, but requirements.lock or PYTHON_VERSION changed since it was
                   built, or it was not built by pyenv.sh (no stamp)
    broken         its interpreter does not run, or runs another Python version than the venv
                   was built for (e.g. a venv on /usr/bin/python3 after an OS upgrade)
    missing        no venv ("NOT prepared")
    unmanaged      the tool has no requirements.lock (not a pyenv.sh venv: mbfl, fangelix)

A tool whose .venv links to another tool's venv (cosmosfl -> autofl, liveswe -> miniswe) is
checked against the owner's lock and versions.env.

Adapters call it before they touch the venv, so a stale or broken venv stops with
"run cvebench prepare -t <tool>" instead of a ModuleNotFoundError deep inside the tool:

    import venvcheck
    VENV_PY = venvcheck.require(HERE)     # <HERE>/.venv/bin/python, or exit with the reason
    venvcheck.reexec(HERE, __file__)      # re-run this script inside the venv (no-op inside)

Both also set PYTHONNOUSERSITE=1 in os.environ, so the tool's Python (and its children) never
see ~/.local/lib/pythonX.Y/site-packages.

CLI (used by `cvebench tools`):  venvcheck.py <tooldir>   ->  "<status>\\t<reason>"
"""
import hashlib
import os
import subprocess
import sys

STAMP = ".cvebench-stamp"


def _kv(path):
    """NAME=value lines of a versions.env or stamp (comments and trailing '# ...' ignored)."""
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.split("#", 1)[0].strip()
    except OSError:
        pass
    return out


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _interpreter_version(py):
    """'X.Y.Z' of the venv's interpreter, or None if it does not run."""
    try:
        r = subprocess.run([py, "-I", "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                           capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, PYTHONNOUSERSITE="1"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def status(tooldir):
    """(status, reason) of <tooldir>/.venv — see the module docstring."""
    tooldir = os.path.abspath(tooldir)
    venv = os.path.join(tooldir, ".venv")
    owner = os.path.dirname(os.path.realpath(venv)) if os.path.islink(venv) else tooldir
    lock = os.path.join(owner, "requirements.lock")
    if not os.path.isfile(lock):
        return "unmanaged", "no requirements.lock"
    if not os.path.lexists(venv):
        return "missing", "no .venv"
    if not os.path.exists(venv):
        return "broken", f".venv links to {os.readlink(venv)}, which does not exist"
    py = os.path.join(venv, "bin", "python")
    built_for = _kv(os.path.join(venv, "pyvenv.cfg"))
    built_for = built_for.get("version_info") or built_for.get("version") or ""
    actual = _interpreter_version(py)
    if actual is None:
        return "broken", f"{py} does not run" + (f" (the venv was built for Python {built_for})" if built_for else "")
    if built_for and built_for != actual:
        return "broken", f"the venv was built for Python {built_for}, its interpreter is now {actual}"
    stamp = _kv(os.path.join(venv, STAMP))
    if not stamp:
        return "stale", f"not built by framework/lib/pyenv.sh (no {STAMP}; Python {actual})"
    if stamp.get("python") != actual:
        return "broken", f"the venv was built with Python {stamp.get('python')}, its interpreter is now {actual}"
    want = _kv(os.path.join(owner, "versions.env")).get("PYTHON_VERSION", "")
    if want and want != actual:
        return "stale", f"PYTHON_VERSION is {want}, the venv has {actual}"
    if stamp.get("lock_sha256") != _sha256(lock):
        return "stale", f"{os.path.basename(owner)}/requirements.lock changed since the venv was synced ({stamp.get('synced', '?')})"
    return "prepared", f"Python {actual}, requirements.lock synced {stamp.get('synced', '?')} (uv {stamp.get('uv', '?')})"


def require(tooldir, tool=None):
    """Exit with the reason unless <tooldir>/.venv is prepared; return its python. Sets PYTHONNOUSERSITE=1."""
    tool = tool or os.path.basename(os.path.abspath(tooldir))
    st, why = status(tooldir)
    if st == "unmanaged" and not os.path.exists(os.path.join(tooldir, ".venv", "bin", "python")):
        st, why = "missing", "no .venv"           # e.g. cosmosfl/liveswe before their prepare.sh linked it
    if st not in ("prepared", "unmanaged"):
        word = "not prepared" if st == "missing" else f"venv {st}"
        raise SystemExit(f"[x] {tool}: {word} — {why}\n"
                         f"    run: cvebench prepare -t {tool}   ({os.path.join(tooldir, 'prepare.sh')})")
    os.environ["PYTHONNOUSERSITE"] = "1"
    return os.path.join(os.path.abspath(tooldir), ".venv", "bin", "python")


def reexec(tooldir, script, tool=None):
    """Re-run <script> with sys.argv[1:] inside <tooldir>/.venv after require(); no-op when already inside."""
    venv = os.path.join(os.path.abspath(tooldir), ".venv")
    if os.path.realpath(sys.prefix) == os.path.realpath(venv):
        return
    py = require(tooldir, tool)
    os.execv(py, [py, os.path.abspath(script)] + sys.argv[1:])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: venvcheck.py <tooldir>")
    print("%s\t%s" % status(sys.argv[1]))
