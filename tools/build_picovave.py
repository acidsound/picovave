#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build picovave.fwsc: the picovave app for the M-Vave SMK-37 Pro plus its update package.

    python3 tools/build_picovave.py [--product NAME] [--skip-package] [--loader PATH]

picovave is SELF-CONTAINED: it never reads repo/. The platform it stands on is vendored into
picovave/platform/ (a copy of the hardware-proven Makkuro37 / Felucca stack), so this directory
builds and flashes on its own - no dependency on the makkuro37 project:

  * startup      picovave/platform/firmware/crt0.S          (the SPL entry)
                 picovave/platform/firmware/hal/fm1_vec.S    (the exception/IRQ vector table)
                 picovave/platform/firmware/hal/fm1_isr.S    (the IRQ wrappers: isr_timer5, isr_alnk0)
  * linker       picovave/platform/firmware/app.ld          (the Felucca memory map, XIP length
                                                           patched to the SMK-37 Pro app slot)
  * flags        picovave/platform/tools/build.py's CFLAGS  (-Os -ffunction-sections ...)
  * app          picovave/src/picovave.c                       (picovave's own source; it includes the
                                                           vendored HAL + USB/OTA stack)
  * engine       picovave/femto8/**                          (the vendored PICO-8 engine, the device
                                                           seam femto8/seam/seam_device.c, and the
                                                           device stubs femto8/stub/device/)
  * cart         picovave/build/picovave/cart_default.h      (baked from picovave/tools/pvcart.py)
  * loader       picovave/platform/tools/build.py build_loader() -> ota.bin
  * package      picovave/platform/tools/fm1pkg_make.py --board smk37 (the same packer, a copy)

The vendored copy carries picovave's own identity: the USB-MIDI device announces itself as
"picovave" (usb.c STR2), not Makkuro37. Platform fixes are applied to picovave/platform/ on their
own schedule (a copy, not a link: picovave and makkuro37 do not depend on each other).

Outputs: build/picovave/picovave.bin (app), build/picovave/picovave.flash.bin (logical flash) and
build/picovave.fwsc (the installable package).

The JieLi toolchain is Linux x86-64 only; on macOS each tool runs in a linux/amd64
container (JIELI_DOCKER=0 forces native).
"""
import argparse
import os
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path

import sources
import pvcart

SRC = Path(__file__).resolve().parents[1]
PICOVAVE = SRC
PLAT = PICOVAVE / "platform"           # picovave's vendored copy of the platform - never repo/
FEMTO8 = PICOVAVE / "femto8"           # the vendored PICO-8 engine + its seams
OUT = SRC / "build" / "picovave"
OBJ = OUT / "obj"
DOCKER_IMAGE = os.environ.get("JIELI_DOCKER_IMAGE", "debian:bookworm-slim")
APP_XIP = 0x02000120
ENTRY_STUB = bytes.fromhex("04818000")       # pi32v2: goto +4 ; rts
CFLAGS = ["-Os", "-ffunction-sections", "-fno-builtin", "-Wall", "-Wno-unused-function",
          "-Wno-macro-redefined"]
if os.environ.get("PV_DEBUG_REMOTE"):
    CFLAGS.append("-DPV_DEBUG_REMOTE")
LINE = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\t(.*)$")
ROM_LO, ROM_HI = 0xFFC00000, 0xFFD00000       # the chip's mask ROM: nothing may call or load it
RAM_ORIGIN = 0x01C00E00                       # app.ld: start of the RAM region (RAMTEXT ends here)
RAM_LEN = 0x73200                             # app.ld: .data/.bss AND the Lua heap, one region
# The engine's boot buffers (p8_emu.c). Upstream allocates m_memory, m_cart_memory,
# m_overlay_memory, m_lua_script plus the file/text-parse scratch (m_file_buffer,
# m_decompression_buffer, m_temp_cart_memory, m_temp_lua_script); picovave's cooked-cart-only patch
# leaves the scratch NULL, reads the cartridge ROM out of the cart itself, and materialises the Lua
# source (sized to the cart's own script, not to the 64 KiB maximum) only while a cart loads.
# ENGINE_HEAP is what the engine holds for the whole time a cart runs (PICO-8's 64 KiB address space
# + the overlay screen); LOAD_PEAK_HEAP is the worst load moment (+ a full-size source buffer), and
# that is what has to fit alongside a cart while it loads. The arena itself is NOT a constant here:
# it is _pool_top - _pool_start, measured from the link (app.ld puts .pool straight after .bss), so
# every byte of static the app gives up is a byte of heap and nothing can drift.
# How much a CART needs cannot be computed here - Lua costs 10-20x the source - so it is measured
# instead: the simulator's wasm links the SAME allocator (femto8/stub/device/alloc.c through
# sim/device_alloc.c, the default tools/build_sim.py flavor) and runs the cart inside this same
# arena, so sim_test.mjs and the page report what the device's pool really did - peak and refusals.
ENGINE_HEAP = (65536 + 8192)
LOAD_PEAK_HEAP = (ENGINE_HEAP + 65536)        # + the Lua source buffer, live only during a load
PV_POOL_GUARD = 1024                          # femto8/stub/device/alloc.c's red zone
MIN_LUA_HEAP = 16 * 1024

# femto8 is compiled exactly as the simulator compiles it (-DOS_FREERTOS -D__DA1470x__ -DENABLE_AUDIO:
# the engine's device branch), with the device's own stub/seam in place of the wasm ones. The
# include order matters: femto8/stub/device supplies this target's <stdio.h> (newlib's is unusable
# without a syscall layer and would drag in __srget_r), and femto8/seam/device supplies <dirent.h>
# (newlib's is "#error not supported" for pi32v2). Both must precede the system directories.
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
ENGINE_INCLUDES = [
    "femto8/stub/device",
    "femto8/seam",
    "femto8/seam/device",
    "femto8/src",
    "femto8/src/data",
    "femto8/src/lexaloffle",
    "femto8/src/lua",
    "femto8/src/lodepng",
]
APP_INCLUDES = [*ENGINE_INCLUDES, "platform/firmware/hal", "platform/firmware/src", "src",
                "build/picovave"]
LIBDIR = "pi32v2/lib"


def toolchain():
    tc = os.environ.get("JIELI_TOOLCHAIN", str(Path.home() / ".jieli" / "toolchain"))
    if not (Path(tc) / "pi32v2" / "bin" / "clang").exists():
        raise SystemExit("build_picovave: JIELI_TOOLCHAIN must point at the JieLi toolchain "
                         "(tools/get_toolchain.sh); looked in %s" % tc)
    return Path(tc).resolve()


def use_docker():
    native = platform.system() == "Linux" and platform.machine() in ("x86_64", "AMD64")
    return os.environ.get("JIELI_DOCKER", "0" if native else "1") == "1"


def tc(tool, *args):
    """Run one toolchain binary (paths relative to SRC)."""
    rel = [str(Path(a).resolve().relative_to(SRC)) if isinstance(a, Path) else str(a) for a in args]
    if tool == "cc":
        tool, rel = "pi32v2/bin/clang", ["-target", "pi32v2", *rel]
    if use_docker():
        cmd = ["docker", "run", "--rm", "--platform", "linux/amd64", "-v", "%s:/work" % SRC,
               "-v", "%s:/opt/jieli:ro" % toolchain(), "-w", "/work", DOCKER_IMAGE,
               "/opt/jieli/%s" % tool, *rel]
    else:
        cmd = [str(toolchain() / tool), *rel]
    r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit("build_picovave: %s failed" % tool)
    if r.stderr.strip():
        sys.stderr.write(r.stderr)
    return r.stdout


def run_shell(script):
    """Run one shell script inside the toolchain (native, or linux/amd64 in docker)."""
    if use_docker():
        cmd = ["docker", "run", "--rm", "--platform", "linux/amd64", "-v", "%s:/work" % SRC,
               "-v", "%s:/opt/jieli:ro" % toolchain(), "-w", "/work", DOCKER_IMAGE,
               "/bin/sh", "-c", script]
    else:
        cmd = ["/bin/sh", "-c", script]
    r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit("build_picovave: toolchain script failed")
    if r.stderr.strip():
        sys.stderr.write(r.stderr)
    return r.stdout


def tcbin():
    """The toolchain's prefix as the *toolchain* sees it (/opt/jieli under docker)."""
    return "/opt/jieli" if use_docker() else str(toolchain())


def sys_includes():
    """The target's C library and clang's own headers, as -isystem (searched AFTER the -I list,
    so femto8/stub/device's <stdio.h> and femto8/seam/device's <dirent.h> shadow newlib's)."""
    root = Path(toolchain()) if not use_docker() else Path("/opt/jieli")
    lib = root / "common" / "lib" / "clang"
    ver = sorted(p.name for p in lib.glob("4.*"))[-1] if lib.exists() else "4.0.1"
    return [root / "pi32v2" / "include", lib / ver / "include"]


def rel(p):
    return str(Path(p).resolve().relative_to(SRC))


def engine_core():
    """The vendored engine itself: p8_*.c, the Lua core, the two decompressors, and the two
    stubs every target needs (the GUI, and lodepng - .p8.png is a browser convenience only).

    The device, the wasm simulator and the host preview all compile exactly this list, with
    the same defines, and differ only in the seam/ implementation they add."""
    engine = [
        "src/p8_emu.c", "src/p8_lua.c", "src/p8_parser.c", "src/p8_input.c",
        "src/p8_audio.c", "src/p8_dsp.c", "src/p8_cstore.c", "src/queue.c", "src/strtcpy.c",
        "src/lexaloffle/p8_compress.c", "src/lexaloffle/pxa_compress_snippets.c",
    ]
    lua = sorted(p.name for p in (FEMTO8 / "src" / "lua").glob("*.c"))
    return ([FEMTO8 / e for e in engine]
            + [FEMTO8 / "src" / "lua" / n for n in lua]
            + [FEMTO8 / "stub" / "p8_gui_stub.c", FEMTO8 / "stub" / "lodepng_stub.c"])


def engine_sources():
    """engine_core() plus the device seam and the device-only libc/allocator stubs."""
    return (engine_core()
            + [FEMTO8 / "seam" / "seam_device.c"]
            + sorted((FEMTO8 / "stub" / "device").glob("*.c")))


def gen_cart_header(out_dir=OUT):
    """Bake the default cart (tools/pvcart.py, a cooked cart) into cart_default.h:
    the cart that runs on a blank device.

    A device with no cart in the store still has to show something, exactly as the simulator
    does (sim/cart_default.h, the same bytes)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "cart_default.h"
    out.write_text(pvcart.header(origin="tools/build_picovave.py from tools/pvcart.py"))
    print("cart     %s  %d B" % (out.relative_to(SRC), pvcart.COOKED_SIZE))


def platform_tools():
    """Import picovave's vendored build/packaging tools."""
    os.environ.setdefault("JIELI_TOOLCHAIN", str(Path.home() / ".jieli" / "toolchain"))
    if not os.environ.get("AC79_SDK") and (Path.home() / "fw-AC79_AIoT_SDK").exists():
        os.environ["AC79_SDK"] = str(Path.home() / "fw-AC79_AIoT_SDK")
    sys.path.insert(0, str(PLAT / "tools"))
    import build as mkbuild                       # noqa: E402
    import fm1pkg_make                            # noqa: E402
    return mkbuild, fm1pkg_make


def get_out(board="smk37"):
    return SRC / "build" / ("picovave" if board == "smk37" else f"picovave-{board}")


def build_loader(mkbuild, board="smk37"):
    """Build the update loader (ota.bin) from Makkuro37's loader sources, unchanged.

    The app image is only half of a usable package: the running firmware reads the package's
    ota.bin entry, stages it and resets, and the SPL then runs it to install flash.bin.
    makkuro37.fwsc ships exactly this loader; picovave ships the identical bytes. Override with
    --loader PATH to use a prebuilt ota.bin.
    """
    out_dir = get_out(board)
    out_dir.mkdir(parents=True, exist_ok=True)
    mkbuild.BOARD = board
    mkbuild.build_loader()                        # writes picovave/platform/build/loader/ota.bin
    ota = (PLAT / "build" / "loader" / "ota.bin").read_bytes()
    (out_dir / "ota.bin").write_bytes(ota)
    return ota


def build_app(fm1pkg_make, product, board="smk37"):
    """Compile + link picovave.bin with Makkuro37's startup, linker map and flags."""
    out_dir = get_out(board)
    out_dir.mkdir(parents=True, exist_ok=True)
    gen_font = subprocess.run([sys.executable, str(SRC / "tools" / "mkfont.py"),
                               str(PICOVAVE / "src" / "picovave_font.h")],
                              capture_output=True, text=True)
    if gen_font.returncode:
        sys.stderr.write(gen_font.stdout + gen_font.stderr)
        raise SystemExit("build_picovave: mkfont failed")
    sys.stdout.write(gen_font.stdout)

    gen_cart_header(out_dir)

    # app.ld: same script, adjusted to each board's XIP length
    app_slot = fm1pkg_make.APP_SLOT
    ld = out_dir / "app.ld"
    txt = (PLAT / "firmware" / "app.ld").read_text()
    if txt.count("LENGTH = 0x8DFBC") != 1:
        raise SystemExit("build_picovave: app.ld: the XIP LENGTH line was not found")
    ld.write_text(txt.replace("LENGTH = 0x8DFBC", "LENGTH = %#x" % app_slot))

    # Everything compiles in ONE toolchain invocation: a docker run costs about a second and
    # there are ~55 translation units (the engine, the Lua core, the device seam and stubs).
    obj = out_dir / "obj"
    obj.mkdir(parents=True, exist_ok=True)
    cc = "%s/pi32v2/bin/clang -target pi32v2" % tcbin()
    sysinc = " ".join("-isystem " + shlex.quote(str(p)) for p in sys_includes())
    board_flag = "-DBOARD_SMK37=1" if board == "smk37" else "-DBOARD_SMK37=0"
    includes = [*APP_INCLUDES, str(out_dir.relative_to(SRC))]
    flaglist = [*CFLAGS, board_flag, '-DFELUCCA_ID="%s"' % product,
                '-DPV_BUILD_TAG="%s"' % sources.build_tag(),
                *ENGINE_DEFINES, *("-I" + p for p in includes)]
    flags = " ".join(shlex.quote(f) for f in flaglist) + " " + sysinc
    objs, lines = [], ["set -e"]
    for s in [PLAT / "firmware" / "crt0.S", PLAT / "firmware" / "hal" / "fm1_vec.S",
              PLAT / "firmware" / "hal" / "fm1_isr.S"]:
        o = obj / (s.stem + ".o")
        lines.append("%s -c %s -o %s" % (cc, rel(s), rel(o)))
        objs.append(o)
    for s in [PICOVAVE / "src" / "picovave.c", *engine_sources()]:
        o = obj / (s.stem + ".o")
        if o in objs:
            raise SystemExit("build_picovave: two sources would write %s" % o.name)
        lines.append("%s %s -c %s -o %s" % (cc, flags, rel(s), rel(o)))
        objs.append(o)
    # --gc-sections: without it every pulled archive member's .text is kept and the image
    # overruns the app slot (the linker script KEEPs the entry and .ram_text).
    elf = out_dir / "picovave.elf"
    lines.append("%s/pi32v2/bin/ld -T %s %s -L%s/%s -lc -lm -lcompiler_rt --gc-sections -Map=%s -o %s"
                 % (tcbin(), rel(ld), " ".join(rel(o) for o in objs), tcbin(), LIBDIR,
                    rel(out_dir / "picovave.map"), rel(elf)))
    run_shell("\n".join(lines))

    for sect in ("text.bin", "data.bin", "ramtext.bin"):
        (out_dir / sect).unlink(missing_ok=True)
    syms = tc("common/bin/objdump", "-t", elf)
    dis = tc("common/bin/objdump", "-d", elf)
    rt = tc("common/bin/objdump", "-d", "-j", ".ram_text", elf)
    (out_dir / "picovave.dis").write_text(dis)
    tc("common/bin/objcopy", "-O", "binary", "-j", ".text", elf, out_dir / "text.bin")
    tc("common/bin/objcopy", "-O", "binary", "-j", ".data", elf, out_dir / "data.bin")
    tc("common/bin/objcopy", "-O", "binary", "-j", ".ram_text", elf, out_dir / "ramtext.bin")

    def symv(name):
        m = re.search(r"^([0-9a-f]+) .*\s" + name + r"$", syms, re.M)
        if not m:
            raise SystemExit("build_picovave: symbol %s not found" % name)
        return int(m.group(1), 16)

    img = bytearray((out_dir / "text.bin").read_bytes())
    for sect, lname in (("ramtext.bin", "_rt_load"), ("data.bin", "_data_load")):
        load = symv(lname)
        blob = (out_dir / sect).read_bytes() if (out_dir / sect).exists() else b""
        if not blob:
            continue
        if load % 4:
            raise SystemExit("build_picovave: %s %#x is not word aligned" % (lname, load))
        if load - APP_XIP < len(img):
            raise SystemExit("build_picovave: %s overlaps the image" % lname)
        img += b"\xff" * (load - APP_XIP - len(img))
        img += blob
    img += b"\xff" * (-len(img) % 4)
    (out_dir / "picovave.bin").write_bytes(img)

    # --- checks -------------------------------------------------------------
    errors, notes = [], []
    if symv("_start") != APP_XIP:
        errors.append("_start is at %#x, not %#x" % (symv("_start"), APP_XIP))
    if img[:4] != ENTRY_STUB:
        errors.append("image starts with %s, not the goto+rts entry stub" % img[:4].hex())
    if len(img) > app_slot:
        errors.append("image %d B exceeds the app slot %#x" % (len(img), app_slot))
    # .ram_text runs with the flash (XIP) switched off: it may touch only SFRs, the stack and
    # RAM buffers - no call into XIP-resident code (picovave/platform/tools/build.py enforces the same).
    rt_calls = [ln for ln in rt.splitlines() if re.search(r"\bcall\b", ln)]
    if rt_calls:
        errors.append(".ram_text contains calls: %s" % rt_calls[:3])
    else:
        notes.append(".ram_text: %d insns, no calls"
                     % len([ln for ln in rt.splitlines() if LINE.match(ln)]))
    # nothing may call into, or load through, an address in the chip's mask ROM. A bare
    # constant that happens to look like one (a mask, an alignment) is not a reference:
    # only control transfers and memory operands are.
    for ln in dis.splitlines():
        mm = LINE.match(ln)
        if not mm:
            continue
        ins = mm.group(3)
        if "[" not in ins and not re.search(r"\b(call|goto|j)\b", ins):
            continue
        for v in re.findall(r"= (-?\d+) <|call -?\d+ <[^:>]*: ([0-9a-f]+) >", ins):
            val = (int(v[0]) & 0xFFFFFFFF) if v[0] else int(v[1], 16) & 0xFFFFFFFF
            if ROM_LO <= val < ROM_HI:
                errors.append("reference to ROM address %#010x: %s" % (val, ins.strip()[:70]))
    bss = symv("_bss_end") - RAM_ORIGIN
    pool_lo, pool_hi = symv("_pool_start"), symv("_pool_top")
    pool_len = pool_hi - pool_lo
    pool_used = symv("_pool_end") - pool_lo
    heap = pool_len - PV_POOL_GUARD - pool_used       # femto8/stub/device/alloc.c's red zone
    notes.append("image %d B (slot %#x)" % (len(img), app_slot))
    notes.append("RAM .data+.bss %d B, then the arena: %d B (%#x..%#x, .pool in use %d B)"
                 % (bss, pool_len, pool_lo, pool_hi, pool_used))
    notes.append("Lua heap %d B (arena - %d B red zone - engine %d B)" % (heap, PV_POOL_GUARD, ENGINE_HEAP))
    notes.append("engine buffers peak at %d B while a cart loads; everything else (%d B) is Lua"
                 % (LOAD_PEAK_HEAP, heap - LOAD_PEAK_HEAP))
    # The pool must end at the stack guard the device wires into fm1_guard.h: a byte past it is a
    # write the guard traps, so _pool_top is not free to drift with app.ld's region length.
    if pool_hi != 0x01C74000:
        errors.append("pool top %#x is not the stack guard 0x01C74000 (app.ld: _guard0)" % pool_hi)
    if bss + pool_len > RAM_LEN:
        errors.append("RAM region overflow: .data+.bss %d + arena %d > %d" % (bss, pool_len, RAM_LEN))
    if heap - LOAD_PEAK_HEAP < MIN_LUA_HEAP:
        errors.append("Lua heap %d B leaves %d B for Lua at load (need %d): shrink the app's statics"
                      " (.data/.bss) - the arena follows them"
                      % (heap, heap - LOAD_PEAK_HEAP, MIN_LUA_HEAP))
    # architecture check: OUR code touches no register - the device register access is Makkuro37's
    # firmware/hal, reused verbatim, so the only thing that can differ from the hardware-proven build
    # is picovave's own screen/input/app logic. A register/window literal, a volatile pointer cast (except
    # of a C object's address), or inline asm other than the empty RING_PUBLISH() barrier. The same
    # MMIO_* rules as picovave/platform/tools/build.py; tools/verify_final.py enforces them too (late). Checking here
    # fails the *build*, as dinghy's build.py does.
    mmio = re.compile(r"\b0x0*(1[0-3][0-9a-f]{3}|4[0-3][0-9a-f]{3}|5[01][0-9a-f]{3}|1ee[0-9a-f]{4}|"
                      r"1c7f[0-9a-f]{3}|20[0-9a-f]{5})u?l?\b"
                      r"|\(\s*(?:const\s+)?volatile\b[^()]*\*\s*\)(?!\s*&)"
                      r"|\b(?:__asm__|asm)\b(?!\s+volatile\s*\(\s*\"\"\s*:::\s*\"memory\"\s*\))", re.I)
    def _mmio_strip(t):
        t = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), t, flags=re.S)
        return re.sub(r"//[^\n]*", "", t)
    mmio_hits = 0
    for f in sorted((PICOVAVE / "src").glob("*.[ch]")):
        if f.name == "picovave_font.h":                    # generated glyph data, no register access
            continue
        for no, line in enumerate(_mmio_strip(f.read_text()).splitlines(), 1):
            if mmio.search(line):
                errors.append("%s:%d touches a register/window outside the HAL: %s"
                              % (f.relative_to(SRC), no, line.strip()[:80]))
                mmio_hits += 1
    if not mmio_hits:
        notes.append("register access stays in picovave/platform/firmware/hal (no MMIO in picovave/src)")
    return bytes(img), errors, notes


def build_target(board, mkbuild, fm1pkg_make, out_path=None, product=None, loader_path=None, sdk=None, skip_pkg=False):
    mkbuild.BOARD = board
    fm1pkg_make.set_board(board)
    product = product or fm1pkg_make.BOARDS[board][2]
    out_dir = get_out(board)
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        out_path = SRC / "build" / ("picovave.fwsc" if board == "smk37" else f"picovave-{board}.fwsc")

    ota_path = loader_path or (out_dir / "ota.bin")
    if loader_path is None:
        print("loader   %d B (built from picovave/platform/firmware/loader for %s)"
              % (len(build_loader(mkbuild, board)), board))
    elif not ota_path.exists():
        raise SystemExit("build_picovave: %s not found" % ota_path)
    else:
        (out_dir / "ota.bin").write_bytes(ota_path.read_bytes())

    img, errors, notes = build_app(fm1pkg_make, product, board=board)
    print(f"[{board.upper()}]")
    for n in notes:
        print("  ok    " + n)
    for e in errors:
        print("  FAIL  " + e)
    if errors:
        raise SystemExit(f"build_picovave: checks failed for {board}")

    if skip_pkg:
        print("app      %s  %d B" % (out_dir / "picovave.bin", len(img)))
        return img, None

    if sdk:
        fm1pkg_make.SDK = sdk
    flash = fm1pkg_make.flash_image(img, fm1pkg_make.KEY)
    pkg = fm1pkg_make.ufw(flash, (out_dir / "ota.bin").read_bytes(), product)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(pkg)
    (out_dir / f"picovave-{board}.flash.bin").write_bytes(flash)
    if board == "smk37":
        (out_dir / "picovave.flash.bin").write_bytes(flash)
        (SRC / "build" / "picovave-smk37.fwsc").write_bytes(pkg)

    print("flash    %d B -> %s" % (len(flash), out_dir / f"picovave-{board}.flash.bin"))
    print("package  %s  %d B, identity %r" % (out_path, len(pkg), product))
    if board == "smk37":
        print("package  %s  %d B, identity %r" % (SRC / "build" / "picovave-smk37.fwsc", len(pkg), product))
    return img, pkg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", choices=["smk37", "fm1", "all"], default="all",
                    help="target board: smk37, fm1, or all (default: all)")
    ap.add_argument("--product", default=None, help="package identity (default: per board)")
    ap.add_argument("--out", type=Path, default=None, help="output .fwsc path (default: build/picovave[-board].fwsc)")
    ap.add_argument("--sdk", type=Path, default=None)
    ap.add_argument("--skip-package", action="store_true")
    ap.add_argument("--loader", type=Path, default=None, help="prebuilt ota.bin (default: build it)")
    a = ap.parse_args()

    if not (PLAT / "tools" / "fm1pkg_make.py").exists():
        raise SystemExit("build_picovave: the vendored platform is missing (expected %s)" % (PLAT / "tools"))
    mkbuild, fm1pkg_make = platform_tools()
    sdk = a.sdk or Path(os.environ.get("AC79_SDK", str(Path.home() / "fw-AC79_AIoT_SDK")))

    boards = ["smk37", "fm1"] if a.board == "all" else [a.board]
    stamp_data = {}
    for b in boards:
        prod = a.product if (len(boards) == 1 and a.product) else None
        out_p = a.out if (len(boards) == 1 and a.out) else None
        img, pkg = build_target(b, mkbuild, fm1pkg_make, out_path=out_p, product=prod,
                                loader_path=a.loader, sdk=sdk, skip_pkg=a.skip_package)
        out_dir = get_out(b)
        out_pkg = out_p or (SRC / "build" / ("picovave.fwsc" if b == "smk37" else f"picovave-{b}.fwsc"))
        if b == "smk37":
            stamp_data["app_sha256"] = sources.sha256_file(out_dir / "picovave.bin")
            if pkg is not None:
                stamp_data["package_sha256"] = sources.sha256_file(out_pkg)
        stamp_data[f"{b}_app_sha256"] = sources.sha256_file(out_dir / "picovave.bin")
        if pkg is not None:
            stamp_data[f"{b}_package_sha256"] = sources.sha256_file(out_pkg)

    stamp_data["build_tag"] = sources.build_tag()
    stamp = sources.write_stamp(stamp_data)
    print("tag      %s (what the device reports once this package is on it)" % sources.build_tag())
    print("stamp    %s" % stamp.relative_to(SRC))
    return 0


if __name__ == "__main__":
    sys.exit(main())
