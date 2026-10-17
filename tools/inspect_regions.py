#!/usr/bin/env python3
import sys, struct, os
sys.path.insert(0, "tools")
import fwsc

KEY = 0x980F

def sfc_decode(pkg):
    d = fwsc.parse(open(pkg, "rb").read())
    fl = [f for f in d["files"] if f["entry"].name == "flash.bin"][0]["data"]
    region = bytearray(fl[0x4000:])
    fwsc.sfc(region, 0, len(region), 0, KEY)
    ents = []
    for i in range(10):
        b = bytearray(region[0x20 + i*0x20: 0x20 + (i+1)*0x20])
        crc, dcrc, off, size = struct.unpack_from("<HHII", b, 0)
        flags, resvd, idx = b[12], b[13], struct.unpack_from("<H", b, 14)[0]
        name = bytes(b[16:]).split(b"\0")[0]
        nmraw = bytes(b[16:])
        ok = fwsc.crc16(bytes(b[2:32])) == crc
        ents.append((name, off, size, flags, resvd, idx, nmraw.hex(), ok))
    head = bytearray(region[0:0x20])
    crc, dcrc, off, size = struct.unpack_from("<HHII", head, 0)
    return d, fl, ents, (crc, off, size, fwsc.crc16(bytes(head[2:32])) == crc), region

PACKAGES = ["/Users/spectrum/Downloads/SMK-37 Pro_015.fwsc",
            "/Users/spectrum/Downloads/SMK-37_Pro_016.fwsc"]
if os.path.exists("repo/build/makkuro37.fwsc"):   # optional control; picovave does not depend on repo/
    PACKAGES.append("repo/build/makkuro37.fwsc")
for pkg in PACKAGES:
    d, fl, ents, head, region = sfc_decode(pkg)
    print("="*80)
    print(pkg, "identity", d["product"], "flash.bin", len(fl), hex(len(fl)))
    print("  app_area_head: crc=%04x off=%#x size=%#x ok=%s" % (head[0], head[1], head[2], head[3]))
    for name, off, size, flags, resvd, idx, nmraw, ok in ents:
        print("   %-14s off=%#010x size=%-8d flags=%02x resvd=%02x idx=%-3d crc_ok=%s name16=%s"
              % (name.decode('latin-1'), off, size, flags, resvd, idx, ok, nmraw))
