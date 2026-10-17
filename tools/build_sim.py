#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the picovave browser simulator: the firmware's own code compiled to WebAssembly.

    python3 tools/build_sim.py [--engine auto|docker|emcc] [--out DIR]

picovave/sim/sim.c includes the firmware's own app layer (src/picovave_p8.h), its cart store and
protocol (src/picovave_cart.h) and the vendored femto8 engine UNCHANGED, and swaps only the HAL
(picovave/sim/picovave_sim_hal.h). So the wasm is the ROM's code, which is the whole point: the
page cannot drift from the hardware because there is no second implementation.

picovave is SELF-CONTAINED: nothing here reads repo/. The page (web/sim/), the audio worklet
(picovave/sim/worklet.js) and the firmware (picovave/sim/sim.c) are all picovave's own.

Outputs (default build/sim):
    sim.wasm        the compiled picovave firmware
    picovave.fwsc      the picovave package the page can flash
    index.html      the page (from web/sim/)
    worklet.js      picovave's AudioWorklet (picovave/sim/worklet.js), served verbatim
    picovave_render.js shared RGB565 conversion
    p8png.mjs       .p8.png -> cooked cart (page + tests)
    sim_test.mjs    the Node boot test
    screen.ppm/png  what the wasm actually drew
    then:  python3 -m http.server -d build/sim 8738
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pvcart                        # the built-in default cart (a cooked cart, tools/pvcart.py)

SRC = Path(__file__).resolve().parents[1]
SIM = SRC / "sim"
WEB = SRC / "web" / "sim"
FEMTO8 = SRC / "femto8"
DOCKER_IMAGE = os.environ.get("EMSDK_IMAGE", "emscripten/emsdk:latest")

EMCC_FLAGS = [
    "-O2", "-std=gnu99", "-w",
    "--no-entry",
    "-sSTANDALONE_WASM",
    "-sSUPPORT_LONGJMP=wasm",       # the engine's setjmp/longjmp; native wasm EH, no JS invoke_* glue
    "-sINITIAL_MEMORY=16777216",
    "-sFILESYSTEM=0",
    "-sALLOW_MEMORY_GROWTH=0",      # the page caches views into memory; a growing buffer detaches them
]
# The C stack the wasm is given. The page's own build is comfortable; the DEVICE is not - app.ld
# hands the engine _ustack_top - _ustack_lo = 0x01C7A000 - 0x01C74100 = 24,320 B, and the hardware
# stack guard FAULTS past _ustack_lo instead of letting the engine run into the statics. Nothing
# else the engine touches differs that much between the wasm and the ROM, so --stack DEV_STACK
# builds the simulator at the device's exact budget: if the run is green, the engine (plus every
# cart in sim_test.mjs) fits the stack the ROM has. (Emscripten's own -sSTACK_OVERFLOW_CHECK=2 is
# not usable as a guard here: with this build's standalone/ctor setup it traps inside
# __wasm_call_ctors at every size, including 1 MiB, so a trap says nothing about the budget.)
STACK_SIM = 1048576
DEV_STACK = 0x01C7A000 - 0x01C74100


def emcc_flags(stack=STACK_SIM):
    return list(EMCC_FLAGS) + ["-sSTACK_SIZE=%d" % stack]

# The vendored PICO-8 engine (femto8/) plus the simulator's own translation unit. femto8's
# __DA1470x__ branch (the one that speaks gdi.h/FreeRTOS) is what the device runs too, so both
# targets compile the same engine with the same defines and swap only the seam/ implementation.
ENGINE_DEFINES = [
    "-DOS_FREERTOS", "-D__DA1470x__", "-DENABLE_AUDIO",
    # Lua's C-call depth limit is sized against the stack the firmware actually has, not the
    # desktop default of 200. Measured from the frames clang emits for this target (pi32v2):
    # one plain C level (luaD_call+luaD_precall+luaV_execute+the C function) is 124 B, and a
    # protected level (pcall / metamethod / callback: luaD_pcall+luaD_rawrunprotected+setjmp)
    # is 316 B. 200 levels is therefore 63 KB of worst case against a 24,320 B user stack, and
    # even the plain path (24,800 B) does not fit: the engine dies in hardware *before* Lua's
    # own "C stack overflow" can fire - which is exactly the crash seen on the device, landing
    # in the first stack store of setjmp (luaD_rawrunprotected's prologue). At 48, the worst
    # case including the 12.5% Lua reserves for the error path itself is 17 KB, which fits.
    # Pure Lua-to-Lua recursion costs no C stack (it iterates inside luaV_execute), so ordinary
    # recursive carts are unaffected; only genuinely deep C nesting now errors instead of dying.
    "-DLUAI_MAXCCALLS=48",
]
# The heap probe (sim/heap_probe.c): rename the allocator so every allocation the engine and Lua
# make is counted, and reported to the page as "what the device's pool would have to hold". The
# engine's own p8_emu.h maps malloc/free onto the seam's rh_* first, exactly as on the device.
HEAP_PROBE_DEFINES = ["-Dmalloc=sim_malloc", "-Dfree=sim_free",
                      "-Drealloc=sim_realloc", "-Dcalloc=sim_calloc"]
ENGINE_INCLUDES = [
    FEMTO8 / "seam",                # gdi.h, FreeRTOS.h, retro_heap.h, ble_controller.h, pthread.h
    FEMTO8 / "src",
    FEMTO8 / "src" / "data",
    FEMTO8 / "src" / "lexaloffle",
    FEMTO8 / "src" / "lua",
    FEMTO8 / "src" / "lodepng",
]


def engine_sources(alloc=None):
    engine = [
        "src/p8_emu.c", "src/p8_lua.c", "src/p8_parser.c", "src/p8_input.c",
        "src/p8_audio.c", "src/p8_dsp.c", "src/p8_cstore.c", "src/queue.c", "src/strtcpy.c",
        "src/lexaloffle/p8_compress.c", "src/lexaloffle/pxa_compress_snippets.c",
    ]
    lua = sorted(p.name for p in (FEMTO8 / "src" / "lua").glob("*.c"))
    return ([FEMTO8 / e for e in engine]
            + [FEMTO8 / "src" / "lua" / n for n in lua]
            + [FEMTO8 / "stub" / "p8_gui_stub.c", FEMTO8 / "stub" / "lodepng_stub.c",
               FEMTO8 / "seam" / "seam_sim.c", SIM / "sim.c", alloc or (SIM / "heap_probe.c")])


def gen_cart_header():
    """Bake the default cart (tools/pvcart.py, a cooked cart) into sim/cart_default.h so the
    wasm carries its own cart and the Node boot test needs no filesystem at all."""
    out = SIM / "cart_default.h"
    text = pvcart.header(origin="tools/build_sim.py from tools/pvcart.py")
    out.write_text(text)
    print("cart     %s  %d B" % (out.relative_to(SRC), pvcart.COOKED_SIZE))


def have_emcc():
    if shutil.which("emcc"):
        return "native"
    emsdk = Path.home() / "emsdk" / "upstream" / "emscripten"
    if (emsdk / "emcc").exists():
        return str(emsdk)
    return None


def have_docker():
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def compile_wasm(engine, out_wasm, flags=None, alloc=None):
    out_wasm.parent.mkdir(parents=True, exist_ok=True)
    srcs = engine_sources(alloc)
    flags = emcc_flags() if flags is None else list(flags)
    if engine == "docker":
        # Absolute paths inside the container: SRC is bind-mounted at /work.
        # EM_CACHE must survive the --rm container or emscripten recompiles its own libc
        # (minutes) on every build; point it at a host-backed directory inside the mount.
        cache = SRC / "build" / "emcache"
        cache.mkdir(parents=True, exist_ok=True)
        src_rel = [os.path.relpath(s, SRC) for s in srcs]
        inc_rel = ["-I" + os.path.relpath(p, SRC) for p in ENGINE_INCLUDES]
        out_rel = os.path.relpath(out_wasm, SRC)
        cmd = ["docker", "run", "--rm", "-v", "%s:/work" % SRC, "-w", "/work",
               "-e", "EM_CACHE=/work/build/emcache", DOCKER_IMAGE,
               "emcc", *flags, *ENGINE_DEFINES, *HEAP_PROBE_DEFINES, *inc_rel,
               "-o", out_rel, *src_rel]
        env = None
    else:
        env = dict(os.environ)
        if engine != "native":                      # an emsdk directory found under $HOME
            env["PATH"] = engine + os.pathsep + env.get("PATH", "")
        incs = ["-I" + str(p) for p in ENGINE_INCLUDES]
        cmd = ["emcc", *flags, *ENGINE_DEFINES, *HEAP_PROBE_DEFINES, *incs,
               "-o", str(out_wasm), *[str(s) for s in srcs]]

    # Compiling the engine (and musl) is minutes under an emulated toolchain. If nothing that
    # feeds the wasm has changed, reuse the last one - the stamp covers every source, the cart
    # and the flags, so a stale wasm can never masquerade as current.
    stamp = out_wasm.with_suffix(".stamp")
    digest = wasm_stamp(srcs, cmd)
    if out_wasm.exists() and stamp.exists() and stamp.read_text().strip() == digest:
        print("wasm     %s  %d B  (unchanged)" % (out_wasm, out_wasm.stat().st_size))
        return out_wasm

    r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True, env=env)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit("build_sim: emcc failed")
    if r.stderr.strip():
        sys.stderr.write(r.stderr)
    stamp.write_text(digest + "\n")
    return out_wasm


def wasm_stamp(srcs, cmd):
    h = hashlib.sha256()
    h.update(("\x00".join(cmd)).encode())
    for s in srcs:
        h.update(str(s.relative_to(SRC)).encode())
        h.update(b"\x00")
        h.update(s.read_bytes())
    h.update(pvcart.cooked())
    # Headers are included, not listed: hash them too, or editing one (p8_emu.h, picovave_p8.h,
    # picovave_cart.h, ...) would silently reuse the previous wasm.
    heads = ((SRC / "src").rglob("*.h"), (SRC / "sim").rglob("*.h"), (SRC / "femto8").rglob("*.h"))
    for hd in sorted({p for g in heads for p in g}):
        h.update(str(hd.relative_to(SRC)).encode())
        h.update(b"\x00")
        h.update(hd.read_bytes())
    return h.hexdigest()


def run_node(script, args, timeout):
    """Run a Node check with a hard time limit. A check that hangs (a wasm stuck in a loop)
    must fail the build, not stall it - a green build has to mean the checks actually ran."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        return subprocess.run([node, str(script), *args], cwd=SRC, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("%s TIMED OUT after %ds" % (Path(script).name, timeout))
        return subprocess.CompletedProcess([], 1, "", "")


def node_test(wasm, out_dir):
    node = shutil.which("node")
    if not node:
        print("build_sim: no node, skipping the boot test")
        return 0
    shutil.copy(SIM / "sim_test.mjs", out_dir / "sim_test.mjs")
    r = run_node(out_dir / "sim_test.mjs", [str(wasm), str(out_dir / "screen.ppm")], 120)
    sys.stdout.write(r.stdout)
    if r.stderr.strip():
        sys.stderr.write(r.stderr)
    return r.returncode


def to_png(out_dir):
    ppm = out_dir / "screen.ppm"
    if not ppm.exists():
        return
    try:
        from PIL import Image
    except ImportError:
        print("build_sim: Pillow missing, leaving %s" % ppm)
        return
    Image.open(ppm).save(out_dir / "screen.png")
    print("screen   %s" % (out_dir / "screen.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["auto", "docker", "emcc"], default="auto")
    ap.add_argument("--out", type=Path, default=SRC / "build" / "sim")
    ap.add_argument("--no-test", action="store_true")
    ap.add_argument("--stack", type=lambda v: int(v, 0), default=STACK_SIM, metavar="BYTES",
                    help="C stack for the wasm; --stack %d is the device's own budget (app.ld)" % DEV_STACK)
    ap.add_argument("--device-alloc", action="store_true",
                    help="run the engine on femto8/stub/device/alloc.c (sim/device_alloc.c) instead "
                         "of the cost-modelling probe: the simulator's own heap becomes the device's")
    a = ap.parse_args()
    a.out = a.out.resolve()                      # a relative --out must not fail the report at the end

    if a.engine == "auto":
        native = have_emcc()
        if native:
            engine = native
        elif have_docker():
            engine = "docker"
        else:
            raise SystemExit("build_sim: no emcc and no usable Docker. Install Emscripten "
                             "(~/emsdk) or start Docker.")
    elif a.engine == "emcc":
        engine = have_emcc() or "native"
        if engine == "native" and not shutil.which("emcc"):
            raise SystemExit("build_sim: emcc not found in PATH or ~/emsdk")
    else:
        if not have_docker():
            raise SystemExit("build_sim: Docker is not running")
        engine = "docker"

    a.out.mkdir(parents=True, exist_ok=True)
    gen_cart_header()
    print("engine   %s" % engine)
    flags = emcc_flags(a.stack)
    print("stack    %d B%s" % (a.stack, "  (the device's own budget)" if a.stack == DEV_STACK else ""))
    alloc = SIM / "device_alloc.c" if a.device_alloc else None
    wasm = compile_wasm(engine, a.out / "sim.wasm", flags, alloc)
    print("wasm     %s  %d B" % (wasm, wasm.stat().st_size))

    for name in ("index.html", "picovave_render.js", "fwsc.mjs", "fm1ota.mjs", "p8png.mjs"):
        shutil.copy(WEB / name, a.out / name)
    # picovave's own AudioWorklet, served verbatim: its audio clock drives the device and makes
    # its sound. picovave's, not a copy of another project's engine.
    shutil.copy(SIM / "worklet.js", a.out / "worklet.js")
    print("page     %s" % (a.out / "index.html"))

    # The sample carts, served next to the page: real .p8.png files the user (and the Node page
    # tests) upload over the device's own SysEx protocol. The default cart is not one of these -
    # it is baked into the wasm (sim/cart_default.h) from tools/pvcart.py.
    carts_out = a.out / "carts"
    carts_out.mkdir(exist_ok=True)
    for p in sorted((SRC / "carts").glob("*.p8.png")):
        shutil.copy(p, carts_out / p.name)

    # The page's DEVICE box has two halves. SIMULATOR verifies and "flashes" the package into
    # its modelled flash; FLASH(USB) serves it to a real device over SysEx. Both use our own
    # picovave.fwsc; the stock v016 is the user's file, never ours.
    src = SRC / "build" / "picovave.fwsc"
    name = "picovave.fwsc"
    if src.exists():
        shutil.copy(src, a.out / name)
        print("package  %s  %d B" % (a.out / name, src.stat().st_size))
    else:
        print("package  %s missing - run tools/build_picovave.py to enable FLASH" % name)

    rc = 0
    node = shutil.which("node")
    if node:
        # Two page checks, both learned from shipped bugs:
        #   check_api.mjs - the page (and the worklet) call only exports the wasm actually has
        #   run_page.mjs  - the page's own code runs without throwing (block-scoped const bug)
        # check_api also reads worklet.js: the wasm's members are now read from inside the
        # AudioWorklet, not only from the page.
        shutil.copy(SIM / "check_api.mjs", a.out / "check_api.mjs")
        shutil.copy(SIM / "run_page.mjs", a.out / "run_page.mjs")
        checks = (
            ("check_api.mjs", [str(wasm), str(a.out / "index.html"), str(a.out / "worklet.js"), str(a.out / "fm1ota.mjs")]),
            ("run_page.mjs", [str(wasm), str(a.out / "index.html")]),
        )
        for script, args in checks:
            r = run_node(a.out / script, args, 120)
            sys.stdout.write(r.stdout)
            if r.stderr.strip():
                sys.stderr.write(r.stderr)
            if r.returncode:
                rc = r.returncode
    else:
        print("build_sim: no node, skipping the page checks")

    if not a.no_test:
        rc = node_test(wasm, a.out) or rc
        to_png(a.out)
    print("serve    python3 -m http.server -d %s 8738" % a.out.relative_to(SRC))
    return rc


if __name__ == "__main__":
    sys.exit(main())
