/* SPDX-License-Identifier: GPL-3.0-only */
/* seam_host.c - the femto8 platform seam for the host preview.
 *
 * The third seam, beside femto8/seam/seam_sim.c (the browser) and
 * femto8/seam/seam_device.c (the board): the same surface, which is the point - the
 * preview renders the engine's own pixels, so it can only agree with the wasm and the
 * ROM if all three run the same code. Here the allocator is the host's malloc and the
 * clock is a counter this file owns, so a preview run is fast and deterministic.
 *
 * gdi_display_update_async is deliberately NOT here: preview.c defines it, because that
 * is where the panel push and the capture live. src/picovave.c does the same on the
 * device, and both swap the framebuffer in place the same way.
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

/* femto8 renders 240x240 native RGB565 straight into this. preview.c reads it. */
uint16_t p8_panel_fb[240 * 240];
uint32_t p8_panel_draws;
uint32_t p8_panel_bytes;

void gdi_set_layer_start(int layer, int x, int y) { (void)layer; (void)x; (void)y; }
void gdi_set_layer_enable(int layer, int enable) { (void)layer; (void)enable; }
void gdi_set_layer_src(int layer, void *src, int w, int h, int fmt)
{
    (void)layer; (void)src; (void)w; (void)h; (void)fmt;
}
void *gdi_get_frame_buffer_addr(int layer) { (void)layer; return p8_panel_fb; }

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
