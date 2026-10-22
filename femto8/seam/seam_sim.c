/* SPDX-License-Identifier: GPL-3.0-only */
/* seam_sim.c - the femto8 platform seam for the wasm simulator.
 *
 * Implements the surface femto8's `__DA1470x__` branch asks for, against the
 * page's panel and the wasm heap: the RGB565 layer (femto8 renders straight into
 * it), the allocator, the controller bitmask, and the FreeRTOS shims. Nothing here
 * knows about a keyboard, exactly like picovave_sim_hal.h.
 *
 * It is a separate translation unit on purpose: it must NOT see p8_emu.h (which
 * maps malloc onto rh_malloc), so the allocator can call the real malloc. */
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>

#include "gdi.h"
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"

uint32_t gamepad;                        /* p8_input.c reads this when !SDL */

/* femto8 renders PICO-8's own 128x128 screen (gdi.h) into p8_screen_fb. p8_panel_fb is the
 * 240x240 the page shows: sim_fb() exposes it and the page's toRGBA() reads it as a
 * Uint16Array (native, no byte swap), so the expand below is the only difference from a
 * cart frame - the same gdi_expand_row() the board blits row by row. */
uint16_t p8_screen_fb[GDI_W * GDI_H];
uint16_t p8_panel_fb[GDI_OUT_W * GDI_OUT_H];
uint32_t p8_panel_draws;
uint32_t p8_panel_bytes;
static uint8_t p8_sx[GDI_OUT_W];
static int p8_sx_ready;

void gdi_set_layer_start(int layer, int x, int y) { (void)layer; (void)x; (void)y; }
void gdi_set_layer_enable(int layer, int enable) { (void)layer; (void)enable; }
void gdi_set_layer_src(int layer, void *src, int w, int h, int fmt)
{
    (void)layer; (void)src; (void)w; (void)h; (void)fmt;
}
void *gdi_get_frame_buffer_addr(int layer) { (void)layer; return p8_screen_fb; }

void gdi_display_update_async(void (*cb)(bool complete, void *user_data), void *user_data)
{
    int y;
    if (!p8_sx_ready) {
        gdi_sx_init(p8_sx);
        p8_sx_ready = 1;
    }
    for (y = 0; y < GDI_OUT_H; y++)
        gdi_expand_row(p8_screen_fb, p8_sx, y, p8_panel_fb + (uint32_t)y * GDI_OUT_W);
    p8_panel_draws++;
    p8_panel_bytes += (uint32_t)GDI_OUT_W * GDI_OUT_H * 2u;
    if (cb)
        cb(true, user_data);
}

/* ---- allocator (wasm heap) ------------------------------------------------- */
void *rh_malloc(size_t n) { return malloc(n); }
void  rh_free(void *p) { free(p); }

/* ---- FreeRTOS shims -------------------------------------------------------- */
static int sim_sem;

SemaphoreHandle_t xSemaphoreCreateBinary(void) { sim_sem = 1; return &sim_sem; }
BaseType_t xSemaphoreGive(SemaphoreHandle_t s) { (void)s; sim_sem = 1; return pdTRUE; }
BaseType_t xSemaphoreTake(SemaphoreHandle_t s, TickType_t ticks)
{
    (void)s; (void)ticks; sim_sem = 0; return pdTRUE;
}

/* A monotonic ms tick, one per call: the page owns the real clock, and femto8 only
 * uses this for its fps readout. */
TickType_t xTaskGetTickCount(void) { static TickType_t t; return t++; }
void vTaskDelay(TickType_t ticks) { (void)ticks; }
void p8_target_wdt_feed(void) { }
int p8_device_dispatch_render(void) { return 0; }
void p8_device_wait_render(void) { }
