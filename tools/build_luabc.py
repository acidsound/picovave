#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build the cart compiler the page runs: the DEVICE's Lua, compiled to wasm32.

    python3 tools/build_luabc.py [--out DIR]

Output: <out>/luabc.wasm (default build/sim), plus a stamp so a rebuild is a no-op when nothing
that feeds it changed. The page (web/sim/luabc.mjs) loads it to turn a cooked cart into Lua
bytecode before the upload, so the device never runs the compiler.

Why wasm32 and not a native tool: the dump header records sizeof(int), sizeof(size_t),
sizeof(Instruction), sizeof(lua_Number) and the byte order, and the device's luaU_undump compares
them (femto8/src/lua/lundump.c LoadHeader). wasm32 gives 4/4/4/4 little-endian - the same numbers
the pi32v2 build has - so the chunk the page makes is one the device accepts. A 64-bit host build
would say 8 for size_t and be refused.
"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
FEMTO8 = SRC / "femto8"
DOCKER_IMAGE = os.environ.get("EMSDK_IMAGE", "emscripten/emsdk:latest")

FLAGS = [
    "-O2", "-w", "--no-entry", "-sSTANDALONE_WASM",
    "-sSUPPORT_LONGJMP=wasm",          # Lua's setjmp/longjmp: native wasm EH, no JS invoke_* glue
    "-sINITIAL_MEMORY=8388608",
    "-sALLOW_MEMORY_GROWTH=0",         # the page caches views; a growing buffer would detach them
    "-sEXPORTED_FUNCTIONS=_luabc_in,_luabc_out,_luabc_compile,_luabc_verify,_luabc_error",
]
INCLUDES = ["-Ifemto8/src/lua", "-Ifemto8/src/lexaloffle", "-Ifemto8/src"]


def sources():
    lua = sorted((FEMTO8 / "src" / "lua").glob("*.c"))
    return ([SRC / "tools" / "luabc.c"]
            + lua
            + [FEMTO8 / "src" / "lexaloffle" / "p8_compress.c",
               FEMTO8 / "src" / "lexaloffle" / "pxa_compress_snippets.c"])


def have_docker():
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SRC / "build" / "sim"))
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    wasm = out / "luabc.wasm"
    stamp = out / "luabc.stamp"

    srcs = sources()
    h = hashlib.sha256()
    h.update(("\x00".join(FLAGS + INCLUDES)).encode())
    for s in srcs:
        h.update(str(s.relative_to(SRC)).encode())
        h.update(b"\x00")
        h.update(s.read_bytes())
    for hd in sorted((FEMTO8 / "src" / "lua").glob("*.h")):
        h.update(hd.name.encode())
        h.update(hd.read_bytes())
    digest = h.hexdigest()
    if wasm.exists() and stamp.exists() and stamp.read_text().strip() == digest:
        print("luabc    %s  %d B  (unchanged)" % (wasm, wasm.stat().st_size))
        return 0

    if not have_docker():
        raise SystemExit("build_luabc: Docker is not running (the wasm toolchain runs in %s)" % DOCKER_IMAGE)
    cache = SRC / "build" / "emcache"
    cache.mkdir(parents=True, exist_ok=True)
    rel = [os.path.relpath(s, SRC) for s in srcs]
    cmd = ["docker", "run", "--rm", "-v", "%s:/work" % SRC, "-w", "/work",
           "-e", "EM_CACHE=/work/build/emcache", DOCKER_IMAGE,
           "emcc", *FLAGS, *INCLUDES, "-o", os.path.relpath(wasm, SRC), *rel]
    r = subprocess.run(cmd, cwd=SRC, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stdout + r.stderr)
        raise SystemExit("build_luabc: emcc failed")
    stamp.write_text(digest + "\n")
    print("luabc    %s  %d B" % (wasm, wasm.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
