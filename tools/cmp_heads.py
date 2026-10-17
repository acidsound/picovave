#!/usr/bin/env python3
"""Concise head comparison: which SPL does each package carry?"""
import sys, hashlib, os
sys.path.insert(0, "tools")
import fwsc

def head_of(pkg):
    d = fwsc.parse(open(pkg, "rb").read())
    fl = [f for f in d["files"] if f["entry"].name == "flash.bin"][0]["data"]
    return d["product"], fl[:0x4000]

pkgs = {
 "stock015": "/Users/spectrum/Downloads/SMK-37 Pro_015.fwsc",
 "stock016": "/Users/spectrum/Downloads/SMK-37_Pro_016.fwsc",
 "x0x37-0.2": "/Users/spectrum/Downloads/x0x37-0.2.fwsc",
 "picovave": "build/picovave.fwsc",
}
if os.path.exists("repo/build/makkuro37.fwsc"):   # optional control; picovave does not depend on repo/
    pkgs["makkuro37"] = "repo/build/makkuro37.fwsc"
heads = {}
for k, p in pkgs.items():
    prod, h = head_of(p)
    heads[k] = h
    print("%-10s identity=%-16r head sha256=%s" % (k, prod, hashlib.sha256(h).hexdigest()[:32]))

sdk_spl = open("/Users/spectrum/fw-AC79_AIoT_SDK/cpu/wl82/tools/uboot.boot","rb").read()
print()
print("SDK uboot.boot %d B sha256 %s" % (len(sdk_spl), hashlib.sha256(sdk_spl).hexdigest()[:32]))

def region(h, off, n):
    b = bytearray(h[off:off+n]); 
    return bytes(b)

print()
print("head byte ranges that differ between picovave and each reference:")
ref = heads["picovave"]
for k, h in heads.items():
    d = [i for i in range(0x4000) if h[i] != ref[i]]
    if not d:
        print("  %-10s identical" % k); continue
    # compress to ranges
    rngs, start, prev = [], d[0], d[0]
    for i in d[1:]:
        if i != prev + 1:
            rngs.append((start, prev)); start = i
        prev = i
    rngs.append((start, prev))
    print("  %-10s %d differing bytes in %d ranges: %s" % (k, len(d), len(rngs),
          ", ".join("%#x-%#x" % r for r in rngs[:12]) + (" ..." if len(rngs) > 12 else "")))

print()
print("Is the stock SPL block [0xa0, 0x10a0) equal to the SDK uboot.boot?")
st = heads["stock016"]
print("  stock[0xa0:0x10a0] == sdk uboot.boot :", st[0xa0:0x10a0] == sdk_spl)
print("  stock[0xa0:0x10a0] == picovave[0xa0:0x10a0] :", st[0xa0:0x10a0] == ref[0xa0:0x10a0])
print("  first 64 bytes stock :", st[0xa0:0xa0+64].hex())
print("  first 64 bytes sdk   :", sdk_spl[:64].hex())
print("  first 64 bytes picovave :", ref[0xa0:0xa0+64].hex())
