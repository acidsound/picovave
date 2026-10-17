/* SPDX-License-Identifier: GPL-3.0-only */
/* fwsc.mjs - read a JieLi/M-Vave ".fwsc" update package, in the browser.
 *
 * A faithful port of tools/fwsc.py (the independent structural codec). It exists so the
 * simulator page can verify a package and show exactly which flash bytes an install would
 * write, with no round trip to Python. tools/verify_final.py cross-checks THIS file against
 * tools/fwsc.py on the real packages, so the port cannot drift from the Python that every
 * other tool trusts: if this file is wrong, verification fails.
 *
 * The container layout is documented, byte for byte, at the top of tools/fwsc.py. Read that
 * first; this file only mirrors it.
 */

export const BLK = 0x30;            /* one interleave block */
export const KEEP = 0x2F;           /* payload bytes kept per block */
export const DEFAULT_BLOCKS = 20;   /* FM-1 / SMK-37 v011..v015 marker count */
export const MAX_BLOCKS = 64;

const T = (() => {
  const t = new Uint16Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i << 8;
    for (let k = 0; k < 8; k++)
      c = (c & 0x8000) ? (((c << 1) ^ 0x1021) & 0xFFFF) : ((c << 1) & 0xFFFF);
    t[i] = c & 0xFFFF;
  }
  return t;
})();

export function crc16(data, crc = 0) {
  for (let i = 0; i < data.length; i++)
    crc = (((crc << 8) & 0xFFFF) ^ T[((crc >> 8) ^ data[i]) & 0xFF]) & 0xFFFF;
  return crc;
}

/* JieLi ENC stream cipher, in place; its own inverse (fwsc.py: enc). */
export function enc(buf, off, size, key = 0xFFFF) {
  for (let i = 0; i < size; i++) {
    buf[off + i] ^= key & 0xFF;
    key = ((key << 1) ^ ((key & 0x8000) ? 0x1021 : 0)) & 0xFFFF;
  }
}

function decoded(src, off, size) {          /* a decrypted copy of src[off..off+size) */
  const b = src.slice(off, off + size);
  enc(b, 0, b.length);
  return b;
}

function u16(b, o) { return b[o] | (b[o + 1] << 8); }
function u32(b, o) { return (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0; }
function cstr(b, off, end) {                 /* a NUL-terminated latin-1 string */
  let s = "";
  for (let i = off; i < end && b[i] !== 0; i++) s += String.fromCharCode(b[i]);
  return s;
}

/* Deinterleave assuming exactly n marker blocks -> [logical image, identity]. */
function deinterleaveN(raw, n) {
  const parts = [];
  let ident = "";
  for (let i = 0; i < n; i++) {
    parts.push(raw.subarray(i * BLK, i * BLK + KEEP));
    const mi = i * BLK + KEEP;
    if (mi < raw.length) {
      const m = raw[mi];
      if (m !== 0x7D) ident += String.fromCharCode((m - i - 1) & 0xFF);
    }
  }
  parts.push(raw.subarray(Math.min(n * BLK, raw.length)));
  let len = 0;
  for (const p of parts) len += p.length;
  const out = new Uint8Array(len);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return [out, ident];
}

/* The header CRC and file-list CRC are the format's own integrity net: a wrong block count
 * scrambles both, so they decide the count (fwsc.py: _logical_ok). */
function logicalOk(logical) {
  if (logical.length < 0x40) return false;
  const hdr = decoded(logical, 0, 0x40);
  if (crc16(hdr.subarray(2, 0x40)) !== u16(hdr, 0)) return false;
  const nfiles = u16(hdr, 8);
  if (!(nfiles >= 1 && nfiles <= 16)) return false;
  const lst = logical.subarray(0x40, 0x40 + nfiles * 0x50);
  if (lst.length !== nfiles * 0x50) return false;
  return crc16(lst) === u16(hdr, 2);
}

/* The header lives in the first logical bytes, unaffected by the marker count as long as
 * n >= 2, so it can be read with the default count (fwsc.py: header_of). */
function headerOf(raw) {
  const [logical] = deinterleaveN(raw, DEFAULT_BLOCKS);
  return decoded(logical, 0, 0x40);
}

/* How many interleave markers the package carries. The header declares the logical
 * (marker-free) size, so the count is len(raw) - total (fwsc.py: blocks_of). */
function blocksOf(raw) {
  if (raw.length >= 0x40) {
    const total = u32(headerOf(raw), 4);
    const n = raw.length - total;
    if (n >= DEFAULT_BLOCKS && n <= MAX_BLOCKS) {
      const [logical] = deinterleaveN(raw, n);
      if (logicalOk(logical) && logical.length === total) return n;
    }
  }
  for (let n = DEFAULT_BLOCKS; n <= MAX_BLOCKS; n++) {
    if (n * BLK > raw.length) break;
    const [logical] = deinterleaveN(raw, n);
    if (logicalOk(logical)) return n;
  }
  return DEFAULT_BLOCKS;
}

/* Decode count chained 0x50-byte file-list entries (encrypted form). */
function parseEntries(buf, off, count) {
  const out = [];
  for (let i = 0; i < count; i++) {
    const e = decoded(buf, off + i * 0x50, 0x50);
    out.push({
      type: u16(e, 0), index: u16(e, 2), crc: u16(e, 4),
      offset: u32(e, 8), length: u32(e, 12), padded: u32(e, 16),
      name: cstr(e, 0x40, 0x50),
    });
  }
  return out;
}

/* Decode a .fwsc package -> the same shape tools/fwsc.py: parse() returns. */
export function parse(raw) {
  if (raw instanceof ArrayBuffer) raw = new Uint8Array(raw);
  const nblocks = blocksOf(raw);
  const [logical, ident] = deinterleaveN(raw, nblocks);
  if (logical.length < 0x40) throw new Error("package too short");
  const hdr = decoded(logical, 0, 0x40);
  const crcHdr = u16(hdr, 0), crcLst = u16(hdr, 2);
  const total = u32(hdr, 4), nfiles = u16(hdr, 8);
  const list = logical.subarray(0x40, 0x40 + nfiles * 0x50);
  const files = parseEntries(logical, 0x40, nfiles).map((e) => {
    const data = logical.subarray(e.offset, e.offset + e.length);
    return { ...e, data, declared: e.length, got: data.length, crcOk: crc16(data) === e.crc };
  });
  return {
    product: ident,
    blocks: nblocks,
    logicalSize: logical.length,
    header: {
      crcHdr, crcHdrOk: crc16(hdr.subarray(2, 0x40)) === crcHdr,
      crcList: crcLst, crcListOk: crc16(list) === crcLst,
      total, totalOk: total === logical.length,
      nfiles, consts: [u16(hdr, 10), u16(hdr, 12), u16(hdr, 14)], chip: cstr(hdr, 16, 0x40),
    },
    files,
  };
}
