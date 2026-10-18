#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Final verification of the picovave build.

Checks the linked app image, the update package, and - most important - the blast
radius of the install, i.e. exactly which flash bytes a .fwsc write can change.
"""
import re
import shutil
import struct
import subprocess
import sys
import json
import os
from pathlib import Path

# The page is checked by EXECUTING it (picovave/sim/run_page.mjs), never by re-implementing its
# logic here. Two shipped bugs were runtime-only, and both slipped past a re-implementation:
#   "ex.sim_ms is not a function"  (a renamed wasm export)
#   "mod is not defined"           (a const scoped to a try block, used after it)
# run_page.mjs drives the page's real inline module against a stub DOM and the real wasm.

# Compares the wasm simulator's screen with the host preview, pixel for pixel.
# The wasm is STANDALONE_WASM, but the engine's libc pulls a few WASI/syscall imports (time,
# stdio, dirent). picovave never reads a file at runtime - the cart is baked in, or arrives
# over the SysEx protocol sim.c parses itself - so they are stubbed exactly as sim/sim_test.mjs
# and the page stub them: the imports must exist, their results are not used.
WASM_IMPORTS_JS = r"""
const bytes = fs.readFileSync(wasmPath);
let mem = null;
const dv = () => new DataView(mem.buffer);
const putU32 = (p, v) => dv().setUint32(p, v >>> 0, true);
const putU64 = (p, v) => dv().setBigUint64(p, BigInt(v), true);
const imports = {
  env: { __syscall_faccessat: () => -1, __syscall_dup3: () => -1, __syscall_getdents64: () => -1 },
  wasi_snapshot_preview1: {
    environ_sizes_get: (c, b) => { putU32(c, 0); putU32(b, 0); return 0; },
    environ_get: () => 0,
    clock_time_get: (id, prec, out) => { putU64(out, Date.now() * 1000); return 0; },
    fd_close: () => 0,
    fd_write: (fd, iovs, n, written) => { putU32(written, 0); return 0; },
    fd_read: (fd, iovs, n, read) => { putU32(read, 0); return 0; },
    fd_seek: (fd, off, whence, newoff) => { putU64(newoff, 0); return 0; },
  },
};
const { instance } = await WebAssembly.instantiate(bytes, imports);
mem = instance.exports.memory;
"""

PARITY_JS = r"""
import fs from "fs";
const wasmPath = process.argv[2], ppmPath = process.argv[3];
""" + WASM_IMPORTS_JS + r"""
const ex = instance.exports;
if (ex._initialize) ex._initialize();
ex.sim_init();
const fb = new Uint16Array(ex.memory.buffer, ex.sim_fb(), 240 * 240);
const ppm = fs.readFileSync(ppmPath);
const i0 = ppm.indexOf(Buffer.from("255\n")) + 4;
console.log("ok  wasm screen has the expected size");
let bad = -1;
for (let i = 0; i < 240 * 240 && bad < 0; i++) {
  // Recover RGB565 from the PPM. The host expands each 5/6-bit channel by bit
  // replication, so r>>3, g>>2, b>>3 return the original channel exactly.
  const r = ppm[i0 + 3 * i], g = ppm[i0 + 3 * i + 1], b = ppm[i0 + 3 * i + 2];
  const c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
  if (c !== fb[i]) bad = i;
}
console.log((bad < 0 ? "ok  " : "FAIL") + " wasm screen == host preview, pixel for pixel" +
            (bad < 0 ? "" : " (first difference at pixel " + bad + " = " +
             (bad % 240) + "," + ((bad / 240) | 0) + ")"));
let n = 0; for (const c of fb) if (c) n++;
console.log("      " + n + " lit pixels in the wasm screen");
process.exit(bad < 0 ? 0 : 1);
"""

# A sequence that exercises every input: button presses/releases, key presses held across
# frames, encoder detents both ways, pads hit and released with velocity, and the faders and
# both wheels swept. Fed to the wasm and to the host preview; the two screens must agree.
INPUT_SCRIPT = """\
btn 0 1
btn 0 0
btn 5 1
btn 17 1
key 0 1
key 36 1
enc 0 3
enc 3 -2
pad 0 110
pad 15 40
fad 0 100
fad 3 20
whl 0 600
whl 1 80
key 24 1
btn 5 0
key 0 0
key 36 0
pad 15 0
pad 0 0
enc 7 1
btn 17 0
"""

# Drives the wasm with that exact script - every call the page would make - and writes the
# screen to a PPM for comparison with the host preview.
INPUT_PARITY_JS = r"""
import fs from "fs";
const wasmPath = process.argv[2], ppmPath = process.argv[3], scriptPath = process.argv[4];
""" + WASM_IMPORTS_JS + r"""
const ex = instance.exports;
if (ex._initialize) ex._initialize();
ex.sim_init();
for (const line of fs.readFileSync(scriptPath, "utf8").split("\n")) {
  const t = line.trim();
  if (!t || t[0] === "#") continue;
  const [kind, id, v] = t.split(/\s+/);
  if (kind === "btn") ex.sim_button(+id, +v);
  else if (kind === "key") ex.sim_key(+id, +v);
  else if (kind === "enc") ex.sim_enc(+id, +v);
  else if (kind === "pad") ex.sim_pad(+id, +v);
  else if (kind === "fad") ex.sim_fader(+id, +v);
  else if (kind === "whl") ex.sim_wheel(+id, +v);
  ex.sim_frame();
}
const fb = new Uint16Array(ex.memory.buffer, ex.sim_fb(), 240 * 240);
const head = Buffer.from("P6\n240 240\n255\n");
const body = Buffer.alloc(240 * 240 * 3);
for (let i = 0; i < 240 * 240; i++) {
  const c = fb[i], r = (c >> 11) & 31, g = (c >> 5) & 63, b = c & 31;
  // the same 5/6-bit-to-8-bit expansion preview.c writes, so the files are comparable
  body[3 * i] = (r << 3) | (r >> 2);
  body[3 * i + 1] = (g << 2) | (g >> 4);
  body[3 * i + 2] = (b << 3) | (b >> 2);
}
fs.writeFileSync(ppmPath, Buffer.concat([head, body]));
console.log("ok  scripted run saw " + ex.sim_events() + " input edges");
"""

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwsc  # noqa: E402
import sources  # noqa: E402

SRC = Path(__file__).resolve().parents[1]
OUT = SRC / "build" / "picovave"
KEY = 0x980F
FAIL = []


def check(name, ok, detail=""):
    print("  %s %s%s" % ("ok  " if ok else "FAIL", name, (" - " + detail) if detail else ""))
    if not ok:
        FAIL.append(name)


def strip_unbuilt(text, guards=("SDL", "OS_FREERTOS")):
    """The text as a target here compiles it: the regions guarded by `guards` are dropped.

    The vendored engine is shared with femto8's own SDL front end and its FreeRTOS port. Neither
    is built by any target in this repo (there is no SDL front end, and the device's seam stubs
    the RTOS), and both branches allocate their own copy of the cart ROM. A check about what the
    DEVICE allocates has to read the code the device build sees, or the SDL front end's leftovers
    read as a device regression - which is exactly what happened to the "no cart ROM copy" check.
    """
    out, stack, emit = [], [], True
    for line in text.splitlines(True):
        s = line.strip()
        if s.startswith(("#ifdef ", "#ifndef ", "#if ")):
            parent = emit
            parts = s.split()
            drop = s.startswith("#ifdef ") and parts[1] in guards
            stack.append((parent, not drop))
            emit = parent and not drop
        elif s.startswith(("#else", "#elif")):
            if stack:
                parent, was = stack.pop()
                stack.append((parent, not was))
                emit = parent and not was
        elif s.startswith("#endif"):
            parent, _ = stack.pop()
            emit = parent
        if emit:
            out.append(line)
    return "".join(out)


def flash_of(pkg):
    d = fwsc.parse(Path(pkg).read_bytes())
    fl = [f for f in d["files"] if f["entry"].name == "flash.bin"][0]["data"]
    return d, fl


def fwsc_summary(d):
    """The same canonical summary picovave/sim/fwsc_check.mjs prints, for the port cross-check."""
    return {
        "product": d["product"], "blocks": d["blocks"], "logicalSize": d["logical_size"],
        "nfiles": d["header"]["nfiles"], "crcHdrOk": d["header"]["crc_hdr_ok"],
        "crcListOk": d["header"]["crc_list_ok"], "totalOk": d["header"]["total_ok"],
        "chip": d["header"]["chip"], "consts": [int(c, 16) for c in d["header"]["consts"]],
        "files": [{"name": f["entry"].name, "type": f["entry"].type, "index": f["entry"].index,
                   "offset": f["entry"].offset, "length": f["entry"].length, "padded": f["entry"].padded,
                   "crc": f["entry"].crc, "crcOk": f["crc_ok"], "got": f["got"]} for f in d["files"]],
    }


def dec32(buf, off, count, encrypted=True):
    """Decode 32-byte JLFS entries. In the flash head the entries are ENC-scrambled; in the
    app area, fwsc.sfc() has already decrypted them, so they arrive in the clear."""
    out = []
    for i in range(count):
        b = bytearray(buf[off + i * 32:off + (i + 1) * 32])
        if encrypted:
            fwsc.enc(b, 0, 32)
        crc = struct.unpack_from("<H", b, 0)[0]
        out.append(dict(crc=crc, dcrc=struct.unpack_from("<H", b, 2)[0],
                        off=struct.unpack_from("<I", b, 4)[0],
                        size=struct.unpack_from("<I", b, 8)[0],
                        flags=b[12], resvd=b[13], idx=struct.unpack_from("<H", b, 14)[0],
                        name=bytes(b[16:]).split(b"\0")[0].decode("latin-1"),
                        ok=fwsc.crc16(bytes(b[2:32])) == crc))
    return out


def main():
    import build_picovave                            # reuse the toolchain/Docker wrapper
    import preview_picovave                          # the host consumer of the same engine

    print("== build freshness ==")
    # A failed build leaves the previous artifacts in build/, so every check below could pass
    # while the tree is broken. That is not hypothetical: the pi32v2 link failed on an
    # undefined reference to memcpy and verification still reported all checks passed,
    # against a package built from older sources. Compare the sources to the build stamp first.
    st = sources.staleness()
    if st is None:
        check("a build stamp exists", False, "run tools/build_picovave.py")
    else:
        changed, added, removed = st
        moved = changed + added + removed
        check("the package was built from the sources now on disk", not moved,
              ("rebuild needed, changed: " + ", ".join(moved))[:200] if moved
              else "stamp matches %d inputs" % len(sources.source_hashes()))

    print("== image ==")
    elf = OUT / "picovave.elf"
    syms = build_picovave.tc("common/bin/objdump", "-t", elf)
    dis = (OUT / "picovave.dis").read_text()
    img = (OUT / "picovave.bin").read_bytes()
    check("app image exists", bool(img) and len(img) <= 0x97400, "%d B of 0x97400" % len(img))
    # The identity reply's NUL padding carries a tag for the build the device is running, so
    # "did my flash land" is answerable from the host: flash, read the identity back, compare.
    tag = sources.build_tag()
    check("the app image carries this build's tag", tag.encode() in img, tag)
    stamp = json.loads((OUT / ".build_stamp.json").read_text())
    check("the stamp records the same tag", stamp.get("build_tag") == tag, str(stamp.get("build_tag")))
    check("the loader leaves no room for the tag, the app does",
          b"ota-SMK-37 Pro_700" in (OUT / "ota.bin").read_bytes() and len("ota-SMK-37 Pro_700") + 1 + len(tag) >= 27)
    check("entry stub goto+rts", img[:4] == bytes.fromhex("04818000"), img[:4].hex())
    m = re.search(r"^([0-9a-f]+) .*\s_start$", syms, re.M)
    # Lua's C-call depth limit has to fit the user stack: the desktop default of 200 levels needs
    # 63 KB (a protected level is 316 B: luaD_pcall+luaD_rawrunprotected+setjmp) against a 24,320 B
    # stack, so the device used to die in hardware instead of raising Lua's own "C stack overflow"
    # - landing, as seen on the device, in the first stack store of setjmp. The limit is compiled
    # into luaD_call, so read it out of the code: < 48 runs, == 48 errors, >= 54 is an error while
    # erroring (48 + 48/8, the headroom Lua reserves).
    dbody = dis[dis.index("luaD_call:"):dis.index("luaD_call:") + 1200] if "luaD_call:" in dis else ""
    check("the engine caps the C-call depth at 48, which fits the 24,320 B stack",
          "r0 < 48" in dbody and "r0 == 48" in dbody and "r0 >= 54" in dbody,
          "48/54 in luaD_call" if "r0 < 48" in dbody else "LUAI_MAXCCALLS is not 48")
    check("_start at 0x02000120", bool(m) and int(m.group(1), 16) == 0x02000120,
          m.group(0).strip() if m else "missing")
    # picovave's own code names no interrupt primitive: the TIMER5 (matrix + USB poll) and ALNK0
    # (audio) handlers are Makkuro37's, and so are their entry wrappers (hal/fm1_isr.S) and the
    # critical sections (smk_input.h, fm1_flash.h). The device build now installs those two
    # handlers, so the linked image must reference both wrappers.
    def _nocomment(t):
        return re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", t, flags=re.S))
    own_intr = [f.name for f in sorted((SRC / "src").glob("*.[ch]"))
                if re.search(r"\b(?:cli|sti|csync)\b", _nocomment(f.read_text()))]
    check("picovave code names no interrupt primitive (device HAL is Makkuro37's)", not own_intr,
          ", ".join(own_intr[:5]))
    check("image installs Makkuro37's TIMER5 and ALNK0 handlers",
          "isr_timer5" in dis and "isr_alnk0" in dis)
    # The watchdog is the device's: armed in the SPL entry before anything else - before the
    # panel even comes up, so a hang during boot still resets - and fed from the main loop, not
    # from the TIMER5 ISR (feeding it there would defeat the safety net it exists to be).
    dev_c = (SRC / "src" / "picovave.c").read_text()
    cstart = dev_c[dev_c.index("void fm1_cstart(void)"):]
    loop = dev_c[dev_c.index("static void picovave_loop(void)"):]
    # the ISR body: fm1_timer5_irq takes the pushed frame (isr_sp) as its argument, see FM1_ISR
    isr_t5 = dev_c[dev_c.index("void fm1_timer5_irq("):dev_c.index("extern void isr_timer5(void);")]
    check("watchdog armed before the panel comes up",
          cstart.index("fm1_wdt_arm(") < cstart.index("picovave_boot()"))
    check("every loop iteration feeds the watchdog", "fm1_wdt_feed()" in loop)
    check("the watchdog is fed from the loop, not the timer ISR", "fm1_wdt_feed(" not in isr_t5)
    # newlib's pi32v2 setjmp/longjmp use the jmp_buf itself as a scratch stack (sp = jmp_buf + 64),
    # and the app's jmp_buf lives in .bss - so pointing the EMU sp window at [_ustack_lo,
    # _sstack_top] (FM1_GUARD_STACK) traps the first frame of the first boot as "EMU bit 3". The
    # write bands, LUAI_MAXCCALLS and the boot screen's STACK line are what guard the stack now.
    check("the EMU sp window stays unarmed (why: see picovave.c's comment at fm1_guard_enable)",
          "FM1_GUARD_STACK" not in _nocomment(dev_c) and "FM1_EMU_CON &= ~(1u << 3)" in dev_c)

    # architecture check: OUR code touches no register. The device register access is
    # Makkuro37's firmware/hal (reused verbatim), not a parallel re-implementation - so the
    # only thing that can differ from the hardware-proven build is the picovave screen itself.
    # a register/window literal, a volatile pointer cast (except of a C object's address), or
    # inline asm other than the empty RING_PUBLISH() barrier - the same MMIO_* rules as
    # picovave/platform/tools/build.py. picovave.c defines that barrier before including the vendored usb.c.
    mmio = re.compile(r"\b0x0*(1[0-3][0-9a-f]{3}|4[0-3][0-9a-f]{3}|5[01][0-9a-f]{3}|1ee[0-9a-f]{4}|"
                      r"1c7f[0-9a-f]{3}|20[0-9a-f]{5})u?l?\b"
                      r"|\(\s*(?:const\s+)?volatile\b[^()]*\*\s*\)(?!\s*&)"
                      r"|\b(?:__asm__|asm)\b(?!\s+volatile\s*\(\s*\"\"\s*:::\s*\"memory\"\s*\))", re.I)
    offenders = []
    def _strip_c(t):
        t = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), t, flags=re.S)
        return re.sub(r"//[^\n]*", "", t)
    for f in sorted((SRC / "src").glob("*.[ch]")):
        if f.name == "picovave_font.h":                 # generated glyph data, no register access
            continue
        for no, line in enumerate(_strip_c(f.read_text()).splitlines(), 1):
            if mmio.search(line):
                offenders.append("%s:%d" % (f.name, no))
    check("picovave/src touches no register (device HAL is Makkuro37's hal/)", not offenders,
          ", ".join(offenders[:5]))

    print("== package ==")
    d, fl = flash_of(SRC / "build" / "picovave.fwsc")
    h = d["header"]
    check("container header CRC", h["crc_hdr_ok"])
    check("file list CRC", h["crc_list_ok"])
    check("declared size", h["total_ok"], "%d B" % h["total"])
    check("flash.bin CRC", [f for f in d["files"] if f["entry"].name == "flash.bin"][0]["crc_ok"])
    ota = [f for f in d["files"] if f["entry"].name == "ota.bin"][0]
    check("ota.bin CRC", ota["crc_ok"], "%d B" % ota["entry"].length)
    check("ota.bin carries the loader marker", b"FELUCCA-LOADER-1" in ota["data"][:0x200])
    check("identity", d["product"] == "SMK-37 Pro_700", repr(d["product"]))
    check("marker blocks", d["blocks"] == 20)

    print("== packer cross-check (Makkuro37's fm1pkg_make == our independent picovave_pack.py) ==")
    # The device package is packed by Makkuro37's fm1pkg_make (tools/build_picovave.py). Our own
    # packer, reverse engineered from the stock packages alone, must reproduce the same bytes
    # when the isd_config tag matches - so a regression in either packer shows up right here.
    sdk = os.environ.get("AC79_SDK")
    if not sdk:
        print("      (skipped: set AC79_SDK to run the packer cross-check)")
    else:
        import tempfile
        ref = Path(tempfile.gettempdir()) / "picovave_ref_pack.fwsc"
        r = subprocess.run([sys.executable, str(SRC / "tools" / "picovave_pack.py"),
                            "--app", str(OUT / "picovave.bin"), "--loader", str(OUT / "ota.bin"),
                            "--out", str(ref), "--product", "SMK-37 Pro_700",
                            "--isd-tag", "[FELUCCA]\r\n", "--sdk", sdk],
                           capture_output=True, text=True, cwd=SRC)
        if r.returncode:
            check("picovave_pack.py reproduces the package", False,
                  (r.stderr.strip().splitlines() or [""])[-1][:160])
        else:
            same = ref.read_bytes() == (SRC / "build" / "picovave.fwsc").read_bytes()
            check("Makkuro37's packer == our independent packer, byte for byte", same,
                  "" if same else "the two packers disagree")

    print("== package reader (browser fwsc.mjs == tools/fwsc.py) ==")
    # The simulator page verifies packages with web/sim/fwsc.mjs, a hand port of tools/fwsc.py.
    # Ports drift silently; this runs BOTH readers on the real packages and requires the same
    # answer, so a wrong port cannot ship a page that blesses a broken package.
    node = shutil.which("node")
    pkgs = [SRC / "build" / "picovave.fwsc", Path("/Users/spectrum/Downloads/SMK-37_Pro_016.fwsc")]
    if not node:
        check("package reader cross-check ran", False, "no node")
    else:
        r = subprocess.run([node, str(SRC / "sim" / "fwsc_check.mjs"),
                            str(SRC / "web" / "sim" / "fwsc.mjs"), *[str(p) for p in pkgs]],
                           capture_output=True, text=True, cwd=SRC)
        if r.returncode:
            check("fwsc.mjs ran on the real packages", False,
                  (r.stderr.strip().splitlines() or [""])[-1][:200])
        else:
            try:
                js = json.loads(r.stdout)
            except ValueError as e:
                check("fwsc.mjs produced a summary", False, str(e))
                js = {}
            for p in pkgs:
                py = fwsc_summary(fwsc.parse(p.read_bytes()))
                same = js.get(p.name) == py
                check("fwsc.mjs == fwsc.py on %s" % p.name, same,
                      "" if same else "the two readers disagree")

    print("== USB flasher (browser fm1ota.mjs == tools/fm1_install.py) ==")
    # FLASH(USB) serves a package over SysEx with web/sim/fm1ota.mjs, a hand port of
    # tools/fm1_install.py - the updater the hardware trusts. Run the port's wire functions on
    # the checker's vectors and require the reference's answer, so the port cannot drift from
    # the protocol the device actually speaks. The checker also drives the module's whole
    # two-step update against a fake device; that self-test must have completed.
    if not node:
        check("USB flasher cross-check ran", False, "no node")
    else:
        sys.path.insert(0, str(SRC / "platform" / "tools"))
        import fm1_install as fm1  # noqa: E402
        fpkgs = [p for p in (SRC / "build" / "picovave.fwsc",) if p.exists()]
        if not fpkgs:
            check("USB flasher cross-check had a package", False, "run tools/build_picovave.py")
        else:
            r = subprocess.run([node, str(SRC / "sim" / "fm1ota_check.mjs"),
                                str(SRC / "web" / "sim" / "fm1ota.mjs"), *[str(p) for p in fpkgs]],
                               capture_output=True, text=True, cwd=SRC)
            if r.returncode:
                check("fm1ota.mjs ran on the real packages", False,
                      (r.stderr.strip().splitlines() or [""])[-1][:200])
            else:
                try:
                    js = json.loads(r.stdout)
                except ValueError as e:
                    check("fm1ota.mjs produced a summary", False, str(e))
                    js = {}
                wire = js.get("wire", {})
                h2b = bytes.fromhex

                def same(label, got, want):
                    ok = got == want
                    check("fm1ota " + label, ok, "" if ok else "%r != %r" % (got, want))

                for v in wire.get("pack7", []):
                    same("pack7", fm1.pack7(h2b(v["in"])).hex(), v["out"])
                for v in wire.get("unpack7", []):
                    same("unpack7", fm1.unpack7(h2b(v["in"])).hex(), v["out"])
                for v in wire.get("crc16", []):
                    same("crc16", fm1._crc16(h2b(v["in"])), v["out"])
                for v in wire.get("jlEnc", []):
                    same("jlEnc", fm1._dec(h2b(v["in"])).hex(), v["out"])
                for v in wire.get("response", []):
                    same("response", fm1.response(v["addr"], h2b(v["data"]), v["fl"]).hex(), v["out"])
                for v in wire.get("reqParse", []):
                    got = fm1.parse_request(h2b(v["wire"]))
                    same("parseRequest",
                         {"fl": got[0], "addr": got[1], "n": got[2]} if got else None,
                         {"fl": v["fl"], "addr": v["addr"], "n": v["n"]})
                for v in wire.get("idParse", []):
                    ident = fm1.parse_identity(h2b(v["wire"]))
                    same("parseIdentity", ident.text if ident else None, v["text"])
                for v in wire.get("pairOutput", []):
                    same("pairOutput", fm1.pair_output(v["name"], v["outs"]), v["out"])
                for name, s in (js.get("packages") or {}).items():
                    raw = next((p.read_bytes() for p in fpkgs if p.name == name), None)
                    if raw is None:
                        continue
                    fimg, nblk = fm1.deinterleave(raw)
                    same("package " + name, s, {"product": fm1.product_of(raw), "blocks": nblk,
                                                "damage": fm1.package_damage(fimg)})
                inst = js.get("install", {})
                check("fm1ota.mjs runs the whole update over the wire (fake device)", bool(inst.get("ok")),
                      str(inst.get("error") or ""))

                # The same two steps driven by the Python host in this tree, and the state an
                # interrupted install can leave the keyboard in: tools/fake_device.py serves the
                # device side without hardware (or MIDI), so the macOS recovery path - the only
                # one there is, see docs/RECOVERY.md - is exercised on every run.
                for extra, label in (
                    ([], "fm1_install.py runs the whole update against a fake device"),
                    (["--app-tag", "feed1234"],
                     "a device that names its build is reported as such after the install"),
                    (["--loader-name", "Felucca Update", "--loader-id", "ota-SMK-37 Pro_900"],
                     "a loader that calls itself 'Felucca Update' is found and written"),
                    (["--phase", "loader", "--loader-id", "ota-SMK-37 Pro_016",
                      "--resume-loader", "ota-SMK-37 Pro_016"],
                     "fm1_install.py refuses a foreign loader, then finishes it with --resume-loader"),
                ):
                    r = subprocess.run([sys.executable, str(SRC / "tools" / "fake_device.py"),
                                        "--test", str(fpkgs[0]), *extra],
                                       capture_output=True, text=True, cwd=SRC)
                    tail = (r.stdout or r.stderr).replace("\r", "\n").strip().splitlines()
                    check(label, r.returncode == 0, (tail or [""])[-1][:160])

    print("== device box: SIMULATOR (web) and FLASH(USB) are separate ==")
    # SIMULATOR applies a build on the web; FLASH(USB) writes one to a real device over SysEx.
    # If a future edit merges them back into one button, this fails.
    page = (SRC / "web" / "sim" / "index.html").read_text()
    for need in ('id="simPicovave"', 'id="simRestore"',
                 'id="flashPicovave"', 'id="flashRestore"',
                 'id="midiport"', 'id="midiscan"'):
        check("the page has %s" % need, need in page)
    check("FLASH(USB) is wired to the Web MIDI flasher", 'from "./fm1ota.mjs"' in page)
    check("FLASH(USB) can list MIDI ports and let the user pick one",
          "refreshPortSelect" in page and "scanPorts" in page)
    # The same split for CARTS - it was missing: the cart controls pointed at the wasm only, so
    # "it works in the simulator" said nothing about a flashed device. They now name both targets,
    # and the FLASH(USB) one must be the SAME protocol on the wire (the firmware's own frames),
    # not a second implementation that could drift.
    for need in ('id="cartmode"', 'id="cartp8"', 'id="cartSend"', 'id="cartErase"'):
        check("the page has %s" % need, need in page)
    check("the cart controls name both targets (SIMULATOR / FLASH(USB))",
          "CART_TARGETS" in page and '"FLASH(USB)"' in page)
    usb_cart = page.split("async function uploadCartUsb", 1)[-1].split("function uploadCart(", 1)[0]
    check("FLASH(USB) carts are the SIMULATOR path on the wire (same frames, the same one slot)",
          "cartFrames(" in usb_cart and "cartUsbSend(" in usb_cart
          and "CART_SLOT" in usb_cart and "cartUsbLink(" in usb_cart)
    check("FLASH(USB) carts open the same Web MIDI link the package flasher uses",
          "async function cartUsbLink" in page and "WebMidiBackend.request()" in page)
    check("the SIMULATOR cart path still hands the wasm its own frames",
          "cartFrames(" in page.split("function uploadCartSim", 1)[-1].split("async function cartReply", 1)[0])
    check("the FLASH(USB) cart reads the device's own cart replies",
          "async function cartReply" in page and "0x7E" in page)
    # One cart at a time (src/picovave_cart.h: PV_SLOT_COUNT 1): both targets write slot 0, so
    # there is no "the other slot" to hide behind and no rollback slot to fall back to - the
    # ladder's floor is the embedded cart.
    check("both cart targets write the one slot (no rollback slot)",
          page.count("CART_SLOT") >= 2 and "cartSlot" not in page
          and "PV_SLOT_COUNT   1u" in (SRC / "src" / "picovave_cart.h").read_text())
    # The one cart format: the page takes a .p8.png, and unwraps it with its own module (shared
    # with the Node tests, so the bytes the hardware gets are the bytes the tests upload).
    check("the page takes only .p8.png carts", 'accept=".p8.png"' in page
          and 'accept=".p8"' not in page)
    check("the page unwraps the image with web/sim/p8png.mjs (the tests run the same module)",
          'from "./p8png.mjs"' in page and "cartPayload(" in page
          and "cartPayload(" in (SRC / "sim" / "sim_test.mjs").read_text())
    p8png = (SRC / "web" / "sim" / "p8png.mjs").read_text()
    check("p8png.mjs refuses anything that is not a 160x205 image",
          "PNG_WIDTH = 160" in p8png and "PNG_HEIGHT = 205" in p8png
          and "not a PNG" in p8png and "DecompressionStream" in p8png)
    check("p8png.mjs's cook size is the wire payload's minus its magic",
          "COOKED_SIZE = PNG_WIDTH * PNG_HEIGHT" in p8png
          and "CART_LEN = COOKED_MAGIC.length + COOKED_SIZE" in p8png)
    # "Does this cart run on the device?" is decided by MEASURING it in the device's own allocator
    # over the device's own arena, not by guessing from the cart's size (Lua costs 10-20x the source).
    # tools/build_sim.py's default wasm is that allocator (sim/device_alloc.c; --probe-alloc is the
    # cost-model escape hatch), and its budget must be the arena the DEVICE really links, not a
    # second, drifting constant. That arena is a link-time number: app.ld puts .data/.bss and .pool in
    # ONE RAM region that ends at the stack guard, so the arena is [_pool_start (wherever .bss ends),
    # 0x01C74000). build/picovave/picovave.map pins it down, and every copy of it - the default build,
    # host/alloc_test.c, the page, sim_test.mjs - must agree with the link, or a cart the page clears
    # fails on the instrument.
    bp = (SRC / "tools" / "build_picovave.py").read_text()
    guard = int(re.search(r"PV_POOL_GUARD = (\d+)", bp).group(1))
    sim_test = (SRC / "sim" / "sim_test.mjs").read_text()
    arena_dev = int(re.search(r"#define PV_ARENA (\d+)u", (SRC / "sim" / "device_alloc.c").read_text()).group(1))
    arena_test = int(re.search(r"#define ARENA (\d+)u", (SRC / "host" / "alloc_test.c").read_text()).group(1))
    arena_page = int(re.search(r"const DEVICE_HEAP = (\d+) -", page).group(1))
    arena_mjs = int(re.search(r"const DEVICE_HEAP = (\d+) -", sim_test).group(1))
    mapf = SRC / "build" / "picovave" / "picovave.map"
    link = None
    if mapf.exists():
        msym = dict((n, int(a, 16)) for a, n in
                    re.findall(r"^\s*0x([0-9a-f]+)\s+(_pool_start|_pool_top) =", mapf.read_text(), re.M))
        if "_pool_start" in msym and "_pool_top" in msym:
            link = msym["_pool_top"] - msym["_pool_start"]
    check("the page judges a cart against the arena the device really links (one number, no drift)",
          link is not None and arena_dev == arena_test == arena_page == arena_mjs == link
          and re.search(r"const DEVICE_HEAP = \d+ - %d;" % guard, page) is not None,
          "link _pool_start.._pool_top %d B; device_alloc %d; alloc_test %d; page %d; sim_test %d"
          % (link if link is not None else -1, arena_dev, arena_test, arena_page, arena_mjs))
    check("the page measures the cart in a throwaway wasm before sending it",
          "async function measureCart" in page and "ex.sim_heap_peak()" in page
          and "probeMods[fw]" in page and "MEASURE_FRAMES" in page)
    check("the page says the verdict in words, and warns instead of claiming success",
          "기기 OK" in page and "기기 불가" in page and 'className = "warn"' in page)
    check("the measurement leaves the running simulator alone (its own store and heap)",
          "instantiateModule(mod)" in page.split("async function measureCart", 1)[-1]
          .split("async function loadCart", 1)[0])
    simc = (SRC / "sim" / "sim.c").read_text()
    probe = (SRC / "sim" / "heap_probe.c").read_text()
    bsim = (SRC / "tools" / "build_sim.py").read_text()
    check("the wasm's heap is the engine's and Lua's own allocator, measured",
          "sim_heap_reset()" in simc and "heap_probe.c" in bsim
          and "-Dmalloc=sim_malloc" in bsim and "sim_heap_peak" in probe)
    check("the probe charges the device's per-block cost, not this allocator's",
          "PROBE_COST" in probe and "sizeof(probe_hdr)" in probe
          and "PV_HDR" in (SRC / "femto8" / "stub" / "device" / "alloc.c").read_text())
    check("sim_test.mjs checks what the device can actually hold",
          "DEVICE_HEAP" in (SRC / "sim" / "sim_test.mjs").read_text()
          and "sim_heap_peak()" in (SRC / "sim" / "sim_test.mjs").read_text())

    print("== app area ==")
    region = bytearray(fl[0x4000:])
    fwsc.sfc(region, 0, len(region), 0, KEY)
    head = dec32(region, 0, 1, encrypted=False)[0]
    ents = dec32(region, 0x20, 8, encrypted=False)
    # the entry's own CRC is at +0, the CRC of the data it describes at +2
    check("app_area_head data CRC over the whole area",
          fwsc.crc16(bytes(region[0x20:head["size"]])) == head["dcrc"])
    check("app_area_head offset 0x02000120", head["off"] == 0x02000120, hex(head["off"]))
    app = [e for e in ents if e["name"] == "app.bin"][0]
    stored = region[0x120:0x120 + app["size"]]
    check("app.bin entry CRC", app["ok"])
    check("app image bytes are the built image", stored[:len(img)] == img)
    check("app slot padding is 0xff", all(b == 0xFF for b in stored[len(img):]))
    check("app slot is 0x97400", app["size"] == 0x97400, hex(app["size"]))


    print("== simulator parity (browser screen == ROM screen) ==")
    # The whole point of the shared structure: src/picovave_p8.h (the app layer) compiles inside
    # the ROM, the wasm simulator and the host preview, all three over the same femto8 engine.
    # Prove the pixels agree.
    sim_wasm = SRC / "build" / "sim" / "sim.wasm"
    # The panel's audio engine is picovave's own worklet (picovave/sim/worklet.js), served verbatim:
    # its audio clock drives the device and makes its sound. If the served copy diverges from
    # picovave/sim/, someone edited it outside the build - that should be a deliberate, visible act.
    worklet_src = SRC / "sim" / "worklet.js"
    worklet_served = SRC / "build" / "sim" / "worklet.js"
    check("the served audio worklet is picovave's own file (picovave/sim/worklet.js)",
          worklet_served.exists() and worklet_served.read_bytes() == worklet_src.read_bytes(),
          "" if worklet_served.exists() else "run tools/build_sim.py")
    page_wasms = [str(w) for w in (sim_wasm,) if w.exists()]

    # The host preview is the third consumer of the same engine. Build it and render the idle
    # screen first: the wasm below then has to produce exactly those pixels.
    prev_bin = None
    try:
        prev_bin = preview_picovave.build()
        check("host preview compiles (the same engine, a third seam)", True)
    except SystemExit as e:
        check("host preview compiles (the same engine, a third seam)", False, str(e)[:200])
    if prev_bin:
        pr = subprocess.run([str(prev_bin), str(SRC / "build" / "picovave" / "preview.ppm"),
                             str(SRC / "build" / "picovave" / "preview.txt")],
                            capture_output=True, text=True, cwd=SRC)
        check("host preview rendered the idle screen", pr.returncode == 0,
              (pr.stdout.strip() or pr.stderr.strip())[:120])

    # The crash screen is drawn by the same hg_crash_screen() the ROM's fault handler calls, so
    # the preview can render it from a scripted record. What matters is that every line fits:
    # hg_text_center() clips silently, so a string that outgrows the panel (or a line that walks
    # into its neighbour) is invisible on a real crash and has to be caught here.
    for crash_mode, crash_name in (("--crash", "the crash screen"),
                                   ("--crash-boot", "the boot report of the last crash")):
      if prev_bin:
        crash_ppm = SRC / "build" / "picovave" / ("crash.ppm" if crash_mode == "--crash"
                                                  else "crash-boot.ppm")
        cr = subprocess.run([str(prev_bin), crash_mode, str(crash_ppm)],
                            capture_output=True, text=True, cwd=SRC)
        # The screen is drawn on PICO-8's own 128x128 surface and the panel is 240x240, so the
        # seam expands it nearest-neighbour: canvas row r lands on the panel rows whose source
        # index is r. Compute where each line's ink goes instead of hand-maintaining band tables
        # - the geometry is (0, 14) for the headline and twelve 9-px lines from canvas y=16,
        # which is hg_crash_screen()'s layout.
        def band(r0, r1):
            lo = -(-(r0 * 240) // 128)          # first panel row reading canvas row r0
            hi = -(-(r1 * 240) // 128) - 1      # last one reading canvas row r1-1
            return max(0, lo), min(239, hi)
        crash_lines = [(0, 14)] + [(16 + 9 * i, 23 + 9 * i) for i in range(12)]
        bands = [band(r0, r1) for r0, r1 in crash_lines]
        try:
            raw = crash_ppm.read_bytes()
            body = raw.split(b"\n255\n", 1)[1]
            px = lambda x, y: body[(y * 240 + x) * 3:(y * 240 + x) * 3 + 3]     # P6, 240x240
            bad = []
            for i, (y0, y1) in enumerate(bands):
                xs = [x for y in range(y0, y1 + 1) for x in range(240) if px(x, y) != b"\0\0\0"]
                if not xs:
                    bad.append("line %d is empty" % (i + 1))
                elif min(xs) < 4 or max(xs) > 235:
                    bad.append("line %d reaches x=%d..%d" % (i + 1, min(xs), max(xs)))
            for i in range(len(bands) - 1):
                y0, y1 = bands[i][1] + 1, bands[i + 1][0] - 1
                if y0 <= y1 and any(px(x, y) != b"\0\0\0"
                                    for y in range(y0, y1 + 1) for x in range(240)):
                    bad.append("two lines collide at y=%d..%d" % (y0, y1))
            check("%s renders %d lines inside the panel (the last names the build)"
                  % (crash_name, len(bands)), cr.returncode == 0 and not bad,
                  "; ".join(bad)[:160])
        except (OSError, IndexError) as e:
            check("%s renders %d lines inside the panel (the last names the build)"
                  % (crash_name, len(bands)), False, str(e)[:160])

    # The boot mark is painted from fm1_cstart before anything else this firmware added, so its
    # presence in the image is what makes the panel able to answer "did the new build run at all".
    check("the firmware paints a boot mark naming itself before the stack scan",
          "pv_early_mark" in (SRC / "src" / "picovave.c").read_text()
          and (SRC / "src" / "picovave.c").read_text().index("pv_early_mark();")
          < (SRC / "src" / "picovave.c").read_text().index("pv_stack_paint();"))

    # The page's verdict on a cart is only a verdict if the wasm it loads IS the device's allocator
    # running in the device's own arena. tools/build_sim.py's DEFAULT build is that (--probe-alloc
    # is the cost-model escape hatch), the RUNNING simulator instance is handed the whole arena
    # array so a cart too big for the hardware still plays, and the throwaway measurement instance
    # keeps the default PV_ARENA - the device's number, pinned by the five-way agreement above.
    bsim_txt = (SRC / "tools" / "build_sim.py").read_text()
    page_txt = (SRC / "web" / "sim" / "index.html").read_text()
    check("the page's own wasm is the device's allocator, in the device's own arena",
          'if a.probe_alloc else (SIM / "device_alloc.c")' in bsim_txt
          and "instantiateModule(probeMods[k], DEVICE_ARENA_MAX)" in page_txt
          and "DEVICE_ARENA_MAX = 1024 * 1024" in page_txt
          and "sim_arena_set" in page_txt
          and "ex = await instantiateModule(mod)" in page_txt)

    if not sim_wasm.exists():
        check("simulator built (build/sim/sim.wasm)", False, "run tools/build_sim.py")
    else:
        js = SRC / "build" / "sim" / "parity.mjs"
        js.parent.mkdir(parents=True, exist_ok=True)
        js.write_text(PARITY_JS)
        r = subprocess.run([shutil.which("node") or "node", str(js), str(sim_wasm),
                            str(SRC / "build" / "picovave" / "preview.ppm")],
                           capture_output=True, text=True, cwd=SRC)
        for line in r.stdout.splitlines():
            tag, rest = (line[:4], line[4:]) if len(line) > 4 else ("", line)
            if tag.strip() in ("ok", "FAIL"):
                check(rest.strip(), tag.strip() == "ok")
            else:
                print("      " + line)
        if r.returncode and not r.stdout:
            check("simulator parity ran", False, r.stderr.strip()[:200])

    print("== input parity (the same presses draw the same pixels) ==")
    # The idle screen proves the drawing is shared. This proves the INPUT PATH is shared:
    # the same script of presses is fed to the host preview and to the wasm simulator, and
    # the two screens must come out byte for byte identical. The firmware reads its own
    # matrix HAL, but the code that turns edges into pixels is the same picovave_p8.h.
    if sim_wasm.exists() and prev_bin:
        script = SRC / "build" / "sim" / "input_script.txt"
        script.write_text(INPUT_SCRIPT)
        host_ppm = SRC / "build" / "sim" / "input_host.ppm"
        host_txt = SRC / "build" / "sim" / "input_host.txt"
        wasm_ppm = SRC / "build" / "sim" / "input_wasm.ppm"
        hp = subprocess.run([str(prev_bin), str(host_ppm), str(host_txt), str(script)],
                            capture_output=True, text=True, cwd=SRC)
        js = SRC / "build" / "sim" / "input_parity.mjs"
        js.write_text(INPUT_PARITY_JS)
        np_ = subprocess.run([shutil.which("node") or "node", str(js), str(sim_wasm),
                              str(wasm_ppm), str(script)],
                             capture_output=True, text=True, cwd=SRC)
        for line in np_.stdout.splitlines():
            head = line[:4].strip()
            if head in ("ok", "FAIL"):
                check(line[4:].strip(), head == "ok")
            else:
                print("      " + line)
        if hp.returncode or np_.returncode:
            err = (hp.stderr or np_.stderr).strip().splitlines()
            check("scripted input parity ran", False, (err or [""])[-1][:200])
        elif host_ppm.exists() and wasm_ppm.exists():
            hb, wb = host_ppm.read_bytes(), wasm_ppm.read_bytes()
            first = ""
            if hb != wb:
                for i in range(min(len(hb), len(wb))):
                    if hb[i] != wb[i]:
                        first = " (first byte difference at %d)" % i
                        break
            check("scripted input: wasm screen == host preview, byte for byte", hb == wb, first)
            check("scripted screen differs from the idle screen",
                  wasm_ppm.read_bytes() != (SRC / "build" / "picovave" / "preview.ppm").read_bytes())

    print("== simulator page vs wasm API ==")
    # A renamed export once shipped a page that called the old name (ex.sim_ms). The boot
    # test and the wasm agreed; nothing looked at the page. Now the page AND the worklet it
    # runs the wasm in are checked: every sim_* name either names must be a real export.
    if sim_wasm.exists():
        checker = SRC / "sim" / "check_api.mjs"
        r = subprocess.run([shutil.which("node") or "node", str(checker), *page_wasms,
                            str(SRC / "web" / "sim" / "index.html"),
                            str(SRC / "sim" / "worklet.js")],
                           capture_output=True, text=True, cwd=SRC)
        for line in r.stdout.splitlines():
            head = line[:4].strip()
            if head in ("ok", "FAIL"):
                check(line[4:].strip(), head == "ok")
            else:
                print("      " + line)
        if r.returncode and not r.stdout:
            check("page/wasm API check ran", False, r.stderr.strip()[:200])
    else:
        check("page/wasm API check ran", False, "run tools/build_sim.py first")

    # Execute the page itself against the real wasm - see picovave/sim/run_page.mjs.
    if sim_wasm.exists():
        runner = SRC / "sim" / "run_page.mjs"
        r = subprocess.run([shutil.which("node") or "node", str(runner), *page_wasms,
                            str(SRC / "build" / "sim" / "index.html")],
                           capture_output=True, text=True, cwd=SRC)
        for line in r.stdout.splitlines():
            head = line[:4].strip()
            if head in ("ok", "FAIL"):
                check(line[4:].strip(), head == "ok")
            else:
                print("      " + line)
        if r.returncode and not r.stdout:
            check("page executed", False, (r.stderr.strip().splitlines() or [""])[-1][:200])

    print("== shared-source structure ==")
    # The simulator must include the firmware's OWN headers, not a copy of their logic.
    sim_c = (SRC / "sim" / "sim.c").read_text()
    check("simulator includes the firmware's app layer", '#include "../src/picovave_p8.h"' in sim_c)
    check("simulator includes the firmware's cart store + protocol",
          '#include "../src/picovave_cart.h"' in sim_c)
    check("simulator swaps only the HAL", '#include "picovave_sim_hal.h"' in sim_c and
          "picovave_hal.h" not in sim_c.replace("picovave_sim_hal.h", ""))
    app_h_raw = (SRC / "src" / "picovave_p8.h").read_text()
    app_h = re.sub(r"/\*.*?\*/", "", app_h_raw, flags=re.S)      # the header explains the HAL
    app_h = re.sub(r"//[^\n]*", "", app_h)
    check("the app layer names no register or SPI symbol",
          not any(t in app_h for t in ("0x11D", "SPI", "HH_", "volatile", "TIMER", "GPIO")))
    check("the app layer only calls the hh_* HAL",
          "hh_input_read(" in app_h and "hh_lcd_init(" in app_h and "hh_cart_" in app_h)
    # The engine is vendored, not re-implemented: the shared app layer drives femto8's own frame
    # loop (p8_begin/p8_step) and owns the per-frame setjmp that a cart's reset()/run() unwinds to.
    # A cart can hand the memory functions any address at all: lua_tounsigned() turns a negative
    # number into a huge unsigned and addr_remap() passes everything outside 0x0-0x1fff/0x6000-0x7fff
    # straight through, so m_memory + addr used to land far outside the cart buffer. On the SMK-37
    # Pro that swept down through .bss and took jmpbuf_restart with it; the next longjmp() then
    # restored a garbage sp into the frame loop (guard: EMU bit 3, PC in setjmp's prologue, and sp
    # was 0x01C0EC3C = jmpbuf_restart + 0x30).
    lua_c = (SRC / "femto8" / "src" / "p8_lua.c").read_text()
    check("cart memory writes stay inside the cart buffer",
          "#define MEM_VALID_LEN MEMORY_SIZE" in lua_c
          and lua_c.count("mem_ok(") >= 10
          and "if (mem_ok(addr + (i-2), 1u))" in lua_c
          and "if (mem_ok(addr + (i-2)*2 + 0, 1u))" in lua_c
          and "if (mem_ok(addr + (i-2)*4 + 3, 1u))" in lua_c
          and "chunk = MIN(chunk, MEM_VALID_LEN - destaddr1);" in lua_c
          and "mem_ok(destaddr, len) && srcaddr < CART_MEMORY_SIZE" in lua_c)
    check("cart memory reads answer 0 outside the cart buffer",
          "mem_ok(addr + i, 1u) ? m_memory[addr+i] : 0" in lua_c)
    check("the app layer drives the vendored femto8 engine",
          '#include "p8_emu.h"' in app_h_raw and "p8_begin(" in app_h_raw
          and "p8_step(" in app_h_raw and "setjmp(jmpbuf_restart)" in app_h)
    # and the three consumers must all compile the same app layer
    consumers = {
        "firmware": (SRC / "src" / "picovave.c").read_text(),
        "simulator": sim_c,
        "preview": (SRC / "host" / "preview.c").read_text(),
    }
    for name, text in consumers.items():
        check("%s compiles picovave_p8.h" % name, "picovave_p8.h" in text)

    # the input path is shared too: the model lives in the app layer, only the reading is a HAL
    check("input model is shared (picovave_input.h)", '#include "picovave_input.h"' in app_h_raw)
    check("the app layer uses the shared input model", "hin_t" in app_h and "hh_input_read(" in app_h)
    hal_files = {
        "firmware": (SRC / "src" / "picovave.c").read_text(),
        "simulator": (SRC / "sim" / "picovave_sim_hal.h").read_text(),
        "preview": (SRC / "host" / "preview.c").read_text(),
    }
    for name, text in hal_files.items():
        check("%s implements the input HAL" % name,
              "hh_input_init(" in text and "hh_input_read(" in text)
    fw = hal_files["firmware"]
    check("firmware matrix scan is Makkuro37's (smk_input.h)",
          '#include "smk_input.h"' in fw and "fm1_input_tick(" in fw and "fm1_enc_take(" in fw)
    check("firmware panel is Makkuro37's (lcd.c + fm1_lcd_hw.h)",
          '#include "lcd.c"' in fw and '#include "fm1_lcd_hw.h"' in fw
          and "lcd_init(" in fw and "lcd_blit(" in fw)
    # the OTA / rescue / crash screens reuse the shared drawing core (picovave_gfx.h) by aliasing
    # its canvas onto PICO-8's own screen surface - the same 32,768 B of .bss the engine renders a
    # cart into, so those screens cost no extra RAM at all.
    check("firmware reuses the shared screens for OTA / rescue / crash",
          '#include "picovave_gfx.h"' in fw and "#define hg_fb p8_screen_fb" in fw)
    # the analog inputs come from Makkuro37's ADC mux too: the pads carry velocity, the faders
    # and the two wheels are read from fm1_in, exactly as the simulator's sim_pad/fader/wheel do.
    check("firmware reads pads/faders/wheels from Makkuro37's HAL",
          "fm1_in.pad_press" in fw and "fm1_in.fader" in fw and "fm1_in.wheel" in fw)
    # The panel push. femto8 renders NATIVE RGB565 into p8_screen_fb (PICO-8's own 128x128), and
    # pv_push() expands ONE row (gdi_expand_row), swaps that row for the ST7789's big endian and
    # hands it to lcd_blit - so a whole 240x240 panel frame never exists in RAM. That row buffer is
    # what bought back the 82,432 B the old 240x240 canvas held (docs/HANDOVER.md §13).
    check("the device expands the engine's 128x128 screen one row at a time, swapped for the panel",
          "gdi_expand_row(p8_screen_fb" in fw and "(v >> 8) | (v << 8)" in fw
          and "lcd_blit(0u, (uint32_t)y, GDI_OUT_W, 1u, pv_row)" in fw
          and "static uint16_t pv_row[" in fw)
    check("the host preview captures the same seam expansion of the same surface",
          "gdi_display_update_async(0, 0)" in hal_files["preview"]
          and "memcpy(cap, p8_panel_fb" in hal_files["preview"])

    # the device is reachable and re-flashable, like Makkuro37: its USB-MIDI + M-UPGRADE stack,
    # the 10 kHz TIMER5 tick that drives it, and the escapes back to the mask-ROM UBOOT.
    check("firmware reuses Makkuro37's USB-MIDI + OTA stack",
          '#include "usb.c"' in fw and '#include "ota.c"' in fw
          and "#define FELUCCA_OTA 1" in fw and "usb_start(" in fw)
    check("firmware installs the 10 kHz TIMER5 tick (matrix scan + USB poll)",
          "fm1_timer5_start(isr_timer5" in fw and "fm1_input_tick()" in fw and "usb_poll()" in fw)
    check("firmware answers and can commit an M-UPGRADE update",
          "ota_service()" in fw and "ota_session()" in fw and "fm1_enter_update(" in fw)
    check("firmware can drop to the mask-ROM UBOOT (never bricked)",
          "fm1_enter_uboot(" in fw and "FM1_OCT_BOTH" in fw
          and "bootguard_begin(" in fw and '#include "bootguard.h"' in fw)
    # the friendly recovery: an app-level USB quarantine, reachable by holding OCT- at power-on or
    # after two failed boots, where the web installer still works. The escapes above stay as the
    # fallback behind it.
    check("firmware has the app-level USB rescue (OCT- at power-on, or 2 failed boots)",
          "picovave_rescue(" in fw and "bootguard_manual(fm1_in.buttons)" in fw
          and "usb.ota_req" in fw and "ota_service()" in fw and "ota_session()" in fw)
    # ...and the rescue itself has to be USB the whole way. usb.c services the SIE ONLY from the
    # TIMER5 ISR, so a rescue that runs "no TIMER5" and polls usb_poll() from its own loop can
    # never finish a 512-byte update read inside ota_read()'s window: the handshake answers and
    # the session never starts ("stopped answering after 0 requests"). The old version did exactly
    # that. The rescue is headless too - no panel, no font, no gfx, nothing for the installer's
    # job that usb.c/ota.c do not already bring - so it must not reference the screen helpers.
    resc = fw[fw.index("static void picovave_rescue(void)"):]
    resc = resc[:resc.index("\n}\n")]
    resc = re.sub(r"/\*.*?\*/", "", resc, flags=re.S)
    resc = re.sub(r"//[^\n]*", "", resc)
    check("the USB rescue drives USB from TIMER5, not from a 500 ms loop of its own",
          "fm1_timer5_start(isr_timer5" in resc and "usb_poll()" not in resc
          and "500u * FM1_TICKS_PER_US" not in resc)
    check("the USB rescue is headless (usb.c + ota.c only; the installer is the display)",
          "hg_" not in resc and "lcd_" not in resc and "panel_up" not in resc)
    # a rescue whose USB is dead must still have a way out that needs no host at all
    check("the USB rescue can still reach the mask-ROM UBOOT (soft key and OCT keys)",
          "usb.uboot_req" in resc and "FM1_OCT_BOTH" in resc
          and "picovave_enter_uboot(" in resc)
    # the upstream fix that keeps a slow host's SysEx parser from swallowing everything after a
    # cut-off reply (dinghy carries it; Makkuro37 predates it) - the reused usb.c must have it.
    check("firmware's USB stack recovers a cut-off SysEx reply (upstream sx_open fix)",
          "sx_open" in (SRC / "platform" / "firmware" / "src" / "usb.c").read_text())

    # Audio is femto8's own mixer (p8_audio.c). It reads the same state Lua writes while the cart
    # runs, so it is advanced from the main loop; the ALNK0 ISR only copies a half out of the ring
    # the loop fills, which keeps the engine's audio state single-writer.
    isr = fw[fw.index("void fm1_alnk0_irq(void)"):fw.index("static void pv_audio_fill(void)")]
    check("the device advances femto8's mixer from the main loop",
          "pv_audio_fill(" in fw and "render_sounds(" in fw and "render_sounds(" not in isr)
    check("the ALNK0 ISR only copies the loop's ring",
          "fm1_audio_pending()" in isr and "fm1_audio_ack_half()" in isr
          and "fm1_audio_init(" in fw)
    check("the simulator renders the same mixer", "render_sounds(" in sim_c and "sim_render(" in sim_c)

    # The cart store and the SysEx upload protocol are shared, so the simulator verifies the
    # protocol the device speaks: the same slots, the same 7-bit fields, the same CRC.
    cart_h_raw = (SRC / "src" / "picovave_cart.h").read_text()
    check("the cart store + SysEx protocol are shared (picovave_cart.h)",
          '#include "picovave_cart.h"' in fw and '#include "../src/picovave_cart.h"' in sim_c)
    check("the device's cart store is the SLOOP flash region (not a filesystem)",
          "FL_DATA_LO" in fw and "FL_XIP(" in fw and "fl_write(" in fw)
    # A cart is a .p8.png and nothing else. The page unwraps the image (web/sim/p8png.mjs) into
    # what the device takes: a magic, then the 32,800-byte cooked cart - the cartridge ROM, then a
    # code section (PXA / ':c:' / raw) that femto8's own expander reads (p8_load_cooked). The
    # cooked cart is BINARY, so an upload SPLITS it 8 bits into 7; both ends must agree, so the
    # device's inverse and both packers are checked.
    engine_c = (SRC / "femto8" / "src" / "p8_emu.c").read_text()
    check("a cart payload is exactly a magic + the cooked cart a .p8.png carries",
          "PV_COOKED_SIZE" in cart_h_raw and "PV_COOKED_MAGIC" in cart_h_raw
          and "PV_CART_LEN" in cart_h_raw and "PV_E_FMT" in cart_h_raw)
    check("BEGIN refuses anything that is not a cooked cart, and a slot without the magic is skipped",
          "len != PV_CART_LEN" in cart_h_raw and "memcmp(m, PV_COOKED_MAGIC" in cart_h_raw)
    # A page-compiled cart ("PVB1"): the same cartridge ROM, then Lua bytecode. The page builds the
    # chunk with the DEVICE's own Lua (tools/luabc.c -> build_luabc.py), and the device hands it to
    # luaL_loadbufferx("b") - so it never expands the source and never runs the compiler. The slot
    # budget is arithmetic on the store's own constants, not a second hardcoded number.
    lua_c = (SRC / "femto8" / "src" / "p8_lua.c").read_text()
    check("a precompiled cart is a payload the slot can hold",
          "PV_ROM_SIZE" in cart_h_raw and "PV_BCODE_MAGIC" in cart_h_raw
          and "PV_BC_HDR" in cart_h_raw and "PV_BC_MAX" in cart_h_raw
          and "PV_SLOT_PAYLOAD" in cart_h_raw)
    check("the device undumps a precompiled chunk instead of compiling the source",
          "p8_load_bytecode(" in engine_c and "luaL_loadbufferx(" in lua_c
          and "p8_load_chunk(" in engine_c)
    check("the page's compiler is built from the device's own Lua",
          (SRC / "tools" / "luabc.c").exists() and (SRC / "tools" / "build_luabc.py").exists()
          and "femto8/src/lua" in (SRC / "tools" / "build_luabc.py").read_text())
    check("the device expands the code section with femto8's own decompressor",
          "p8_load_cooked(" in engine_c and "pico8_code_section_decompress(" in engine_c)
    check("the expansion buffer is sized to the cart's own script, not to the 64 KiB maximum",
          "script_size = src[4] * 256 + src[5]" in engine_c
          and "(uint8_t *)m_lua_script, script_size);" in engine_c
          and "m_lua_script[script_size] = '\\0'" in engine_c
          and "script_size = 0x3d00;" in engine_c
          and "script_size = LUA_SCRIPT_SIZE - 1;" in engine_c)
    # The boot ladder has to hand the pool back before EVERY candidate, or a cart the device cannot
    # run takes the device down with it. Both halves were real on the SMK-37 Pro (baba uploaded, the
    # panel then stayed on its boot screen for good) and neither was a hardware fault:
    #   - a cart too big for the pool is refused, and the failed compile leaves that memory
    #     unreachable but UNCOLLECTED - Lua's allocator throws out of it and nothing runs a GC after;
    #   - an upload boots the new cart with the OLD one still live, and that one holds the pool.
    # Either way the next p8_load_cooked() (64 KiB of script buffer, out of a 288 KiB pool) could not
    # be served, hh_cart_next() walked to the end, picovave_status stayed -1 and picovave_frame()
    # returned forever. p8_stop_cart() is the release half of p8_reset_cart(): lua_shutdown_api() is
    # lua_close(), so every object goes and L is NULL (p8_begin() -> p8_reset_cart() ->
    # lua_load_api() builds it again). It deliberately skips p8_reset_cart()'s assert(!cart_running):
    # on an upload a cart IS still running, and the assert (enabled - nothing defines NDEBUG) would
    # abort instead of booting.
    ladder = (SRC / "src" / "picovave_p8.h").read_text()
    loop = ladder[ladder.index("for (;;) {"):ladder.index("picovave_frame();")]
    stop = engine_c[engine_c.index("int p8_stop_cart(void)"):]
    stop = stop[:stop.index("\n}")]
    check("the boot ladder hands the pool back before every candidate (a refused cart cannot freeze it)",
          "p8_stop_cart();" in loop and "p8_reset_cart();" not in loop)
    check("...and that release is lua_close(), not p8_reset_cart() - on upload a cart IS still running",
          "lua_shutdown_api();" in stop and "cart_running = false;" in stop and "assert(" not in stop
          and "int p8_stop_cart(void);" in (SRC / "femto8" / "src" / "p8_emu.h").read_text())
    check("a candidate that runs clears the status the refused ones before it set",
          "picovave_status = 0;" in loop[loop.index("if (p8_begin() == 0) {"):])
    check("the sim proves the fallback end to end: refused -> the built-in cart RUNS, status is 0",
          "sim_cart_running" in sim_c and "sim_cart_running" in (SRC / "sim" / "sim_test.mjs").read_text()
          and "the committed cart runs, or the pool refused it and the ladder fell back"
              in (SRC / "sim" / "sim_test.mjs").read_text())
    check("the page measures a cart against the device's own pool before uploading it",
          "DEVICE_HEAP" in page and "measureCart" in page and "confirm(" in page)
    # PICO-8's `?` is `print(`, closed at the end of the line, so it can be an EXPRESSION -
    # `local w = ?text, 0, -1000` measures a string (print returns the x after the text). Upstream
    # femto8's Lua only accepts it as a statement, which makes real carts (baba_demake-9) fail to
    # load; picovave's parser patch is what lets them run, so a re-vendor must not lose it.
    check("femto8's Lua takes PICO-8's `?` where an expression is expected",
          "case TK_PRINT:" in (SRC / "femto8" / "src" / "lua" / "lparser.c").read_text()
          and "shortprint(ls, v)" in (SRC / "femto8" / "src" / "lua" / "lparser.c").read_text()
          and "shortprint(ls, NULL)" in (SRC / "femto8" / "src" / "lua" / "lparser.c").read_text())
    # A cart is loaded, then ANOTHER cart is loaded, in the same process: an upload IS a boot. The
    # cached callbacks have to be cleared with the cart, or a cart without `_update` inherits the
    # previous cart's (collected) one and dies at frame 0. sim_test.mjs uploads all of carts/ in
    # sequence, which is what caught it.
    check("loading a cart clears the previous cart's cached callbacks (upload is a boot)",
          "m_lua_update = NULL;" in (SRC / "femto8" / "src" / "p8_lua.c").read_text()
          and "m_lua_update60 = NULL;" in (SRC / "femto8" / "src" / "p8_lua.c").read_text()
          and "m_lua_draw = NULL;" in (SRC / "femto8" / "src" / "p8_lua.c").read_text()
          and "m_lua_init = NULL;" in (SRC / "femto8" / "src" / "p8_lua.c").read_text())
    check("an upload payload is 8->7 split (the cooked cart is binary, so it has to be)",
          "pv_unpack7(" in cart_h_raw and "PV_DATA_MAX" in cart_h_raw
          and "(n * 8u + 6u) / 7u" in cart_h_raw)
    check("the page and the sim test pack the payload exactly as the device unpacks it",
          "pack7" in page and "pack7" in (SRC / "sim" / "sim_test.mjs").read_text())
    check("a committed cart is CRC16/CCITT checked, and the newest slot runs",
          "0x1021" in cart_h_raw and "seq" in cart_h_raw and "pv_seq_newest" in cart_h_raw)
    check("the device answers an upload with OK / ERR / INFO frames",
          "pv_ok(" in cart_h_raw and "pv_err(" in cart_h_raw and "pv_send(0x12" in cart_h_raw)
    # Only the cooked-cart path is reachable, so the buffers upstream keeps for the text-.p8 and
    # cart-FILE paths are not allocated: 114 KiB of .pool that the Lua VM gets instead (the
    # device's Lua heap). The simulator compiles the same patch, so it runs what the ROM runs.
    check("the device never decodes a .p8.png itself (lodepng stays stubbed; the page unwraps it)",
          (SRC / "femto8" / "stub" / "lodepng_stub.c").exists()
          and (SRC / "femto8" / "README.picovave.md").exists()
          and "lodepng_decode32" not in engine_c)
    check("the file/text-parse scratch buffers are not allocated (they go to the Lua heap)",
          "m_file_buffer = NULL" in engine_c and "m_decompression_buffer = NULL" in engine_c
          and "m_temp_cart_memory = NULL" in engine_c
          and "malloc(FILE_BUFFER_SIZE)" not in engine_c)
    # A cart is the one thing the device really cannot afford to keep copies of: both the 17 KiB
    # cartridge ROM and the 64 KiB Lua source are taken from the cooked cart on demand instead.
    # Read as the device build sees it (femto8's SDL front end is not built and keeps its own
    # allocation of the cart ROM, which says nothing about the device).
    engine_dev = strip_unbuilt(engine_c)
    check("the cartridge ROM is read out of the cooked cart, not copied into RAM",
          "m_cart_memory = (uint8_t *)cooked" in engine_dev
          and "malloc(CART_MEMORY_SIZE)" not in engine_dev
          and "memcpy(m_cart_memory" not in engine_dev)
    check("the Lua source buffer exists only while a cart is loading",
          "static int p8_materialize_script(void)" in engine_c
          and "static void p8_release_script(void)" in engine_c
          and "p8_release_script();" in engine_c)
    bp = (SRC / "tools" / "build_picovave.py").read_text()
    check("the build's ENGINE_HEAP matches the buffers p8_init() really allocates",
          "ENGINE_HEAP = (65536 + 8192)" in bp and "LOAD_PEAK_HEAP" in bp)
    # The device's allocator is the one piece of the ROM that no other test can reach: the host
    # preview links the host's libc, and the wasm links sim/device_alloc.c - which IS this code -
    # in the device's own arena, so a cart is judged by what the pool really did. Build the same
    # allocator on the host too, so a broken split or a lost realloc fails here instead of on the
    # instrument - which is exactly how the malloc that wrote a block header over the sentinel
    # (and a footer outside the pool) reached a flash.
    alloc_bin = SRC / "build" / "alloc_test"
    try:
        alloc_bin.parent.mkdir(parents=True, exist_ok=True)
        ac = subprocess.run(["cc", "-O1", "-o", str(alloc_bin), str(SRC / "host" / "alloc_test.c")],
                            capture_output=True, text=True, cwd=SRC)
        ar = subprocess.run([str(alloc_bin)], capture_output=True, text=True, cwd=SRC) if ac.returncode == 0 else None
        check("the device allocator (femto8/stub/device/alloc.c) passes host/alloc_test.c",
              ac.returncode == 0 and ar is not None and ar.returncode == 0,
              (ac.stderr.strip() or (ar.stdout.strip().splitlines() or [""])[-1] if ac.returncode == 0
               else ac.stderr.strip())[:200])
        if ar is not None and ar.returncode == 0:
            for line in ar.stdout.splitlines()[-2:]:
                print("      " + line)
    except OSError as e:
        check("the device allocator (femto8/stub/device/alloc.c) passes host/alloc_test.c", False, str(e)[:200])
    check("reload(name) refuses instead of parsing into buffers that are not there",
          "reload: loading another cart is not available on this target"
          in (SRC / "femto8" / "src" / "p8_lua.c").read_text())
    # The built-in default cart is a cooked cart too, assembled by tools/pvcart.py - so no build
    # step parses a text .p8 either.
    check("the default cart is baked from tools/pvcart.py (no .p8 text path in the build)",
          "import pvcart" in (SRC / "tools" / "build_picovave.py").read_text()
          and "import pvcart" in (SRC / "tools" / "build_sim.py").read_text()
          and "pvcart.py" in (SRC / "tools" / "sources.py").read_text())

    print("== blast radius (which flash bytes an install changes) ==")
    _, st = flash_of("/Users/spectrum/Downloads/SMK-37_Pro_016.fwsc")
    protected = [(0x9D000, "VM (projects/settings/SLOOP store)"),
                 (0xC0000, "BTIF (bluetooth pairing)"),
                 (0xC1000, "USRTRIM (key/pad calibration)"),
                 (0xC2000, "USRFLASH (user sample slots)"),
                 (0xF4000, "USR (stock resources)"),
                 (0xFF000, "key_mac")]
    for off, what in protected:
        check("install never reaches %s at %#x" % (what, off), len(fl) <= off,
              "flash.bin ends at %#x" % len(fl))
    print("      app area   0x04000..0x%05x rewritten (the firmware slot)" % len(fl))
    print("      head       0x00000..0x03FFF rebuilt; SPL + key_mac carried over from the SDK")
    st_area = bytearray(st[0x4000:])
    fwsc.sfc(st_area, 0, len(st_area), 0, KEY)      # same decode the package uses on the device
    st_regions = dec32(st_area, 0x20, 8, encrypted=False)[2:]
    he_regions = ents[2:]
    rt_same = all(a["off"] == b["off"] and a["size"] == b["size"] and a["idx"] == b["idx"]
                  and a["flags"] == b["flags"] and a["resvd"] == b["resvd"]
                  and a["name"] == b["name"] for a, b in zip(st_regions, he_regions))
    check("SMK region table identical to stock v016", rt_same)

    print()
    if FAIL:
        print("VERIFICATION FAILED: " + ", ".join(FAIL))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
