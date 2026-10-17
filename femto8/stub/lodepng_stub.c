/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave stub: LodePNG, replaced with a decoder that always fails.
 *
 * picovave's carts are `.p8.png`, but the PAGE decodes the image (the low two bits of each
 * 160x205 pixel are the cart) and hands the device the 32,800-byte cooked cart, so no PNG
 * ever reaches the engine: decoding 160x205 RGBA needs 131,200 B and the device's Lua heap
 * is smaller than that. p8_parser.c still references these two symbols in its PNG entry
 * points; this keeps the PNG codec out of the image. */
#include <stddef.h>
#include "lodepng.h"

unsigned lodepng_decode32(unsigned char **out, unsigned *w, unsigned *h,
                          const unsigned char *in, size_t insize)
{
    (void)out;
    (void)w;
    (void)h;
    (void)in;
    (void)insize;
    return 78;   /* "invalid PNG" — the device never sees an image */
}

const char *lodepng_error_text(unsigned code)
{
    (void)code;
    return "PNG decoding is not built into picovave (the browser unwraps .p8.png carts)";
}
