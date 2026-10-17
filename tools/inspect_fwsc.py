#!/usr/bin/env python3
import sys, hashlib, struct, os
sys.path.insert(0, "tools")
import fwsc

paths = [
    "/Users/spectrum/Downloads/SMK-37 Pro_015.fwsc",
    "/Users/spectrum/Downloads/SMK-37_Pro_016.fwsc",
    "/Users/spectrum/Downloads/x0x37-0.2.fwsc",
]
if os.path.exists("repo/build/makkuro37.fwsc"):   # optional control; picovave does not depend on repo/
    paths.append("repo/build/makkuro37.fwsc")
for p in paths:
    raw = open(p, "rb").read()
    d = fwsc.parse(raw)
    h = d["header"]
    print("=" * 78)
    print(p)
    print("  sha256 %s  size %d" % (hashlib.sha256(raw).hexdigest()[:32], len(raw)))
    print("  product %r  blocks %d  logical %d" % (d["product"], d["blocks"], d["logical_size"]))
    print("  hdr: crc=%04x ok=%s list_crc_ok=%s total=%d ok=%s nfiles=%d consts=%s chip=%r"
          % (h["crc_hdr"], h["crc_hdr_ok"], h["crc_list_ok"], h["total"], h["total_ok"], h["nfiles"], h["consts"], h["chip"]))
    for f in d["files"]:
        e = f["entry"]
        print("     %-16s off=%#010x len=%-9d padded=%-9d crc=%04x ok=%s" % (e.name, e.offset, e.length, e.padded, e.crc, f["crc_ok"]))
