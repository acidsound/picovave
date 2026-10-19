// SPDX-License-Identifier: GPL-3.0-only
// Shared screen conversion for the picovave simulator: the panel's RGB565 buffer to RGBA
// (browser) or RGB (files). The page and the Node test use this one implementation, so
// what is shown and what is written out are the same pixels.

export const W = 240;
export const H = 240;

// fb: Uint16Array (RGB565) -> rgba: Uint8ClampedArray (W*H*4)
export function toRGBA(fb, rgba) {
  for (let i = 0; i < W * H; i++) {
    const c = fb[i];
    rgba[4 * i] = (((c >> 11) & 31) * 255) / 31;
    rgba[4 * i + 1] = (((c >> 5) & 63) * 255) / 63;
    rgba[4 * i + 2] = ((c & 31) * 255) / 31;
    rgba[4 * i + 3] = 255;
  }
  return rgba;
}

// fb -> PPM (P6) bytes, for the Node test and offline inspection
export function toPPM(fb) {
  const head = Buffer.from("P6\n" + W + " " + H + "\n255\n");
  const body = Buffer.alloc(W * H * 3);
  for (let i = 0; i < W * H; i++) {
    const c = fb[i];
    body[3 * i] = (((c >> 11) & 31) * 255) / 31;
    body[3 * i + 1] = (((c >> 5) & 63) * 255) / 63;
    body[3 * i + 2] = ((c & 31) * 255) / 31;
  }
  return Buffer.concat([head, body]);
}
