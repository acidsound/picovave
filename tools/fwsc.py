#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Independent structural codec for JieLi/M-Vave ".fwsc" update packages.

Written from the byte layout only (no reuse of the Makkuro37 packaging code), so it
can be used to cross-check packages produced by other tools.

Container (".fwsc")
-------------------
  logical image:  0x40-byte header, then 0x50-byte file list entries, then file data
  header:   [0:2]   CRC16 over header[2:0x40]
            [2:4]   CRC16 over the file list entries (encrypted form)
            [4:8]   u32 total logical size
            [8:10]  u16 file count
            [10:16] 0004 0200 0000   (packer constants)
            [16:..] chip name, NUL padded ("AC791N")
  entries (0x50 each, chained at 0x40):
            [0:2] type, [2:4] index, [4:6] CRC16(data), [6:8] 0,
            [8:12] u32 data offset, [12:16] u32 length, [16:20] u32 length rounded to 32,
            [0x40:0x50] file name, NUL padded
  Each 32-byte block of the header and each whole entry is scrambled with the JieLi
  "ENC" stream cipher, key 0xFFFF, restarted per 32-byte block. File data is plain.
  Interleave: one marker byte after every 0x2F bytes for the first N blocks
  (N = 20, or more for longer identities). Marker = ordinal(identity char) + i + 1,
  or 0x7D when the identity is shorter than the block count.

flash.bin (the head, and the app area)
--------------------------------------
  [0:0x4000) head: 32-byte flash header (CRC16 over the following 30 bytes), then JLFS
             entries: "uboot.boot" (the SPL), "isd_config.ini", "app_dir_head", "key_mac".
             Encrypted as 32-byte ENC blocks, key 0xFFFF.
  [0x4000:]  app area, SFC-encrypted with the chip key:
             [0:0x20)   "app_area_head" entry, CRC16 over the whole area
             [0x20:0x120) file entries (app.bin, cfg_tool.bin, region descriptors)
             [0x120:0x120+APP_SLOT) the app image
             [..]       cfg_tool.bin
             then a nested "cfg" directory ("eq_cfg_hw.bin")
"""
import struct

BLK = 0x30
KEEP = 0x2F
DEFAULT_BLOCKS = 20
MAX_BLOCKS = 64


def _crc_table():
    t = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) if c & 0x8000 else (c << 1)
        t.append(c & 0xFFFF)
    return t


_T = _crc_table()


def crc16(data, crc=0):
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _T[((crc >> 8) ^ b) & 0xFF]
    return crc


def enc(buf, off, size, key=0xFFFF):
    """JieLi ENC stream cipher, in place (its own inverse)."""
    for i in range(size):
        buf[off + i] ^= key & 0xFF
        key = ((key << 1) ^ (0x1021 if key & 0x8000 else 0)) & 0xFFFF


def sfc(buf, off, size, base, key):
    """SFC cipher: ENC per 32-byte block, the key mixed with the block address."""
    for i in range(0, size, 32):
        enc(buf, off + i, min(size - i, 32), key ^ ((off + i - base) >> 2))


def _marker(raw, k):
    return raw[k * BLK + KEEP] if (k + 1) * BLK <= len(raw) else None


def id_char(m, i):
    c = (m - i - 1) & 0xFF
    return 0x20 <= c < 0x7F


def deinterleave_n(raw, n):
    """Deinterleave assuming exactly n marker blocks."""
    out = bytearray()
    ident = ""
    for i in range(n):
        out += raw[i * BLK:i * BLK + KEEP]
        m = raw[i * BLK + KEEP]
        if m != 0x7D:
            ident += chr((m - i - 1) & 0xFF)
    out += raw[n * BLK:]
    return bytes(out), ident


def _logical_ok(logical):
    """The header CRC and the file-list CRC are the format's own integrity net; a wrong block
    count scrambles both, so they are what decides the count."""
    if len(logical) < 0x40:
        return False
    hdr = bytearray(logical[:0x40])
    enc(hdr, 0, 0x40)
    if crc16(bytes(hdr[2:0x40])) != struct.unpack_from("<H", hdr, 0)[0]:
        return False
    nfiles = struct.unpack_from("<H", hdr, 8)[0]
    if not 1 <= nfiles <= 16:
        return False
    lst = logical[0x40:0x40 + nfiles * 0x50]
    if len(lst) != nfiles * 0x50:
        return False
    return crc16(lst) == struct.unpack_from("<H", hdr, 2)[0]


def header_of(raw):
    """The package header is in the first logical bytes, so it is unaffected by the marker
    count as long as n >= 2. Decode it once and read the declared logical size."""
    logical, _ = deinterleave_n(raw, DEFAULT_BLOCKS)
    hdr = bytearray(logical[:0x40])
    enc(hdr, 0, 0x40)
    return hdr


def blocks_of(raw):
    """How many interleave markers the package carries.

    The header declares the logical (marker-free) size, so the marker count is
    len(raw) - total. That is exact and self-describing: 20 for the FM-1 / SMK-37
    v011..v015 layout, 36 for the stock SMK-37 Pro v016 (14-character identity, the
    rest unused 0x7D). Falls back to the CRC scan when the header is unreadable."""
    if len(raw) >= 0x40:
        total = struct.unpack_from("<I", header_of(raw), 4)[0]
        n = len(raw) - total
        if DEFAULT_BLOCKS <= n <= MAX_BLOCKS:
            logical, _ = deinterleave_n(raw, n)
            if _logical_ok(logical) and len(logical) == total:
                return n
    for n in range(DEFAULT_BLOCKS, MAX_BLOCKS + 1):
        if (n * BLK) > len(raw):
            break
        logical, _ = deinterleave_n(raw, n)
        if _logical_ok(logical):
            return n
    return DEFAULT_BLOCKS


def deinterleave(raw):
    """-> (logical image, identity string, block count)"""
    n = blocks_of(raw)
    out = bytearray()
    ident = ""
    for i in range(n):
        out += raw[i * BLK:i * BLK + KEEP]          # 0x2F payload bytes
        m = raw[i * BLK + KEEP]                     # then the marker byte
        if m != 0x7D:
            ident += chr((m - i - 1) & 0xFF)
    out += raw[n * BLK:]                            # the tail after the last marker
    return bytes(out), ident, n


def interleave(logical, product, n=DEFAULT_BLOCKS):
    raw = bytearray()
    for i in range(n):
        ch = product[i] if i < len(product) else None
        m = (ord(ch) + i + 1) & 0xFF if ch else 0x7D
        raw += logical[i * KEEP:(i + 1) * KEEP] + bytes([m])
    raw += logical[n * KEEP:]
    return bytes(raw)


class Entry:
    __slots__ = ("index", "type", "crc", "offset", "length", "padded", "name", "raw")

    def __repr__(self):
        return (f"Entry(type={self.type:#06x} idx={self.index} crc={self.crc:#06x} "
                f"off={self.offset:#x} len={self.length} padded={self.padded} name={self.name!r})")


def parse_entries(buf, off, count):
    """Decode count chained 0x50-byte entries starting at off (encrypted form)."""
    out = []
    for i in range(count):
        e = bytearray(buf[off + i * 0x50:off + (i + 1) * 0x50])
        enc(e, 0, 0x50)
        typ, idx, crc, _z, doff, dlen, dpad = struct.unpack_from("<HHHHIII", e, 0)
        name = bytes(e[0x40:0x50]).split(b"\0")[0].decode("latin-1")
        ent = Entry()
        ent.index, ent.type, ent.crc, ent.offset = idx, typ, crc, doff
        ent.length, ent.padded, ent.name, ent.raw = dlen, dpad, name, bytes(e)
        out.append(ent)
    return out


def parse(raw):
    """Decode a .fwsc package -> dict."""
    logical, ident, nblocks = deinterleave(raw)
    if len(logical) < 0x40:
        raise ValueError("package too short")
    hdr = bytearray(logical[:0x40])
    enc(hdr, 0, 0x40)
    crc_hdr = struct.unpack_from("<H", hdr, 0)[0]
    crc_lst = struct.unpack_from("<H", hdr, 2)[0]
    total, nfiles = struct.unpack_from("<IH", hdr, 4)
    consts = struct.unpack_from("<HHH", hdr, 10)
    chip = bytes(hdr[16:0x40]).split(b"\0")[0].decode("latin-1")
    lst = logical[0x40:0x40 + nfiles * 0x50]
    ents = parse_entries(logical, 0x40, nfiles)
    files = []
    for e in ents:
        data = logical[e.offset:e.offset + e.length]
        files.append({"entry": e, "data": data, "crc_ok": crc16(data) == e.crc,
                      "declared": e.length, "got": len(data)})
    return {
        "product": ident,
        "blocks": nblocks,
        "logical_size": len(logical),
        "header": {
            "crc_hdr": crc_hdr, "crc_hdr_ok": crc16(bytes(hdr[2:0x40])) == crc_hdr,
            "crc_list": crc_lst, "crc_list_ok": crc16(lst) == crc_lst,
            "total": total, "total_ok": total == len(logical),
            "nfiles": nfiles, "consts": [hex(c) for c in consts], "chip": chip,
        },
        "files": files,
    }


def parse_flash_head(flash):
    """Decode the 0x4000-byte head of a flash image -> list of JLFS entries + flag."""
    hdr = bytearray(flash[:0x20])
    enc(hdr, 0, 0x20)
    hdr_crc = struct.unpack_from("<H", hdr, 0)[0]
    ok = crc16(bytes(hdr[2:0x20])) == hdr_crc
    ents = []
    e = bytearray(flash[0x20:0x20 + 6 * 0x20])
    for i in range(6):
        blk = bytearray(e[i * 0x20:(i + 1) * 0x20])
        enc(blk, 0, 0x20)
        ents.append(blk)
    return ok, hdr, ents
