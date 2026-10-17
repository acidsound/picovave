#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Render the picovave screen on the host and report what it contains.

    python3 tools/preview_picovave.py

Builds picovave/host/preview.c - the firmware's own app layer (src/picovave_p8.h) and the
vendored femto8 engine, compiled for the host with only the hh_* HAL swapped - then runs it,
converts the screen to build/picovave/preview.png and prints a summary plus an ASCII view.
Nothing here touches hardware.

The binary it leaves at build/picovave/preview is also what tools/verify_final.py drives with
an input script, so the idle screen and a scripted screen are both produced by the same code
the simulator runs.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_picovave as bp                                  # noqa: E402  (the engine list)
import pvcart                                                # noqa: E402  (the default cart)

SRC = Path(__file__).resolve().parents[1]
OUT = SRC / "build" / "picovave"

# The host is the third target for the same engine: the same defines as the device and the
# wasm (so all three compile the engine's device branch), its own seam instead of seam_sim.c /
# seam_device.c, and the host's libc instead of the toolchain's - so no femto8/stub/device.
DEFINES = ["-DOS_FREERTOS", "-D__DA1470x__", "-DENABLE_AUDIO"]
INCLUDES = [
    "femto8/seam",              # gdi.h, FreeRTOS.h, retro_heap.h, ble_controller.h, pthread.h
    "femto8/src",
    "femto8/src/data",
    "femto8/src/lexaloffle",
    "femto8/src/lua",
    "femto8/src/lodepng",
    "src",
    "host",
    "build/picovave",
]


def gen_cart_header():
    """Bake the default cart into build/picovave/cart_default.h: a host preview has no flash store."""
    out = OUT / "cart_default.h"
    out.write_text(pvcart.header(origin="tools/preview_picovave.py from tools/pvcart.py"))


def build():
    """Compile build/picovave/preview (and its cart) and return the executable's path."""
    OUT.mkdir(parents=True, exist_ok=True)
    objdir = OUT / "hostobj"
    objdir.mkdir(parents=True, exist_ok=True)
    gen_cart_header()
    inc = ["-I" + str(SRC / p) for p in INCLUDES]
    paths = [*bp.engine_core(), SRC / "host" / "seam_host.c", SRC / "host" / "preview.c"]
    objs = []
    for path in paths:
        # the engine is vendored and noisy; picovave's own two files are held to -Wall -Wextra
        warn = ["-Wall", "-Wextra"] if path.parent.name == "host" else ["-w"]
        obj = objdir / (path.stem + ".o")
        r = subprocess.run(["cc", "-O2", *warn, *DEFINES, *inc, "-c", str(path), "-o", str(obj)],
                           capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stdout + r.stderr)
            raise SystemExit("preview: %s failed to compile" % path.name)
        objs.append(str(obj))
    exe = OUT / "preview"
    r = subprocess.run(["cc", "-O2", "-o", str(exe), *objs], capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit("preview: link failed")
    return exe


def main():
    exe = build()
    run = subprocess.run([str(exe), str(OUT / "preview.ppm"), str(OUT / "preview.txt")],
                         capture_output=True, text=True)
    if run.returncode:
        sys.stderr.write(run.stdout + run.stderr)
        raise SystemExit("preview: run failed")
    print(run.stdout.strip())

    ppm = OUT / "preview.ppm"
    try:
        from PIL import Image
    except ImportError:
        print("preview: Pillow missing, leaving %s" % ppm)
        return 0
    img = Image.open(ppm)
    img.save(OUT / "preview.png")
    hist = sorted(img.getcolors(240 * 240) or [], reverse=True)
    print("preview %s  %s  %d colours" % (OUT / "preview.png", img.size, len(hist)))
    for count, col in hist[:6]:
        print("   %6d px  #%02x%02x%02x" % (count, col[0], col[1], col[2]))
    print()
    print((OUT / "preview.txt").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
