/* SPDX-License-Identifier: GPL-3.0-only */
/* seam_device.c - the femto8 platform seam for the M-Vave SMK-37 Pro (AC791N).
 *
 * Mirrors femto8/seam/seam_sim.c: it implements the surface femto8's `__DA1470x__`
 * branch asks for, against the board instead of a page - the RGB565 layer femto8
 * renders into, the allocator, the `gamepad` word p8_input.c reads, and the FreeRTOS
 * shims. The display push itself (gdi_display_update_async) lives in src/picovave.c
 * because the panel driver it needs (platform/firmware/src/lcd.c) is static and must
 * be included exactly once.
 *
 * It is a separate translation unit on purpose: it must NOT see p8_emu.h (which maps
 * malloc onto rh_malloc), so the allocator can call the real malloc. */
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>

#include "fm1_time.h"        /* fm1_ticks / fm1_delay_ms / FM1_TICKS_PER_US */

#include "gdi.h"
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"

uint32_t gamepad;                        /* p8_input.c reads this when !SDL; set in picovave_frame */

/* femto8 renders 240x240 native RGB565 straight into this (gdi_get_frame_buffer_addr).
 * It lives in .bss: 115,200 of the new 136 KiB RAM. The panel wants big endian, so
 * picovave's gdi_display_update_async byte-swaps it in place right before lcd_blit -
 * safe because p8_render() rewrites all 240 rows every frame. */
uint16_t p8_panel_fb[240 * 240];
uint32_t p8_panel_draws;
uint32_t p8_panel_bytes;

/* The femto8 gdi surface, reduced to the one plain layer femto8 asks for. The real
 * board's GDI has no part in this: picovave owns the panel through lcd.c. */
void gdi_set_layer_start(int layer, int x, int y) { (void)layer; (void)x; (void)y; }
void gdi_set_layer_enable(int layer, int enable) { (void)layer; (void)enable; }
void gdi_set_layer_src(int layer, void *src, int w, int h, int fmt)
{
    (void)layer; (void)src; (void)w; (void)h; (void)fmt;
}
void *gdi_get_frame_buffer_addr(int layer) { (void)layer; return p8_panel_fb; }

/* femto8's allocator: p8_emu.h maps malloc/free onto these. Same heap as everyone
 * else's (femto8/stub/device/alloc.c), which is what keeps the engine's boot
 * allocations low and contiguous while Lua grows above them. */
void *rh_malloc(size_t n) { return malloc(n); }
void  rh_free(void *p) { free(p); }

/* ---- the FreeRTOS slice femto8 uses --------------------------------------------
 * One thread and one interrupt, and the only shared thing the interrupt touches is
 * the audio ring index (picovave.c). Nothing here needs a real scheduler, so a
 * binary semaphore is a non-NULL token and a delay is a busy wait - the same shims
 * the simulator uses, with the board's TIMER4 for the clock. */
SemaphoreHandle_t xSemaphoreCreateBinary(void) { static int s; return (SemaphoreHandle_t)&s; }
BaseType_t xSemaphoreGive(SemaphoreHandle_t s) { (void)s; return pdTRUE; }
BaseType_t xSemaphoreTake(SemaphoreHandle_t s, TickType_t ticks) { (void)s; (void)ticks; return pdTRUE; }

/* Milliseconds since boot, from the free-running 24 MHz counter TIMER4. */
TickType_t xTaskGetTickCount(void)
{
    return (TickType_t)(fm1_ticks() / (1000u * FM1_TICKS_PER_US));
}

void vTaskDelay(TickType_t ticks) { fm1_delay_ms((uint32_t)ticks); }

/* p8_lua.c's sleep() (Lua's usleep). A cart that sleeps blocks its own frame. */
int usleep(unsigned int us)
{
    fm1_delay_ms((us + 999u) / 1000u);
    return 0;
}
