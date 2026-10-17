#!/usr/bin/env python3
"""Decode flashed images: head (ENC 0xFFFF) and app area (SFC, chip key)."""
import sys, struct, os
sys.path.insert(0, "tools")
import fwsc

KEY = 0x980F
APP_SLOT_SMK = 0x97400

def jlfs_entries(head, off, count, size=0x20):
    out = []
    for i in range(count):
        b = bytearray(head[off + i*size: off + (i+1)*size])
        fwsc.enc(b, 0, size)
        crc, o, n = struct.unpack_from("<HII", b, 0)
        flags, resvd, idx = b[10], b[11], struct.unpack_from("<H", b, 12)[0]
        name = bytes(b[14:30]).split(b"\0")[0].decode("latin-1")
        assert fwsc.crc16(bytes(b[2:32])) == crc, "entry CRC mismatch for %r" % name
        out.append(dict(crc=crc, off=o, size=n, flags=flags, resvd=resvd, idx=idx, name=name))
    return out

def analyse(pkg, label):
    d = fwsc.parse(open(pkg, "rb").read())
    fl = [f for f in d["files"] if f["entry"].name == "flash.bin"][0]["data"]
    print("="*78); print(label, "  flash.bin", len(fl), hex(len(fl)))
    hdr = bytearray(fl[:0x20]); fwsc.enc(hdr, 0, 0x20)
    print("  flash hdr crc=%04x ok=%s bytes=%s" % (struct.unpack_from("<H",hdr,0)[0],
          fwsc.crc16(bytes(hdr[2:0x20])) == struct.unpack_from("<H",hdr,0)[0], bytes(hdr[2:0x20]).hex()))
    for e in jlfs_entries(fl, 0x20, 6):
        print("   head %-16s off=%#08x size=%-8d flags=%02x resvd=%02x idx=%d" %
              (e["name"], e["off"], e["size"], e["flags"], e["resvd"], e["idx"]))
    # isd_config blob -> chip key
    isd = None
    for e in jlfs_entries(fl, 0x20, 6):
        if e["name"] == "isd_config.ini":
            isd = fl[e["off"]:e["off"]+e["size"]]
    if isd:
        print("  isd_config %d B: %s" % (len(isd), isd[:48].hex()))
        core = isd[:32]; tail = isd[32:]
        s = sum(core[:16]) & 0xFF
        s = 0xAA if s >= 0xE0 else 0x55 if s <= 0x10 else s
        key = sum(1 << i for i in range(16) if (core[16+i] ^ core[15-i]) < s)
        print("  -> chip key %#06x   tail=%r" % (key, tail[:24]))
    # app area
    region = bytearray(fl[0x4000:])
    fwsc.sfc(region, 0, len(region), 0, KEY)
    are = jlfs_entries(region, 0, 1)
    print("  app_area_head off=%#x size=%#x type/crc=%04x" % (are[0]["off"], are[0]["size"], are[0]["crc"]))
    blk = are[0]["size"]
    print("  area crc over [0x20:%#x) ok=%s" % (blk, fwsc.crc16(bytes(region[0x20:blk])) == are[0]["crc"]))
    ents = jlfs_entries(region, 0x20, 8)
    for e in ents:
        print("   app  %-18s off=%#08x size=%-8d flags=%02x resvd=%02x idx=%d" %
              (e["name"], e["off"], e["size"], e["flags"], e["resvd"], e["idx"]))
    return region, ents

analyse("/Users/spectrum/Downloads/SMK-37 Pro_015.fwsc", "STOCK 015")
if os.path.exists("repo/build/makkuro37.fwsc"):   # optional control; picovave does not depend on repo/
    analyse("repo/build/makkuro37.fwsc", "MAKKURO37 control (optional)")
