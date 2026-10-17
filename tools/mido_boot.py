#!/usr/bin/env python3
"""Run a MIDI host script on an interpreter that actually has mido.

A stock macOS `python3` (and most Linux distributions' too) has no `mido`, so
`python3 tools/ota_probe.py --list` and `python3 platform/tools/fm1_install.py pkg.fwsc` would die
with ImportError before they ever reach the keyboard - and the keyboard is the only place the
recovery tools can be run from on macOS (the mask-ROM UBOOT needs raw SCSI and stays
Linux/Windows-only, see docs/RECOVERY.md). Rather than making the environment the user's problem,
both scripts ask this module first; it re-executes the script into the first interpreter that can
`import mido, rtmidi`:

  $PICOVAVE_PY, then .venv / .venv-ota / venv beside the script and up to three directories above
  it (which covers picovave/ and the repository root), then ~/.venv-ota and ~/.venv.

`Scripts/python.exe` is used on Windows, so a Windows host gets the same behaviour.

This module never imports mido itself, so importing it has no side effects and costs nothing.
"""

import os
import subprocess
import sys
from pathlib import Path

VENVS = (".venv", ".venv-ota", "venv")
PROBE = "import mido, rtmidi"


def has_mido(py):
    """True when `py` can import what a MIDI host needs (asked in a subprocess: this module must
    not import mido into the caller)."""
    try:
        r = subprocess.run([py, "-c", PROBE], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60)
        return r.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def interpreters(script):
    """Interpreters to try, best guess first: an explicit PICOVAVE_PY, then the virtualenvs a JieLi
    / OTA host is usually installed into next to the script, the package and the repository."""
    out = []
    env = os.environ.get("PICOVAVE_PY")
    if env:
        out.append(env)
    exe = "python.exe" if os.name == "nt" else "python"
    start = Path(script).resolve().parent
    for root in [start, *list(start.parents)[:3]]:
        for venv in VENVS:
            out.append(str(root / venv / ("Scripts" if os.name == "nt" else "bin") / exe))
    home = Path.home()
    for venv in (".venv-ota", ".venv"):
        out.append(str(home / venv / ("Scripts" if os.name == "nt" else "bin") / exe))
    out.append(sys.executable)                              # last: ourselves, to prove it fails
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def ensure(script=None, label=None):
    """True when mido is importable from this interpreter. Otherwise re-execute `script` into one
    that has it (which does not return), or print how to fix the environment and return False."""
    script = os.path.abspath(script or sys.argv[0])
    label = label or Path(script).stem
    if has_mido(sys.executable):
        return True
    tried = []
    for py in interpreters(script):
        if os.path.abspath(py) == os.path.abspath(sys.executable) or not os.path.exists(py):
            continue
        tried.append(py)
        if has_mido(py):
            print("%s: %s has no mido; using %s" % (label, sys.executable, py), file=sys.stderr)
            os.environ["PICOVAVE_PY"] = py
            os.execv(py, [py, script] + sys.argv[1:])
        # not a Python at all (or one too old to run -c): keep looking
    print("%s: no mido in %s" % (label, sys.executable), file=sys.stderr)
    if tried:
        print("  also tried, without mido: %s" % ", ".join(tried), file=sys.stderr)
    print("  install it once:\n"
          "    python3 -m venv .venv-ota && .venv-ota/bin/pip install mido python-rtmidi\n"
          "  or point the script at an interpreter that has it:\n"
          "    PICOVAVE_PY=/path/to/python python3 %s ..." % os.path.relpath(script), file=sys.stderr)
    return False


def import_mido(script=None, label=None):
    """The mido module, from this interpreter or from one this script re-executes into; None when
    there is none anywhere (after printing how to install it)."""
    try:
        import mido
        import rtmidi            # noqa: F401  - mido installs without a backend, and then it cannot open a port
        return mido
    except ImportError:
        pass
    if not ensure(script, label):
        return None
    import mido
    return mido
