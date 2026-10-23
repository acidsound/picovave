#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Cut the app area out of a .fwsc package, for a mask-ROM-UBOOT (re)install.

An M-UPGRADE install (web FLASH(USB) / fm1_install.py) writes exactly the app area of the
package — SMK-37 Pro: flash 0x04000-0x9CFFF, i.e. the package's flash.bin bytes
[0x4000:0x9D000] = 0x99000 bytes. The package's flash.bin is the image as it lies in flash
(the same bytes the update loader programs), so writing this range with jl-uboot-tool from the
mask-ROM bootloader is byte-identical to a normal install.

Use this when the device does not enumerate over USB (so nothing can M-UPGRADE it) but the
mask-ROM UBOOT is reachable (`WL82 UBOOT1.00`, USB 4C4A:8057). See docs/RECOVERY.md.

    python3 tools/make_app_area.py build/picovave.fwsc
    python3 tools/make_app_area.py build/picovave.fwsc -o build/picovave/picovave.app.bin

Writes nothing unless -o is given (default: alongside the package, "<stem>.app-area.bin").
Adds the exact jl-uboot-tool commands to run on Linux (root) / Windows (admin) — never macOS.
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fwsc

# SMK-37 Pro flash map (repo/firmware/hal/fm1_flash_map.h). The app slot is the one range an
# M-UPGRADE install replaces; everything else is protected and must not be written here.
APP_LO = 0x04000          # FL_APP_LO
APP_HI = 0x09D000         # FL_APP_HI
PROTECTED = [
    (0x00000, 0x04000, "head: SPL / isd_config / app_dir_head"),
    (0x0C0000, 0x0C2000, "BTIF + USRTRIM (Bluetooth pairing, key/pad calibration)"),
    (0x0F4000, 0x0FE000, "stock USR resources"),
    (0x0FF000, 0x100000, "key_mac"),
]


def flash_bin_of(path):
    """Return the flash.bin bytes of a .fwsc package (the type-0 entry)."""
    raw = open(path, "rb").read()
    d = fwsc.parse(raw)
    fb = [f for f in d["files"] if f["entry"].type == 0]
    if len(fb) != 1:
        raise SystemExit("%s: expected one flash.bin entry, got %d" % (path, len(fb)))
    if not fb[0]["crc_ok"]:
        raise SystemExit("%s: flash.bin CRC mismatch — refusing" % path)
    return d["product"], fb[0]["data"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", help="a .fwsc package (e.g. build/picovave.fwsc)")
    ap.add_argument("-o", "--out", help="output file (default <stem>.app-area.bin)")
    ap.add_argument("--full", action="store_true", help="write entire flash.bin from 0x0000 (recommended for stock firmware packages)")
    args = ap.parse_args()

    product, fb = flash_bin_of(args.package)
    is_official = not any(tag in product for tag in ("_700", "_900", "picovave"))
    use_full = args.full or is_official

    if use_full:
        start_addr = 0x00000
        app = fb
        app_hi = len(fb)
        range_desc = "full flash.bin (stock restore)"
    else:
        start_addr = APP_LO
        if len(fb) == 0x93000:
            app_hi = 0x93000
        elif len(fb) == 0x9D000:
            app_hi = 0x9D000
        else:
            app_hi = len(fb)
        app = fb[APP_LO:app_hi]
        range_desc = "app area"

    out = args.out or os.path.join(os.path.dirname(args.package) or ".",
                                   os.path.splitext(os.path.basename(args.package))[0] + ".app-area.bin")
    with open(out, "wb") as fh:
        fh.write(app)

    try:
        rel_pkg = os.path.relpath(args.package)
        rel_out = os.path.relpath(out)
    except Exception:
        rel_pkg = args.package
        rel_out = out

    print("package   %s" % rel_pkg)
    print("product   %s" % product)
    print("start     0x%05X" % start_addr)
    print("range     0x%05X-0x%05X  (%d bytes, 0x%05X) [%s]" % (start_addr, start_addr + len(app) - 1, len(app), len(app), range_desc))
    print("sha256    %s" % hashlib.sha256(app).hexdigest())
    print("wrote     %s" % rel_out)
    print()
    print("On Linux (root) or Windows (admin) or macOS (sudo) with the device in `WL80/WL82 UBOOT`:")
    print("  # 0. back up the whole flash first (twice, compare): read 0 0x100000 fm1-dump-1.bin")
    print("  python3 tools/jl-uboot-tool/jluboottool.py --chip wl82 \"erase 0x%X 0x%X\"" % (start_addr, len(app)))
    print("  python3 tools/jl-uboot-tool/jluboottool.py --chip wl82 \"write 0x%X %s\"" % (start_addr, rel_out))
    print("  python3 tools/jl-uboot-tool/jluboottool.py --chip wl82 \"read 0x%X 0x%X check.bin\"" % (start_addr, len(app)))
    print("  # check.bin must equal %s (sha256 above). Then power-cycle." % rel_out)
    print()
    print("Never write these in UBOOT (restore only from your own dump):")
    for lo, hi, what in PROTECTED:
        if not (start_addr == 0 and lo == 0):
            print("  0x%05X-0x%05X  %s" % (lo, hi - 1, what))


if __name__ == "__main__":
    main()
