/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: the display layer femto8's __DA1470x__ branch renders into.
 *
 * The surface is PICO-8's own screen - GDI_W x GDI_H, native RGB565 - and femto8 writes it 1:1.
 * The 128->240 scale lives here, in gdi_expand_row(), not in the engine and not in three
 * targets: whoever needs a panel-sized picture walks its rows through that one function.
 *
 * Why the surface is not 240x240 itself (it used to be, 115,200 B of .bss): every byte of .bss
 * on this board is a byte the Lua heap does not get (app.ld puts .pool right behind .bss, and
 * alloc.c reads the linker's _pool_start) - see docs/HANDOVER.md 12. 32,768 B of screen instead
 * of 115,200 B is 82,432 B more Lua, which is the difference between a 13 KB cart and a 45 KB
 * one: baba_demake-9 needs an arena of about 400 KB, and the old canvas could not leave it.
 *
 * gdi_display_update_async() stays the frame boundary, and stays where the scale happens:
 *   device   (src/picovave.c)           240-pixel row at a time into lcd_blit(), byte swapped
 *                                       for the ST7789: a whole frame is never in RAM
 *   browser  (femto8/seam/seam_sim.c)   expanded into its own 240x240, which sim_fb() exposes
 *   preview  (host/preview.c)           expanded into its own 240x240 capture, for the PPM
 * so the page, the preview and the ROM still show the same pixels (tools/verify_final.py).
 *
 * The app's own screens (src/picovave_gfx.h) draw into the same surface: the firmware aliases
 * hg_fb onto it, so a boot/crash screen is scaled and pushed by the same path as a cart frame. */
#pragma once
#include <stdint.h>
#include "ble_controller.h"

#define HW_LCDC_LAYER_0   0
#define GDI_FORMAT_RGB565 0

/* femto8's surface: PICO-8's screen, native RGB565. gdi_get_frame_buffer_addr() returns it. */
#define GDI_W 128
#define GDI_H 128

/* What the SMK-37 Pro's panel is: the size every target's output ends up at. */
#define GDI_OUT_W 240
#define GDI_OUT_H 240

void  gdi_set_layer_start(int layer, int x, int y);
void  gdi_set_layer_enable(int layer, int enable);
void  gdi_set_layer_src(int layer, void *src, int w, int h, int fmt);
void *gdi_get_frame_buffer_addr(int layer);
void  gdi_display_update_async(void (*cb)(bool complete, void *user_data), void *user_data);

/* 128 -> 240 nearest neighbour, one destination row per call: sx[] is the 240 source columns
 * (fill it once with gdi_sx_init()), y is the destination row. dst gets GDI_OUT_W pixels. */
static inline void gdi_sx_init(uint8_t *sx)
{
    int x;
    for (x = 0; x < GDI_OUT_W; x++)
        sx[x] = (uint8_t)((x * GDI_W) / GDI_OUT_W);
}

static inline void gdi_expand_row(const uint16_t *src, const uint8_t *sx, int y, uint16_t *dst)
{
    const uint16_t *s = src + ((uint32_t)(y * GDI_H) / GDI_OUT_H) * GDI_W;
    int x;
    for (x = 0; x < GDI_OUT_W; x++)
        dst[x] = s[sx[x]];
}
