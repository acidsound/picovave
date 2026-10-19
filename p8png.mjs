// SPDX-License-Identifier: GPL-3.0-only
// p8png.mjs - unwrap a PICO-8 .p8.png into the cooked cart picovave uploads.
//
// A .p8.png is PICO-8's own compressed cart: the 32,800 bytes it carries sit one per pixel in
// the low two bits of a 160x205 image's channels (a=7..6, r=5..4, g=3..2, b=1..0), and because
// the container is a PNG, zlib compresses them again - which is why a .p8.png is a third the
// size of the same cart saved as text.
//
// The device never sees an image. Decoding one costs 160*205*4 = 131,200 B of RGBA (lodepng)
// plus an inflate window, and the device's entire Lua heap is 22,016 B, so the PAGE unwraps it
// here and sends the cooked cart (src/picovave_cart.h: magic + 32,800 B). The bytes are exactly
// what femto8's parse_png_ram() reads out of its decoded image (femto8/src/p8_parser.c): the
// 17,152-byte cartridge ROM, then a self-describing code section (PXA, ':c:' mini, or raw text
// for pre-0.1.7 carts) that the device expands into the Lua source. Nothing here interprets the
// cart - it only reverses the image.
//
// Shared by the page and by sim/run_page.mjs / sim/sim_test.mjs, which run it in Node: the bytes
// the hardware gets are the bytes those tests upload.

export const PNG_WIDTH = 160, PNG_HEIGHT = 205;
export const COOKED_SIZE = PNG_WIDTH * PNG_HEIGHT;      // 32,800
export const CART_ROM_SIZE = 0x4300;                    // 17,152: gfx, map, flags, music, sfx
export const CODE_SIZE = COOKED_SIZE - CART_ROM_SIZE;   // 15,648: the code section
export const CODE_RAW_MAX = 0x3d00;                     // 15,616: what a raw section may hold
export const COOKED_MAGIC = Uint8Array.from([0x50, 0x56, 0x43, 0x31]);   // "PVC1"
export const CART_LEN = COOKED_MAGIC.length + COOKED_SIZE;               // 32,804
export const CART_NAME_MAX = 31;

const PNG_SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

/* zlib (RFC 1950) - PNG's IDAT. The browser's own inflate: this page is an offline file with no
 * dependencies, and every engine that can run it has DecompressionStream. */
async function inflateZlib(z) {
  if (typeof DecompressionStream !== "function")
    throw new Error("no DecompressionStream here (Chrome, or Node 18+): cannot read .p8.png");
  const stream = new Blob([z]).stream().pipeThrough(new DecompressionStream("deflate"));
  const reader = stream.getReader();
  const chunks = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    n += value.length;
  }
  const out = new Uint8Array(n);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}

/* Undo the PNG scanline filters (RFC 2083 §6): each row is prefixed by its filter type and
 * coded against the row above; `stride` is one row of bytes, `bpp` the distance to the pixel
 * on the left. */
function unfilter(raw, w, h, bpp) {
  const stride = w * bpp;
  const out = new Uint8Array(stride * h);
  let prev = new Uint8Array(stride);
  let p = 0;
  for (let y = 0; y < h; y++) {
    if (p + 1 + stride > raw.length) throw new Error("PNG image data is short (row " + y + ")");
    const f = raw[p++];
    const line = out.subarray(y * stride, (y + 1) * stride);
    line.set(raw.subarray(p, p + stride));
    p += stride;
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? line[i - bpp] : 0;
      const b = prev[i];
      const c = i >= bpp ? prev[i - bpp] : 0;
      if (f === 1) line[i] = (line[i] + a) & 0xff;
      else if (f === 2) line[i] = (line[i] + b) & 0xff;
      else if (f === 3) line[i] = (line[i] + ((a + b) >> 1)) & 0xff;
      else if (f === 4) {
        const q = a + b - c;
        const pa = Math.abs(q - a), pb = Math.abs(q - b), pc = Math.abs(q - c);
        line[i] = (line[i] + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c)) & 0xff;
      } else if (f !== 0) throw new Error("PNG filter type " + f + " (row " + y + ")");
    }
    prev = line;
  }
  return { out, stride };
}

/* Read the image's pixels and keep only the low two bits of each channel, in image order - the
 * exact expression parse_png_ram() uses (femto8/src/p8_parser.c), one byte per pixel. */
function packLow2(px, w, h, color, plte, trns) {
  const bpp = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[color];
  const stride = w * bpp;
  const out = new Uint8Array(w * h);
  for (let i = 0, y = 0; y < h; y++) {
    for (let x = 0; x < w; x++, i++) {
      const o = y * stride + x * bpp;
      let r, g, b, a;
      if (color === 6) { r = px[o]; g = px[o + 1]; b = px[o + 2]; a = px[o + 3]; }
      else if (color === 2) {
        r = px[o]; g = px[o + 1]; b = px[o + 2];
        a = (trns && trns.length >= 6 && r === trns[1] && g === trns[3] && b === trns[5]) ? 0 : 255;
      } else if (color === 0) {
        r = g = b = px[o];
        a = (trns && trns.length >= 2 && px[o] === trns[1]) ? 0 : 255;
      } else {                                  // 3: palette
        const k = px[o] * 3;
        if (!plte || k + 2 >= plte.length) throw new Error("PNG palette index out of range");
        r = plte[k]; g = plte[k + 1]; b = plte[k + 2];
        a = (trns && px[o] < trns.length) ? trns[px[o]] : 255;
      }
      out[i] = ((a & 3) << 6) | ((r & 3) << 4) | ((g & 3) << 2) | (b & 3);
    }
  }
  return out;
}

/* A .p8.png -> the 32,800-byte cooked cart. Throws with the reason if it is not one. */
export async function cookedFromPng(png) {
  const d = png instanceof Uint8Array ? png : new Uint8Array(png);
  if (d.length < 8 || PNG_SIG.some((v, i) => d[i] !== v))
    throw new Error("not a PNG (a .p8.png starts with the PNG signature)");

  let w = 0, h = 0, depth = 0, color = -1, interlace = 0;
  let plte = null, trns = null;
  const idat = [];
  for (let p = 8; p + 8 <= d.length;) {
    const len = ((d[p] << 24) | (d[p + 1] << 16) | (d[p + 2] << 8) | d[p + 3]) >>> 0;
    const type = String.fromCharCode(d[p + 4], d[p + 5], d[p + 6], d[p + 7]);
    const body = d.subarray(p + 8, p + 8 + len);
    if (body.length !== len) throw new Error("truncated PNG (" + type + " chunk)");
    p += 12 + len;                            // length + type + data + CRC
    if (type === "IHDR") {
      w = (body[0] << 24) | (body[1] << 16) | (body[2] << 8) | body[3];
      h = (body[4] << 24) | (body[5] << 16) | (body[6] << 8) | body[7];
      depth = body[8];
      color = body[9];
      interlace = body[12];
    } else if (type === "PLTE") plte = body;
    else if (type === "tRNS") trns = body;
    else if (type === "IDAT") idat.push(body);
    else if (type === "IEND") break;
  }

  if (w !== PNG_WIDTH || h !== PNG_HEIGHT)
    throw new Error("a .p8.png is " + PNG_WIDTH + "x" + PNG_HEIGHT + ", this image is " + w + "x" + h);
  if (interlace !== 0)
    throw new Error("interlaced PNG (PICO-8 writes .p8.png non-interlaced)");
  if (depth !== 8 || ![0, 2, 3, 6].includes(color))
    throw new Error("unsupported PNG (bit depth " + depth + ", colour type " + color + ")");
  if (!idat.length)
    throw new Error("PNG has no image data");

  let z = 0;
  for (const c of idat) z += c.length;
  const zlib = new Uint8Array(z);
  let o = 0;
  for (const c of idat) { zlib.set(c, o); o += c.length; }

  const raw = await inflateZlib(zlib);
  const bpp = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[color];
  const { out } = unfilter(raw, w, h, bpp);
  return packLow2(out, w, h, color, plte, trns);
}

/* A .p8.png -> the payload that goes over SysEx and into a slot: the magic, then the cart. */
export async function cartPayload(png) {
  const cooked = await cookedFromPng(png);
  const out = new Uint8Array(CART_LEN);
  out.set(COOKED_MAGIC, 0);
  out.set(cooked, COOKED_MAGIC.length);
  return out;
}

/* A cooked cart from a Lua script and (optionally) a cartridge ROM, with a RAW code section -
 * no ':c:'/PXA header, which is what the pre-0.1.7 form of the code section is, so the device's
 * expander copies it verbatim. This is how the built-in default cart and the tests' synthetic
 * carts are made; a real .p8.png always carries a compressed section. */
export function cookedFromScript(script, rom) {
  const out = new Uint8Array(COOKED_SIZE);
  if (rom) {
    if (rom.length !== CART_ROM_SIZE) throw new Error("cartridge ROM is " + rom.length + ", not " + CART_ROM_SIZE);
    out.set(rom, 0);
  }
  const text = new TextEncoder().encode(script);
  if (text.length > CODE_RAW_MAX)
    throw new Error("raw code section is " + text.length + " B, over " + CODE_RAW_MAX);
  out.set(text, CART_ROM_SIZE);
  return out;
}
