#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build a JieLi/M-Vave ".fwsc" update package for the M-Vave SMK-37 Pro (AC791N).

Reverse engineered from the stock packages and cross-checked against the GPL-3.0
Makkuro37 board layer; the container codec is tools/fwsc.py (independent reader).

    python3 tools/picovave_pack.py --app build/picovave/picovave.bin --out build/picovave.fwsc
    python3 tools/picovave_pack.py --app ... --sdk ~/fw-AC79_AIoT_SDK --product "SMK-37 Pro_700"

Inputs
  --app    the linked application image (app.bin content, entry at 0x02000120)
  --sdk    a JieLi AC79 SDK checkout (provides uboot.boot, cfg_tool.bin, cfg/eq_cfg_hw.bin)

Outputs
  build/picovave/picovave.flash.bin   the logical flash image the package installs
  build/picovave.fwsc              the installable package

Flash map written, and deliberately NOT written
  0x00000-0x03FFF  head (SPL, isd_config)        written, byte-shaped like the stock head
  0x04000-0x9CFFF  app area                      written: app_area_head, app.bin, cfg_tool.bin, regions
  0x9D000-0xFFFFF  VM / BTIF / USRTRIM / USRFLASH / USR / key_mac   NOT written (outside PRCT)
"""
import argparse
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fwsc  # noqa: E402

# --- format constants (see the module docstring of tools/fwsc.py) ------------
APP_XIP = 0x02000120          # app.bin offset 0 maps here
XIP_BASE = 0x02000000         # flash 0x4000 maps here
SFC_KEY = 0x980F              # chip key: isd_config / the SFC cipher
FLASH_SIZE = 0x9D000          # PRCT: the end of what the bootloader writes
APP_SLOT = 0x97400            # the app region the stock v016 package reserves
HEAD_SIZE = 0x4000
UFW_CHIP = b"AC791N"
LOADER_MARK = b"FELUCCA-LOADER-1"

# 32-byte flash header: burner_size 544, VID "0.01", flash_size 0xFF000, fs_ver 0x10,
# PID "AC791N_STORY" (JieLi SDK defaults; identical in the stock and Makkuro37 packages)
FLASH_HDR = bytes.fromhex("20022f104cc900f00f001010a4ff4e5d0b41e0cd781dc856417fb367cf9f")

# The SMK-37 Pro v016 region table, exactly as the stock package carries it.
# (offset, size, flags, resvd, index, name16)
REGIONS = [
    (0x9D000, 0x23000, 0x12, 0x81, 0, bytes.fromhex("564d00ffffffffffffffffff00800000")),   # VM
    (0x0,     None,    0x92, 0x82, 0, bytes.fromhex("5052435400ffffffffffffff434f4445")),     # PRCT
    (0xC0000, 0x1000,  0x92, 0x82, 0, bytes.fromhex("4254494600ffffffffffffff4155544f")),     # BTIF
    (0xC1000, 0x1000,  0x92, 0x81, 0, bytes.fromhex("5553525452494d00ffffffffffffffff")),     # USRTRIM
    (0xC2000, 0x29000, 0x92, 0x81, 0, bytes.fromhex("555352464c41534800ffffffffffffff")),     # USRFLASH
    (0xF4000, 0xA000,  0x92, 0x81, 1, bytes.fromhex("55535200ffffffffffffffffffffffff")),     # USR
]


def entry32(dcrc, off, size, flags, resvd, idx, name):
    """A 32-byte JLFS entry: crc16(body) + body, body = dcrc, offset, size, flags,
    resvd, index, name(16). Used by the head and by the app area's file table."""
    nm = name if isinstance(name, bytes) else name.encode()
    if len(nm) < 16:
        nm = nm + b"\0" + b"\xff" * (15 - len(nm))
    nm = nm[:16]
    body = struct.pack("<HIIBBH16s", dcrc & 0xFFFF, off & 0xFFFFFFFF, size & 0xFFFFFFFF,
                       flags & 0xFF, resvd & 0xFF, idx & 0xFFFF, nm)
    return struct.pack("<H", fwsc.crc16(body)) + body


def key_blob(key):
    """32 bytes that decode (isd_config's bit-sum rule) to the chip key, plus its CRC16."""
    d = bytearray([0x10] * 16) + bytearray(16)          # sum 0 -> the 0x55 threshold
    for i in range(16):
        d[16 + i] = d[15 - i] ^ (0x00 if key >> i & 1 else 0xFF)
    return bytes(d) + struct.pack("<H", fwsc.crc16(d))


def sdk_file(root, rel):
    p = Path(root) / "cpu" / "wl82" / "tools" / rel
    if not p.exists():
        raise SystemExit("picovave_pack: %s missing (--sdk must point at a JieLi AC79 SDK checkout)" % p)
    return p.read_bytes()


def build_head(sdk, isd_tag=b"[PICOVAVE]\r\n"):
    """[0, 0x4000): flash header + SPL + isd_config + app_dir_head + key_mac, ENC 0xFFFF."""
    spl = sdk_file(sdk, "uboot.boot")
    isd = key_blob(SFC_KEY) + isd_tag
    f = bytearray(b"\xff" * HEAD_SIZE)
    spl_off = 0xA0
    isd_off = spl_off + len(spl)
    if isd_off + len(isd) > HEAD_SIZE:
        raise SystemExit("picovave_pack: SPL + isd_config do not fit the head")
    f[spl_off:spl_off + len(spl)] = spl
    f[isd_off:isd_off + len(isd)] = isd
    table = (entry32(fwsc.crc16(spl), spl_off, len(spl), 0x00, 0x00, 0, "uboot.boot") +
             entry32(fwsc.crc16(isd), isd_off, len(isd), 0x02, 0x80, 0, "isd_config.ini") +
             entry32(0xFFFF, 0x4000, 0xFFFFFFFF, 0x81, 0xFF, 0, "app_dir_head") +
             entry32(0xFFFF, 0xFF000, 0x1000, 0x12, 0x01, 0xFFFF, "key_mac"))
    hdr = struct.pack("<H", fwsc.crc16(FLASH_HDR)) + FLASH_HDR
    blob = bytearray(hdr + table)
    for o in range(0, len(blob), 32):
        fwsc.enc(blob, o, 32)                            # header and each entry: ENC 0xFFFF
    f[0:len(blob)] = blob
    return bytes(f), spl


def build_area(app, sdk, app_slot=APP_SLOT, flash_size=FLASH_SIZE):
    """[0x4000, ...): app_area_head + file table + app.bin + cfg_tool.bin + region
    descriptors + a nested cfg directory. SFC encrypted with the chip key."""
    cfg_tool = sdk_file(sdk, "cfg_tool.bin")
    eq = sdk_file(sdk, "cfg/eq_cfg_hw.bin")
    if len(app) > app_slot:
        raise SystemExit("picovave_pack: app %d B > slot %d B" % (len(app), app_slot))
    app = app + b"\xff" * (app_slot - len(app))
    blk = 0x120 + app_slot + len(cfg_tool)               # what app_area_head describes
    area = bytearray(b"\xff" * blk)
    area[0x120:0x120 + app_slot] = app
    area[0x120 + app_slot:blk] = cfg_tool
    ents = [entry32(fwsc.crc16(app), 0x120, app_slot, 0x82, 0xFF, 0, "app.bin"),
            entry32(fwsc.crc16(cfg_tool), 0x120 + app_slot, len(cfg_tool), 0x82, 0xFF, 0, "cfg_tool.bin")]
    for off, size, flags, resvd, idx, nm in REGIONS:
        ents.append(entry32(0xFFFF, off, flash_size if size is None else size, flags, resvd, idx, nm))
    area[0x20:0x20 + len(b"".join(ents))] = b"".join(ents)
    area[0:0x20] = entry32(fwsc.crc16(bytes(area[0x20:blk])), APP_XIP, blk, 0x83, 0xFF, 0, "app_area_head")
    eq_e = entry32(fwsc.crc16(eq), 0x40, len(eq), 0x82, 0xFF, 1, "eq_cfg_hw.bin")
    cfg_body = eq_e + eq
    cfg = entry32(fwsc.crc16(cfg_body), 0x20, 0x20 + len(cfg_body), 0x83, 0xFF, 1, "cfg") + cfg_body
    region = bytearray(area + cfg)
    fwsc.sfc(region, 0, len(region), 0, SFC_KEY)
    if HEAD_SIZE + len(region) > flash_size:
        raise SystemExit("picovave_pack: app area ends at %#x, past %#x" % (HEAD_SIZE + len(region), flash_size))
    return bytes(region), len(eq)


def ufw(files, chip=UFW_CHIP):
    """The .fwsc container body: 0x40 header + 0x50 entries + data, ENC 0xFFFF per block."""
    off = 0x400
    ents = []
    for i, (typ, name, data) in enumerate(files):
        off = (off + 0xFF) & ~0xFF
        e = bytearray(0x50)
        struct.pack_into("<HHHHIII", e, 0, typ, i, fwsc.crc16(data), 0, off, len(data),
                         (len(data) + 31) & ~31)
        e[0x40:0x40 + len(name)] = name.encode()
        ents.append((e, off, data))
        off += len(data)
    total = off
    hdr = bytearray(0x40)
    struct.pack_into("<IH", hdr, 4, total, len(files))
    struct.pack_into("<HHH", hdr, 10, 0x0004, 0x0200, 0)
    hdr[16:16 + len(chip)] = chip
    lst = bytearray()
    for e, _, _ in ents:
        x = bytearray(e)
        fwsc.enc(x, 0, 0x50)
        lst += x
    struct.pack_into("<H", hdr, 2, fwsc.crc16(lst))
    struct.pack_into("<H", hdr, 0, fwsc.crc16(hdr[2:0x40]))
    fwsc.enc(hdr, 0, 0x40)
    logical = bytearray(b"\xff" * total)
    logical[0:0x40] = hdr
    logical[0x40:0x40 + len(lst)] = lst
    for e, o, d in ents:
        logical[o:o + len(d)] = d
    return bytes(logical)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", type=Path, required=True, help="linked app image (entry at 0x02000120)")
    ap.add_argument("--loader", type=Path, required=True,
                    help="update loader (ota.bin) shipped as the package's ota.bin entry")
    ap.add_argument("--out", type=Path, default=Path("build/picovave.fwsc"))
    ap.add_argument("--product", default="SMK-37 Pro_700", help="package identity (max 20 chars)")
    ap.add_argument("--sdk", type=Path, default=None, help="AC79 SDK checkout (default $AC79_SDK)")
    ap.add_argument("--app-slot", type=lambda s: int(s, 0), default=APP_SLOT)
    ap.add_argument("--flash-size", type=lambda s: int(s, 0), default=FLASH_SIZE)
    ap.add_argument("--isd-tag", default="[PICOVAVE]\r\n")
    a = ap.parse_args()

    sdk = a.sdk or os.environ.get("AC79_SDK")
    if not sdk:
        raise SystemExit("picovave_pack: set AC79_SDK or pass --sdk")
    if len(a.product) > 20:
        raise SystemExit("picovave_pack: product identity is at most 20 characters")

    app = a.app.read_bytes()
    ota = a.loader.read_bytes()
    # the running firmware identifies its own loader by this marker inside the first LZ4
    # literals and refuses to stage a package without it (Makkuro37 firmware/src/ota.c)
    if LOADER_MARK not in ota[:0x200]:
        raise SystemExit("picovave_pack: %s does not look like the update loader (no %r near its start)"
                         % (a.loader, LOADER_MARK))
    if len(ota) > 0x14000:
        raise SystemExit("picovave_pack: the update loader is %d B, over the 0x14000 staging area" % len(ota))
    head, spl = build_head(sdk, a.isd_tag.encode())
    area, eq_len = build_area(app, sdk, a.app_slot, a.flash_size)
    flash = bytearray(b"\xff" * a.flash_size)
    flash[0:HEAD_SIZE] = head
    flash[HEAD_SIZE:HEAD_SIZE + len(area)] = area
    flash = bytes(flash)
    logical = ufw([(0, "flash.bin", flash), (100, "ota.bin", ota)])
    raw = fwsc.interleave(logical, a.product, fwsc.DEFAULT_BLOCKS)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    (a.out.parent / "picovave.flash.bin").write_bytes(flash)
    a.out.write_bytes(raw)

    chk = fwsc.parse(raw)
    files = {f["entry"].name: f for f in chk["files"]}
    ok = (chk["product"] == a.product and chk["header"]["crc_hdr_ok"] and chk["header"]["crc_list_ok"]
          and chk["header"]["total_ok"] and files["flash.bin"]["crc_ok"])
    print("app      %d B (slot %#x)" % (len(app), a.app_slot))
    print("loader   %d B" % len(ota))
    print("head     %d B, SPL %d B, eq %d B" % (HEAD_SIZE, len(spl), eq_len))
    print("flash    %d B -> %s" % (len(flash), a.out.parent / "picovave.flash.bin"))
    print("package  %s  %d B, identity %r, markers %d" % (a.out, len(raw), chk["product"], chk["blocks"]))
    print("verify   header=%s filelist=%s total=%s flash.bin=%s" %
          (chk["header"]["crc_hdr_ok"], chk["header"]["crc_list_ok"], chk["header"]["total_ok"],
           files["flash.bin"]["crc_ok"]))
    if not ok:
        raise SystemExit("picovave_pack: self-check failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
