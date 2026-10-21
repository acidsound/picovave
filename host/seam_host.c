/* SPDX-License-Identifier: GPL-3.0-only */
/* seam_host.c - the femto8 platform seam for the host preview.
 *
 * The third seam, beside femto8/seam/seam_sim.c (the browser) and
 * femto8/seam/seam_device.c (the board): the same surface, which is the point - the
 * preview renders the engine's own pixels, so it can only agree with the wasm and the
 * ROM if all three run the same code. Here the allocator is the host's malloc and the
 * clock is a counter this file owns, so a preview run is fast and deterministic.
 *
 * gdi_display_update_async is here, as in seam_sim.c: it expands the engine's 128x128 screen
 * into a 240x240 buffer (gdi.h), which is what the preview captures, so the capture is the
 * panel's picture by construction rather than by a second implementation.
 *
 * It is a separate translation unit on purpose: it must NOT see p8_emu.h, which maps
 * malloc onto rh_malloc - the allocator has to call the real malloc. */
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>

#include "gdi.h"
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"

uint32_t gamepad;                        /* p8_input.c reads this when !SDL */

/* p8_emu.c's help()/version dialog prints this; the device defines it in its syscalls. */
const char *femto8_version = "femto8 (picovave preview)";

/* The engine's 128x128 screen, and the 240x240 the preview captures (both native RGB565). */
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

/* femto8's allocator: p8_emu.h maps malloc/free onto these. */
void *rh_malloc(size_t n) { return malloc(n); }
void  rh_free(void *p) { free(p); }

/* ---- the FreeRTOS slice femto8 uses ----------------------------------------
 * Nothing here is concurrent, so a binary semaphore is a token and a delay only moves
 * this file's tick. That matters: a preview must produce the same screen regardless of
 * how fast the machine is, and p8_step() paces itself through vTaskDelay(). */
static int host_sem;
static TickType_t host_ticks;

SemaphoreHandle_t xSemaphoreCreateBinary(void) { host_sem = 1; return (SemaphoreHandle_t)&host_sem; }
BaseType_t xSemaphoreGive(SemaphoreHandle_t s) { (void)s; host_sem = 1; return pdTRUE; }
BaseType_t xSemaphoreTake(SemaphoreHandle_t s, TickType_t ticks)
{
    (void)s; (void)ticks; host_sem = 0; return pdTRUE;
}

TickType_t xTaskGetTickCount(void) { return host_ticks; }
void vTaskDelay(TickType_t ticks) { host_ticks += ticks; }
void p8_target_wdt_feed(void) { }
