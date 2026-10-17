/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave_sim_hal.h - the simulated SMK-37 Pro panel.
 *
 * Implements exactly the hh_* surface that picovave_p8.h uses on the device,
 * backed by a 240x240 RGB565 array instead of SPI1 and an ST7789V. Nothing in here knows
 * about a keyboard, so the shared code cannot accidentally depend on the simulation.
 *
 * The pixel convention matches the panel: big endian RGB565, so hg_fb is copied verbatim.
 * (The WASM page converts to RGBA when it paints; the Node test writes a PPM.)
 */
#pragma once
#include <stdint.h>
#include "../src/picovave_input.h"

/* Device state, kept under dist' names so the exported accessors (sim_ms(), sim_bytes(),
 * sim_draws(), sim_wdt()) can use the natural public names without colliding here. */
static uint16_t sim_fb_buf[240 * 240];
static uint32_t sim_dev_ms;             /* the simulated device clock */
static uint32_t sim_dev_pushes;         /* hh_blit() calls */
static uint32_t sim_dev_bytes;          /* bytes handed to the panel */
static uint32_t sim_dev_wdt;            /* watchdog feeds */

/* The panel the page feeds through sim_button()/sim_key()/sim_enc()/sim_pad()/sim_fader()/
 * sim_wheel(). The shared app reads it exactly as it reads the real matrix + ADC. */
static uint32_t sim_in_buttons;
static uint64_t sim_in_keys;
static int8_t   sim_in_enc[HIN_NENC];
static uint16_t sim_in_pads;
static uint8_t  sim_in_pad_vel[HIN_NPAD];
static uint8_t  sim_in_fader[HIN_NFADER];
static uint16_t sim_in_wheel[HIN_NWHEEL];

static void hh_time_init(void) { sim_dev_ms = 0; }
static void hh_delay_ms(uint32_t ms) { sim_dev_ms += ms; }   /* instant: the page advances time */
static void hh_wdt_arm(uint32_t code) { (void)code; }
static void hh_wdt_feed(void) { sim_dev_wdt++; }
static void hh_lcd_init(void) { sim_dev_pushes = sim_dev_bytes = sim_dev_wdt = 0; }

static void hh_input_init(void)
{
    uint32_t i;
    sim_in_buttons = 0;
    sim_in_keys = 0;
    for (i = 0; i < HIN_NENC; i++)
        sim_in_enc[i] = 0;
    sim_in_pads = 0;
    for (i = 0; i < HIN_NPAD; i++)
        sim_in_pad_vel[i] = 0;
    for (i = 0; i < HIN_NFADER; i++)
        sim_in_fader[i] = 0;
    for (i = 0; i < HIN_NWHEEL; i++)
        sim_in_wheel[i] = 0;
}

static void hh_input_read(hin_t *in)
{
    uint32_t i;
    in->buttons = sim_in_buttons;
    in->keys = sim_in_keys;
    for (i = 0; i < HIN_NENC; i++) {
        in->enc[i] = sim_in_enc[i];
        sim_in_enc[i] = 0;                  /* detents are consumed on read, as the real HAL does */
    }
    in->pads = sim_in_pads;
    for (i = 0; i < HIN_NPAD; i++)
        in->pad_vel[i] = sim_in_pad_vel[i];
    for (i = 0; i < HIN_NFADER; i++)
        in->fader[i] = sim_in_fader[i];
    for (i = 0; i < HIN_NWHEEL; i++)
        in->wheel[i] = sim_in_wheel[i];
}

static void hh_blit(uint32_t x, uint32_t y, uint32_t w, uint32_t h, const uint16_t *px)
{
    uint32_t i, j;
    for (j = 0; j < h; j++)
        for (i = 0; i < w; i++)
            if (x + i < 240u && y + j < 240u) {
                uint16_t c = px[j * w + i];
                sim_fb_buf[(y + j) * 240u + x + i] = (uint16_t)((c >> 8) | (c << 8));  /* visible RGB565 */
            }
    sim_dev_bytes += w * h * 2u;
    sim_dev_pushes++;
}
