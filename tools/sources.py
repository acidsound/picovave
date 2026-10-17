#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""The firmware's build inputs, and the stamp that records which revision was built.

verify_final.py exists to say "this package is correct". That claim is only meaningful if
the package was built from the sources now on disk. Once it was not: the pi32v2 link failed
(undefined reference to memcpy) and verification passed anyway, green, against the previous
build's artifacts sitting in build/. A green light on a broken tree is the worst outcome
this project can produce, so every successful build now records the hash of every firmware
input, and verification recomputes them and refuses to pass if anything has moved since.
"""
import hashlib
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
STAMP = SRC / "build" / "picovave" / ".build_stamp.json"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files():
    """Every checked-in file that can change the linked app or the package.

    picovave builds against its own vendored platform (picovave/platform/ - startup, linker map,
    HAL, panel, USB/OTA, loader, packer), so those files are inputs too: if the vendored tree
    moves, the package on disk is no longer current even though picovave/src/ did not change.
    Nothing here reads repo/: the build does not either.
    """
    files = sorted(p for p in (SRC / "src").glob("*") if p.is_file())
    for n in ("build_picovave.py", "picovave_pack.py", "fwsc.py", "mkfont.py", "pvcart.py"):
        files.append(SRC / "tools" / n)
    plat = SRC / "platform"       # the vendored platform: sources, HAL, loader, packer
    files.extend(sorted(p for p in plat.rglob("*")
                        if p.is_file() and p.suffix in (".c", ".h", ".S", ".ld", ".py")
                        and "build" not in p.parts          # skip picovave/platform/build/ outputs
                        and p.name != "fm1_install.py"))    # host flasher: not a build input
    # The vendored PICO-8 engine (femto8/): the device links it, and tools/pvcart.py (above) bakes
    # the default cart from it. carts/ is deliberately NOT an input: the device runs .p8.png carts
    # from the store, so the sample carts change no byte of the ROM or the package.
    files.extend(sorted(p for p in (SRC / "femto8").rglob("*")
                        if p.is_file() and p.suffix in (".c", ".h", ".S", ".ld")
                        and "build" not in p.parts))
    return files


def source_hashes():
    return {str(f.relative_to(SRC)): sha256_file(f) for f in source_files() if f.is_file()}


def build_tag(n=8):
    """A short, deterministic tag for the exact inputs this build is made of. The device reports
    it over USB (the identity reply's NUL padding), so "did my build actually reach the device"
    stops being a guess: flash, then read the identity back and compare."""
    h = hashlib.sha256()
    for k in sorted(source_hashes()):
        h.update(k.encode())
        h.update(source_hashes()[k].encode())
    return h.hexdigest()[:n]


def write_stamp(extra=None):
    data = {"inputs": source_hashes()}
    if extra:
        data.update(extra)
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    return STAMP


def staleness():
    """(changed, added, removed) input paths versus the recorded stamp, or None if no stamp."""
    if not STAMP.exists():
        return None
    old = json.loads(STAMP.read_text()).get("inputs", {})
    new = source_hashes()
    changed = sorted(k for k in old if k in new and old[k] != new[k])
    removed = sorted(k for k in old if k not in new)
    added = sorted(k for k in new if k not in old)
    return changed, added, removed
