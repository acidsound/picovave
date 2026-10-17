/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: the display layer femto8's __DA1470x__ branch renders into.
 * picovave supplies a plain 240x240 RGB565 framebuffer; the device copies it to
 * the ST7789 over SPI1, the simulator copies it to the page's panel. */
#pragma once
#include <stdint.h>
#include "ble_controller.h"

#define HW_LCDC_LAYER_0   0
#define GDI_FORMAT_RGB565 0

void  gdi_set_layer_start(int layer, int x, int y);
void  gdi_set_layer_enable(int layer, int enable);
void  gdi_set_layer_src(int layer, void *src, int w, int h, int fmt);
void *gdi_get_frame_buffer_addr(int layer);
void  gdi_display_update_async(void (*cb)(bool complete, void *user_data), void *user_data);
