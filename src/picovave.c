/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave.c - the device build of picovave: run a PICO-8 cart on the M-Vave SMK-37 Pro (AC791N).
 *
 * This file is glue only. Everything the device DOES lives in the shared app layer
 * (src/picovave_p8.h) which the browser simulator (sim/sim.c) includes unchanged, over
 * the vendored femto8 engine (femto8/). What is device-specific here is:
 *
 *   - the hh_* HAL (time/panel/inputs/cart) forwarded to the vendored board layer
 *   - gdi_display_update_async: the one femto8 display hook, panel push included
 *   - the ALNK0 audio path: the mixer is advanced by the main loop into a ring, the ISR
 *     only copies a half out of it
 *   - USB-MIDI + M-UPGRADE (vendored) and the SysEx .p8 cart upload (shared protocol,
 *     src/picovave_cart.h, over the SLOOP store in flash)
 *   - the SPL entry point, the crash screen and the two rescue paths
 *
 * picovave is SELF-CONTAINED: it builds against its own vendored copy of the hardware layer
 * and USB/OTA stack in picovave/platform/ (GPL-3.0, Leo Kuroshita / Hugelton Instruments),
 * taken from Makkuro37 - the firmware proven to boot, read the SMK-37 Pro and update over
 * USB-MIDI on real hardware. picovave never reads repo/. Under picovave/platform/:
 *
 *   firmware/hal/fm1_time.h    TIMER4 time base
 *   firmware/hal/fm1_sys.h     P33 watchdog, reset, UBOOT / update entry
 *   firmware/hal/fm1_irq.h     interrupt / exception vectors (fm1_vec.S, fm1_isr.S)
 *   firmware/hal/fm1_timer.h   TIMER5: the 10 kHz tick (matrix scan + USB poll + ms count)
 *   firmware/hal/fm1_audio.h   ALNK0 I2S to the codec
 *   firmware/hal/fm1_lcd_hw.h  SPI1 pin groups (SMK-37: PB6 CLK / PB7 DO / PC8 CS / PB5 D/C)
 *   firmware/src/lcd.c         the ST7789 panel: the stock v016 init table + DMA blit
 *   firmware/hal/smk_input.h   the 74HC595 column scan: 18 buttons, 37 keys, 8 encoders
 *   firmware/src/usb.c         USB-MIDI device + M-UPGRADE sysEx transport
 *   firmware/src/ota.c         M-UPGRADE: handshake, stage the loader, commit + reset
 *   firmware/hal/fm1_flash.h   the RAM flash driver (the SLOOP store is cells 0x9D000..0xBB000)
 *
 * (Makkuro37's freestanding firmware/src/libc.c is NOT included here: this build links the
 * toolchain's libc.a, which supplies memset/memcpy/memcmp/str*, so vendoring them again would
 * only collide with newlib's prototypes.)
 *
 * The device is reachable and re-flashable like Makkuro37 is:
 *   - it enumerates as a USB-MIDI device (usb_start), polled from the TIMER5 ISR
 *   - it answers M-UPGRADE (ota_service / ota_session), so FLASH(USB) can overwrite it
 *   - a host uploads a .p8 over SysEx (F0 22 24 35 7E ..) and it runs on reboot
 *   - it can drop to the mask-ROM UBOOT: OCT- + OCT+ held 5 s, the USB SysEx soft key
 *     (F0 22 24 35 7D F7), and after two failed boots (bootguard) automatically
 *
 * The screen surface (p8_screen_fb: PICO-8's own 128x128, which this file draws its own screens
 * into and scales) and the allocator are the femto8 seam
 * (femto8/seam/seam_device.c, femto8/stub/device/): this file only pushes pixels. */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

/* ---- picovave's vendored hardware layer (picovave/platform/firmware/hal) -------------------- */
#include "fm1_cc.h"
#include "fm1_time.h"
#include "fm1_sys.h"          /* fm1_wdt_arm / fm1_wdt_feed / fm1_enter_uboot / fm1_enter_update */
#include "fm1_irq.h"          /* fm1_irq_init / fm1_irq_attach (fm1_vec.S, fm1_isr.S) */
#include "fm1_guard.h"        /* stack / write / bus / PC guards + the top-window lock */
#include "smk_input.h"        /* fm1_input_init / fm1_input_tick / fm1_enc_take / fm1_in */
#include "fm1_timer.h"        /* fm1_timer5_start: the 10 kHz tick */
#include "fm1_audio.h"        /* fm1_audio_init / fm1_audio_pending, the ALNK0 half buffers */
#include "fm1_lcd_hw.h"       /* SPI1 pins and registers that lcd.c builds on */
#include "fm1_flash.h"        /* the RAM flash driver, FL_XIP, FL_DATA_LO/HI, FL_JEDEC_OK */
#include "lcd.c"              /* lcd_init / lcd_blit, the stock ST7789 v016 sequence */

/* ---- the shared screens (OTA / rescue / crash) ------------------------------------------
 * picovave_gfx.h draws into hg_fb; aliasing it onto the engine's own screen (seam_device.c,
 * gdi.h: 128x128 native RGB565, GDI_W x GDI_H) means the boot, crash, update and rescue
 * screens cost no RAM of their own - and that a 240x240 frame never exists in RAM at all,
 * only one row on its way to lcd_blit(). The helpers store native RGB565, like femto8's. */
#include "picovave_font.h"
#include "picovave_input.h"
#define hg_fb p8_screen_fb
#include "picovave_gfx.h"
#include "gdi.h"              /* gdi_expand_row, gdi_display_update_async's own prototype */

extern uint16_t p8_screen_fb[GDI_W * GDI_H];       /* femto8/seam/seam_device.c */
extern uint32_t p8_panel_draws, p8_panel_bytes;    /* ...and this file */

static uint8_t panel_up;                 /* lcd_init() ran: the panel, and so a crash screen, is safe */

/* One panel frame, 240 rows at a time: expand a row of the engine's screen (or of a screen
 * picovave drew into it), byte-swap it for the ST7789 and stream it into the one address window
 * this frame opened. The row is the only frame-sized buffer in the app: 480 B, against the
 * 115,200 B of .bss a 240x240 canvas used to cost - and .bss is Lua heap here (app.ld puts .pool
 * right behind it). */
static uint16_t pv_row[GDI_OUT_W];
static uint8_t pv_row_sx[GDI_OUT_W];        /* pv_sx is the SysEx buffer further down */
static uint8_t pv_row_sx_ready;

static void pv_push(void)
{
    int y, x;
    if (!pv_row_sx_ready) {
        gdi_sx_init(pv_row_sx);
        pv_row_sx_ready = 1;
    }
    lcd_stream_begin(0u, 0u, GDI_OUT_W, GDI_OUT_H);
    for (y = 0; y < GDI_OUT_H; y++) {
        lcd_stream_wait();              /* the previous row is still on the wire */
        gdi_expand_row(p8_screen_fb, pv_row_sx, y, pv_row);
        for (x = 0; x < GDI_OUT_W; x++) {
            uint16_t v = pv_row[x];
            pv_row[x] = (uint16_t)((v >> 8) | (v << 8));
        }
        lcd_stream_row(pv_row, GDI_OUT_W);
    }
    lcd_stream_end();
    p8_panel_draws++;
    p8_panel_bytes += (uint32_t)GDI_OUT_W * GDI_OUT_H * 2u;
}

/* Guarded: the USB rescue runs headless (no lcd_init), and ota_show() still calls this - the
 * installer is the display there, so a blit into an unconfigured panel must be a no-op, not SPI
 * traffic into a controller nobody has brought up. */
static void pv_blit(void) { if (panel_up) pv_push(); }

/* ---- the stack, measured ---------------------------------------------------------------
 * The guard's write window under _ustack_lo catches a store off the bottom of the user stack,
 * and the fault screen names the instruction that did it - but with 24,320 B of stack the
 * interesting question is how deep it really goes. Painting the unused stack at boot and
 * scanning it later answers that on the panel: at boot it reports what the previous run used
 * (also when that run was perfectly healthy), and on a fault it reports the high-water mark
 * next to the stack pointer, which separates a cart that recurses too far from a stack pointer
 * that left both windows altogether (a longjmp into a dead frame, or setjmp's own prologue:
 * see pv_sp_bad). 0.._ustack_lo is the guard band between the stack and the Lua heap: never
 * written here. */
/* Which stack the app itself was on, and how deep. Only f[24] (the HANDLER's sp, pushed by the
 * vector stub) can be trusted - and it always reads "just under a stack top", so it cannot tell a
 * deep stack from a shallow one. f[22]/f[23] are NOT stack pointers on this silicon: f[22] holds
 * the value setjmp() had just put in sp (see fm1_guard_enable's comment). The paint scan below and
 * the ISR's pv_sp_min are what answer "how deep", which is why they exist. */
/* Which stack region an address falls in: 'U' user, 'S' supervisor, '?' neither. */
static int pv_stack_ok(uint32_t a)
{
    if (a >= (uint32_t)(uintptr_t)_ustack_lo && a <= (uint32_t)(uintptr_t)_ustack_top)
        return 1;
    if (a >= (uint32_t)(uintptr_t)_sstack_lo && a <= (uint32_t)(uintptr_t)_sstack_top)
        return 1;
    return 0;
}

static char pv_stack_of(uint32_t a)
{
    if (a >= (uint32_t)(uintptr_t)_ustack_lo && a <= (uint32_t)(uintptr_t)_ustack_top)
        return 'U';
    if (a >= (uint32_t)(uintptr_t)_sstack_lo && a <= (uint32_t)(uintptr_t)_sstack_top)
        return 'S';
    return '?';
}

/* f[24] sp is the frame slot that behaved like a stack pointer in the field, so that is what the
 * SP band reports and the verdict compares; f[22]/f[23] are printed raw beside it because on this
 * silicon they hold values that look like data (0x01C0EC3C is .bss, not a stack). */
static uint32_t pv_app_sp(const fm1_crash_t *c, char *tag, uint32_t *lo, uint32_t *hi)
{
    char t = pv_stack_of(c->sp);

    if (t == 'S') {
        *lo = (uint32_t)(uintptr_t)_sstack_lo;
        *hi = (uint32_t)(uintptr_t)_sstack_top;
    } else {
        *lo = (uint32_t)(uintptr_t)_ustack_lo;
        *hi = (uint32_t)(uintptr_t)_ustack_top;
    }
    *tag = t;
    return c->sp;
}

#define PV_SP_MIN_NONE 0xFFFFFFFFu
/* The deepest stack address this app has ever reached, sampled from the 10 kHz tick (see
 * fm1_timer5_irq). The frame cannot say which stack the main loop runs on - f[24] is the handler's
 * and f[22]/f[23] hold data-looking values - but the ISR body runs on that same stack, so the
 * address of one of its locals tracks it. .noinit: the boot after a fault still reports it, and it
 * is reset just before the tick starts so the number always belongs to the run that died. */
static uint32_t pv_sp_min __attribute__((section(".noinit")));
/* The first tick whose interrupted code was NOT on one of our two stacks, with the pc it was
 * interrupted at. Two things can put sp outside both windows: a longjmp() into a dead frame (see
 * the restart notes in picovave_p8.h), and setjmp() itself, which points sp at the jmp_buf for the
 * few instructions of its prologue (see the comment at fm1_guard_enable in picovave.c) - a 10 kHz
 * sample landing inside those is unlikely but not impossible, so read this together with the pc. */
static uint32_t pv_sp_bad __attribute__((section(".noinit")));
static uint32_t pv_sp_bad_pc __attribute__((section(".noinit")));

#define PV_STACK_PAT 0xA5A5A5A5u
static uint32_t pv_stack_prev;                 /* bytes of stack the previous boot touched */

static uint32_t pv_stack_depth(void)
{
    const uint32_t *p = (const uint32_t *)(const void *)_ustack_lo;
    const uint32_t *top = (const uint32_t *)(const void *)_ustack_top;
    if (p >= top || *p != PV_STACK_PAT)
        return 0u;                             /* never painted (power-on): nothing to report */
    while (p < top && *p == PV_STACK_PAT)
        p++;
    return (uint32_t)((uintptr_t)top - (uintptr_t)p);
}

static void pv_stack_paint(void)
{
    volatile uint32_t here = 0;                /* this frame: never paint at or above it */
    uint32_t *p = (uint32_t *)(void *)_ustack_lo;
    uint32_t *end = (uint32_t *)(void *)(((uintptr_t)&here & ~(uintptr_t)7u) - 256u);
    while (p < end)
        *p++ = PV_STACK_PAT;
}

/* ------------------------------------------------------------- the hh_* HAL --- */
static void hh_time_init(void) { fm1_time_init(); }
static void hh_lcd_init(void) { lcd_init(); panel_up = 1; }

#ifndef PV_BUILD_TAG
#define PV_BUILD_TAG "no-tag"
#endif

/* Painted from fm1_cstart before the stack scan and before the boot stages: if the panel still
 * shows the previous build's crash screen after a flash, the new image did not run at all. */
static void pv_early_mark(void)
{
    hh_lcd_init();
    hg_clear(HG_BLACK);
    hg_text_center(20, "PICOVAVE", 2, HG_GREEN);
    hg_text_center(44, PV_BUILD_TAG, 1, HG_WHITE);
    hg_text_center(62, "BOOT MARK: NEW IMAGE", 1, HG_DIM);
    pv_blit();
}
static void hh_input_init(void) { fm1_input_init(); }

/* Boot-stage breadcrumb. The fault screen needs panel_up and a watchdog reset prints nothing at
 * all, so a device that dies on the way up used to be a black panel and no information. Drawing
 * the stage reached costs one screen (into the panel buffer the engine renders into anyway): the
 * panel then shows the LAST stage until the first real frame replaces it. 1 = time/LCD/input,
 * 2 = engine init, 3 = cart decoded, 4 = cart started, 5 = first frame done. */
/* The fault screen is up for three seconds before the reset, which is not long enough to copy
 * anything down - and the numbers it shows (the PC, the guard bits, the painted high-water mark)
 * are what tells a deep cart apart from an sp that left the stacks. The record lives in .noinit
 * and survives the reset, so the boot AFTER the crash draws the same picture and holds it: read
 * it, photograph it, or hold any button to skip. Shown once (the magic is cleared), and 10 s is
 * well inside the 30 s the bootguard needs to call a boot healthy, so the report cannot be
 * mistaken for a failed boot. */
#define PV_REPORT_MS 10000u
static void pv_crash_report(void)
{
    static hg_crash_t h;              /* static: see hg_crash_screen - the stack is not trusted */
    uint32_t t0;
    if (!panel_up || fm1_crash.magic != FM1_CRASH_MAGIC || fm1_crash.count == 0u)
        return;
    memset(&h, 0, sizeof h);
    h.boot_report = 1u;
    h.pc = fm1_crash.pc;
    h.vec = fm1_crash.vec;
    h.emu = fm1_crash.emu;
    h.dbg = fm1_crash.dbg;
    h.sp = pv_app_sp(&fm1_crash, &h.sp_tag, &h.stack_lo, &h.stack_top);
    h.usp = fm1_crash.usp;
    h.ssp = fm1_crash.ssp;
    h.sp_min = pv_sp_min;                            /* the previous run's, still in .noinit */
    h.sp_min_tag = pv_stack_of(pv_sp_min);
    h.sp_bad = pv_sp_bad;
    h.sp_bad_pc = pv_sp_bad_pc;
    h.rets = fm1_crash.rets;
    h.etm0 = fm1_crash.etm[0];
    h.etm1 = fm1_crash.etm[1];
    h.count = fm1_crash.count;
    h.early = fm1_crash.early;
    h.uptime_ms = fm1_crash.uptime_ms;
    h.stack_used = pv_stack_prev;     /* the crashed run's high-water mark, scanned before repaint */
    hg_crash_screen(&h);
    pv_blit();
    t0 = fm1_ticks();                 /* TIMER5 is not running yet: the same clock the fault uses */
    while ((uint32_t)(fm1_ticks() - t0) < PV_REPORT_MS * 1000u * FM1_TICKS_PER_US) {
        fm1_wdt_feed();
        if (fm1_in.buttons)           /* scanned before the panel came up: hold one to skip */
            break;
    }
    fm1_crash.magic = 0u;             /* reported once: later boots are quiet */
}

static void hh_boot_stage(int stage)
{
    static char b[2];
    static char s[28];
    char *p;
    if (!panel_up)
        return;
    b[0] = (char)('0' + (stage % 10));
    b[1] = 0;
    hg_clear(HG_BLACK);
    hg_text_center(10, "PICOVAVE BOOT", 1, HG_GREEN);
    hg_text_center(30, b, 4, HG_WHITE);
    /* the stack the run before this one used, so a healthy boot still shows the margin */
    p = hg_put(s, "STACK ", pv_stack_prev, 4);
    p = hg_put(p, "/", (uint32_t)((uintptr_t)_ustack_top - (uintptr_t)_ustack_lo), 4);
    *p = 0;
    hg_text_center(78, s, 1, HG_DIM);
    pv_blit();
    if (stage == 1)
        pv_crash_report();            /* if the last boot died, this stays on the panel instead */
}

static void hh_input_read(hin_t *in)
{
    uint32_t i;
    /* TIMER5 runs Makkuro37's fm1_input_tick() at 10 kHz, so this only reads its debounced
     * state. Scanning again here would fight the ISR for the 595 latch and the ADC. */
    in->buttons = fm1_in.buttons;             /* bit i = button i held (already debounced) */
    in->keys = fm1_in.keys;                   /* bit k = keybed key k held */
    for (i = 0; i < HIN_NENC; i++)
        in->enc[i] = (int8_t)fm1_enc_take(i); /* detents since the previous read, consumed here */
    in->pads = 0;                             /* the 16 pads: pressure > 0 means the pad is down */
    for (i = 0; i < HIN_NPAD; i++) {
        in->pad_vel[i] = fm1_in.pad_press[i];
        if (fm1_in.pad_press[i])
            in->pads |= (uint16_t)(1u << i);
    }
    for (i = 0; i < HIN_NFADER; i++)          /* the four faders, 0..127 (Makkuro37's ADC mux) */
        in->fader[i] = fm1_in.fader[i];
    for (i = 0; i < HIN_NWHEEL; i++)          /* pitch and mod wheels, raw (10-bit) */
        in->wheel[i] = fm1_in.wheel[i];
}

/* ------------------------------------------------ the one femto8 display hook ---
 * femto8 has just written its 128x128 screen (seam_device.c, gdi.h) and this is the frame
 * boundary: pv_push() scales and pushes it one row at a time. Same code the boot, crash and
 * rescue screens go through, so what the panel shows and what the host preview captures are
 * produced by one routine (tools/verify_final.py checks that they agree). */
void gdi_display_update_async(void (*cb)(bool complete, void *user_data), void *user_data)
{
    pv_push();
    if (cb)
        cb(true, user_data);
}

/* ------------------------------------------------------------ the mixer (ALNK0) ---
 * femto8's render_sounds() reads the channels, the music state and the PCM ring - the same
 * state Lua writes while the cart runs. Rendering it from the ISR would race the main loop,
 * so the main loop renders into a ring (pv_audio_fill) and the ISR only copies. 8 halves is
 * 46 ms at 44.1 kHz: comfortably more than the 33 ms a frame takes.
 *
 * 24-bit left justified, both channels equal (mono -> stereo), like Makkuro37's audio.c. */
extern void render_sounds(int16_t *buffer, int total_samples);

#define PV_AUD_HALF 256u                 /* mono frames a half: 5.8 ms */
#define PV_AUD_WORDS (PV_AUD_HALF * 2u)  /* int32 words a half (L,R interleaved) */
#define PV_AUD_RING 8u                   /* halves the main loop keeps queued */

static int32_t pv_abuf[2u * PV_AUD_WORDS] __attribute__((aligned(4)));
static int16_t pv_ring[PV_AUD_RING][PV_AUD_HALF];
static volatile uint32_t pv_ring_w, pv_ring_r;

extern void isr_alnk0(void);             /* hal/fm1_isr.S wrapper */
void fm1_alnk0_irq(void)
{
    uint8_t p = fm1_audio_pending();
    fm1_audio_ack_aux(p);
    if (p & FM1_AUDIO_HALF) {
        uint32_t half = fm1_audio_free_half(), i;
        int32_t *o = &pv_abuf[half * PV_AUD_WORDS];
        if (pv_ring_r != pv_ring_w) {
            const int16_t *s = pv_ring[pv_ring_r % PV_AUD_RING];
            pv_ring_r++;
            for (i = 0; i < PV_AUD_HALF; i++) {
                int32_t v = (int32_t)s[i] << 8;
                o[2u * i] = v;
                o[2u * i + 1u] = v;
            }
        } else {
            for (i = 0; i < PV_AUD_WORDS; i++)
                o[i] = 0;               /* underrun: silence, never stale audio */
        }
        fm1_audio_ack_half();
    }
}

/* Top the ring up ahead of the DMA. Runs in the main loop only, so the engine's audio state
 * has exactly one writer. */
static void pv_audio_fill(void)
{
    while (pv_ring_w - pv_ring_r < PV_AUD_RING) {
        render_sounds(pv_ring[pv_ring_w % PV_AUD_RING], (int)PV_AUD_HALF);
        pv_ring_w++;
    }
}

static void pv_audio_init(void)
{
    uint32_t i;
    for (i = 0; i < sizeof pv_abuf / sizeof pv_abuf[0]; i++)
        pv_abuf[i] = 0;                 /* the DMA loops it before the first ISR */
    pv_ring_w = pv_ring_r = 0;
    fm1_audio_init(pv_abuf, PV_AUD_WORDS, isr_alnk0, 3);   /* below TIMER5 (4) */
}

/* -------------------------------------------------- USB-MIDI + M-UPGRADE ---
 * the vendored stack (picovave/platform/firmware/src), so the device enumerates and can be
 * re-flashed. No CDC console, no USB audio, the full M-UPGRADE updater on. */
#define FELUCCA_OTA 1
#define FELUCCA_OTA_DRYRUN 0
#ifndef FELUCCA_ID
#define FELUCCA_ID "SMK-37 Pro_700"           /* package identity (build_picovave.py) */
#endif
#define FELUCCA_CDC 0
#define FELUCCA_UAC 0
#define FELUCCA_FLASH 1
#define RING_PUBLISH() __asm__ volatile("" ::: "memory")   /* slot store before the index update */
static volatile uint32_t fm1_ms;              /* milliseconds since boot (TIMER5 ISR) */
#include "bootguard.h"
bootguard_t picovave_bootguard __attribute__((section(".noinit")));

#include "usb.c"

static uint8_t flash_ok;                      /* JEDEC id matched at boot */

static int st_read(uint32_t off, void *dst, uint32_t n)   /* 256-byte IRQ-off windows */
{
    uint8_t *d = dst;
    while (n) {
        uint32_t k = n > 256u ? 256u : n, f = irq_save();
        int rc = FL_FAR(fl_read_ram)(off, d, k);
        irq_restore(f);
        if (rc)
            return rc;
        off += k;
        d += k;
        n -= k;
    }
    return 0;
}

static void pv_audio_silence(void)            /* IRQs off: the DMA would loop stale audio (buzz) */
{
    uint32_t i;
    for (i = 0; i < sizeof pv_abuf / sizeof pv_abuf[0]; i++)
        pv_abuf[i] = 0;
}

/* a sector erase with the audio silenced in the same interrupts-off window */
static int fl_erase4k_quiet(uint32_t off, uint32_t *took)
{
    uint32_t f = irq_save();
    int rc;
    pv_audio_silence();
    rc = FL_FAR(fl_erase4k_ram)(off, took);
    irq_restore(f);
    return rc;
}

#include "ota.c"

/* the M-UPGRADE hooks (ota.c's forward declarations bind to these) */
static uint32_t ota_now_ms(void) { return fm1_ms; }
static void ota_idle(void) { fm1_wdt_feed(); }
static int ota_in_area(uint32_t off, uint32_t n) { return FL_IN(off, n, OTA_AREA, OTA_AREA + OTA_AREA_LEN); }
static int ota_erase(uint32_t off)
{
    uint32_t took;
    if (!ota_in_area(off, 0x1000u) || (off & 0xFFFu))
        return -8;
    return fl_erase4k_quiet(off, &took);
}
static int ota_prog(uint32_t off, const void *p, uint32_t n)
{
    if (!ota_in_area(off, n))
        return -8;
    return fl_write(off, p, n);
}
static int ota_fread(uint32_t off, void *p, uint32_t n) { return st_read(off, p, n); }
static void ota_show(uint32_t step, int32_t code)
{
    static const char *const STEP[] = {"", "PACKAGE", "CHECK HEAD", "LOADER", "CONFIRM", "UPDATING..."};
    char b[24];
    uint32_t k = 0, v;
    hg_clear(HG_BLACK);
    hg_text_center(20, "UPDATE", 2, HG_WHITE);
    if (step < 9u) {
        hg_text_center(48, STEP[step < 6u ? step : 0], 1, HG_GREEN);
        pv_blit();
        return;
    }
    if (code == 1) {
        const char *t = "DRY RUN OK";
        while (t[k]) { b[k] = t[k]; k++; }
        b[k] = 0;
    } else {
        const char *t = "FAILED  -";
        v = (uint32_t)-code;
        while (t[k]) { b[k] = t[k]; k++; }
        if (v >= 10u)
            b[k++] = (char)('0' + v / 10u);
        b[k++] = (char)('0' + v % 10u);
        b[k] = 0;
    }
    hg_text_center(48, b, 1, HG_GREEN);
    pv_blit();
    fm1_delay_ms(1500);
}
static void ota_commit(const uint8_t *parm)
{
    bootguard_clear(&picovave_bootguard);                 /* intentional update reset */
    usb_detach();
    fm1_delay_ms(30);
    fm1_enter_update(parm);                            /* record into RAM, core reset (fm1_sys.h) */
}

/* ---------------------------------------------------- the cart store (SLOOP + SysEx) ---
 * src/picovave_cart.h owns the layout, the framing, the 7-bit fields, the state machine and
 * the CRC; the device supplies three flat flash operations and the reply path. The region is
 * the SLOOP store (0x9D000..0xBB000, 120 KiB), which a plain firmware update never touches, so
 * an uploaded cart survives reflashing. Reads go through XIP (fl_plain_window_init once at
 * boot), writes and erases through the RAM flash driver. */
#include "picovave_cart.h"
#include "cart_default.h"          /* built by tools/build_picovave.py: the cart on a blank device */

/* ---------------------------------------------------------------- the app ---
 * picovave_p8.h needs hh_input_read/hh_input_init/hh_lcd_init/hh_time_init defined above. */
#include "picovave_p8.h"

int hh_store_read(uint32_t off, void *dst, uint32_t n)
{
    if (off > PV_STORE_SIZE || n > PV_STORE_SIZE - off)
        return -1;
    memcpy(dst, (const void *)FL_XIP(FL_DATA_LO + off), n);
    return 0;
}

int hh_store_erase(uint32_t off, uint32_t n)
{
    uint32_t a;
    if ((off & (PV_SECTOR - 1u)) || off > PV_STORE_SIZE || n > PV_STORE_SIZE - off)
        return -1;
    for (a = 0; a < n; a += PV_SECTOR) {
        uint32_t took;
        fm1_wdt_feed();                    /* a 15-sector slot erase is not instantaneous */
        if (fl_erase4k_quiet(FL_DATA_LO + off + a, &took) != 0)
            return -1;
    }
    return 0;
}

int hh_store_write(uint32_t off, const void *src, uint32_t n)
{
    if (off > PV_STORE_SIZE || n > PV_STORE_SIZE - off)
        return -1;
    return fl_write(FL_DATA_LO + off, src, n);
}

void hh_cart_reply(const uint8_t *msg, uint32_t n) { ota_wire_send(msg, n); }

/* The cooked cart to run: the one the rollback ladder currently offers (pv_run_slot) - the
 * active slot's payload PAST its 4-byte wire magic (pv_hdr_read has already checked the magic and
 * the length), or the embedded default cart, which was never on the wire and so is a bare cooked
 * cart. p8_load_cooked() re-checks the size; the slot's payload lives in memory-mapped flash, and
 * the loader only reads it. */
const uint8_t *hh_cart_data(void)
{
    if (pv_run_slot >= 0) {
        uint32_t off = PV_SLOT_BASE((uint8_t)pv_run_slot) + PV_SLOT_DATA + PV_COOKED_MAGIC_LEN;
        return (const uint8_t *)FL_XIP(FL_DATA_LO + off);
    }
    return cart_default;                   /* nothing stored: the embedded cart */
}

uint32_t hh_cart_size(void)
{
    if (pv_run_slot < 0)
        return (uint32_t)cart_default_len;
    return pv_cart.slot_len[(uint8_t)pv_run_slot] - PV_COOKED_MAGIC_LEN;
}

/* Which loader the payload wants: 0 = a cooked cart (p8_load_cooked), 1 = precompiled bytecode
 * (p8_load_bytecode). The embedded default cart was never on the wire, so it is always cooked. */
int hh_cart_kind(void)
{
    return pv_run_slot >= 0 ? (int)pv_cart.slot_kind[(uint8_t)pv_run_slot] : 0;
}

/* p8_load_cooked()/p8_begin() refused the cart: offer the next one (the store's rollback). */
int hh_cart_next(void) { return pv_run_next(); }

/* One F0..F7 frame from the USB host. usb.c hands it over raw (no F0/F7); rebuild the frame
 * the shared parser wants, and only take it if it is ours - M-UPGRADE's own frames must stay
 * for ota_service() (ota_take() consumes whatever is pending). */
#define PV_SX_MAX 642u            /* usb.c's sx_frame is 640, plus F0 and F7 */
static uint8_t pv_sx[PV_SX_MAX];

static void pv_cart_poll(void)
{
    const uint8_t *p;
    uint32_t n, k;
    if (!ota_frame_get(&p, &n))
        return;
    if (n < 5u || p[0] != 0x22u || p[1] != 0x24u || p[2] != 0x35u || p[3] != 0x7Eu)
        return;                            /* not the cart command: leave it to ota_service() */
    ota_frame_done();                      /* ours: ota_take() must not see it */
    if (n + 2u > sizeof pv_sx)
        return;
    pv_sx[0] = 0xF0u;
    memcpy(pv_sx + 1, p, n);
    k = n + 1u;
    pv_sx[k++] = 0xF7u;
    if (pv_cart_sysex(pv_sx, k))
        picovave_boot();                   /* a slot was committed/erased: it runs from now */
}

/* ---------------------------------------------------- TIMER5: input + USB ---
 * 10 kHz: Makkuro37's matrix/ADC tick, the millisecond count and the polled USB SIE. USB runs
 * every 5th tick (2 kHz), as in main.c; no USB audio here, so there is nothing to nest. */
void fm1_timer5_irq(uint32_t isr_sp)
{
    static uint32_t sub, owed;
    uint32_t sp_here = (uint32_t)(uintptr_t)&sp_here;   /* the active stack, see pv_sp_min */
    uint32_t t0 = fm1_ticks(), usb_due = sub % 5u == 0u;

    if (sp_here < pv_sp_min)
        pv_sp_min = sp_here;
    if (!pv_stack_ok(sp_here) && pv_sp_bad == 0u) {
        const uint32_t *fr = (const uint32_t *)(uintptr_t)isr_sp;   /* psr/rets/reti, one is the pc */
        /* Which of the three names the interrupted code: the image runs from XIP (app.ld's XIP
         * origin, FM1_XIP(FL_APP_LO)) up to _etext, and .ram_text runs from RAM. The link addresses
         * keep this in the HAL's terms - no literal window in the app. */
        uint32_t xlo = FM1_XIP(FL_APP_LO), xhi = (uint32_t)(uintptr_t)_etext;
        uint32_t rlo = (uint32_t)(uintptr_t)_rt_start, rhi = (uint32_t)(uintptr_t)_rt_end;
        uint32_t w;

        pv_sp_bad = sp_here;
        pv_sp_bad_pc = fr[2];
        for (w = 0; w < 3u; ++w)
            if ((fr[w] >= xlo && fr[w] < xhi) || (fr[w] >= rlo && fr[w] < rhi))
                pv_sp_bad_pc = fr[w];
    }
    fm1_timer5_ack();
    fm1_input_tick();
    {   /* milliseconds from the 24 MHz TIMER4 (robust to a late tick: an upload's erase
         * disables interrupts for a sector at a time) */
        static uint32_t last, acc;
        acc += t0 - last;
        last = t0;
        while (acc >= 1000u * FM1_TICKS_PER_US) {
            acc -= 1000u * FM1_TICKS_PER_US;
            fm1_ms++;
        }
    }
    if (usb_due)
        owed |= 1u;
    if (++sub == 10u)
        sub = 0;
    if (owed & 1u)
        usb_poll();
    owed = 0;
}
extern void isr_timer5(void);

/* -------------------------------------------------------- boot / loop / fault --- */
/* panel_up is declared with the HAL above: hh_lcd_init() sets it. */

static void picovave_enter_uboot(const char *why)
{
    fm1_audio_stop();
    if (panel_up) {                           /* the rescue runs headless: the panel may not be up */
        hg_clear(HG_BLACK);
        hg_text_center(56, why, 1, HG_GREEN);
        pv_blit();
    }
    fm1_delay_ms(20);
    usb_detach();
    fm1_delay_ms(30);
    picovave_bootguard.pending = 0;              /* intentional reset: not a failed boot */
    fm1_enter_uboot();
}

/* The USB rescue: the app-level recovery, entered after two failed boots (bootguard) or when
 * OCT- is held at power-on. It IS usb.c + ota.c and nothing else - no panel, no audio, no engine,
 * no font: the installer is the display, and the rescue's only job is to be reachable over USB.
 *
 * USB is the part that matters, and usb.c services the SIE ONLY from the TIMER5 ISR (2 kHz) -
 * usb_poll() has no other caller. An earlier version polled it from this loop (every 500 ms,
 * "no TIMER5"): a 512-byte update frame is 128 USB packets, so a read could not finish inside
 * ota_read()'s 5 s window and the session never even started - the device answered the handshake
 * and then silently ignored the update. TIMER5 runs here, exactly as in the main loop, and the
 * M-UPGRADE session is the same code that installs over it every day.
 *
 * Two ways out that need no working USB: SysEx F0 22 24 35 7D F7, and OCT- + OCT+ held 5 s.
 * Both reset into the mask-ROM UBOOT (fm1_enter_uboot). If even this cannot run, the bootguard
 * falls back there by itself: pending = 2 means the next reset enters recovery, recovery
 * resetting again -> BOOT_ROM. */
static void picovave_rescue(void)
{
    uint32_t t_oct = 0;

    picovave_bootguard.pending = 2;              /* this reset is a failed boot unless it goes through */
    fm1_wdt_arm(0x0Du);
    fm1_ms = 0;
    hh_input_init();                             /* the OCT- + OCT+ way out reads the matrix */
    usb_start();
    pv_sp_min = PV_SP_MIN_NONE;                  /* this run's meter; the boot report read the last run's */
    pv_sp_bad = 0u;
    pv_sp_bad_pc = 0u;
    fm1_timer5_start(isr_timer5, 4);             /* usb_poll() runs at 2 kHz from here */
    fm1_guard_lock_top();
    fm1_irq_enable_all();
    for (;;) {
        fm1_wdt_feed();
        usb_retry(fm1_ms);
        ota_service();                           /* the M-UPGRADE handshake, so the installer reaches it */
        if (usb.ota_req) {
            usb.ota_req = 0;
            ota_session();                       /* installs, then resets; returns only if nothing committed */
        }
        if (usb.uboot_req)                       /* SysEx F0 22 24 35 7D F7: the mask-ROM updater */
            picovave_enter_uboot("UBOOT (USB)");
        if ((fm1_in.buttons & FM1_OCT_BOTH) != FM1_OCT_BOTH)
            t_oct = fm1_ms;
        else if (fm1_ms - t_oct > 5000u)
            picovave_enter_uboot("UBOOT");
    }
}

/* The device loop: femto8's frame, then the interrupt-driven housekeeping. p8_step() blocks in
 * p8_sleep() -> vTaskDelay() (33 ms for a 30 fps cart, 2x16 ms for _update60), so this loop is
 * the frame clock - and the audio ring is topped up right before each step. */
static void picovave_loop(void)
{
    uint32_t healthy_since = fm1_ms, t_oct = 0;
    for (;;) {
        uint32_t m = fm1_ms;
        fm1_wdt_feed();
        usb_retry(fm1_ms);
        if ((uint32_t)(m - healthy_since) >= 30000u && picovave_bootguard.pending)
            bootguard_clear(&picovave_bootguard);        /* 30 s of this boot: it is healthy */
        if ((fm1_in.buttons & FM1_OCT_BOTH) != FM1_OCT_BOTH) {
            t_oct = fm1_ms;
        } else if (fm1_ms - t_oct > 5000u) {
            picovave_enter_uboot("UBOOT");               /* OCT- + OCT+ held 5 s */
        }
        pv_cart_poll();                      /* the cart upload, before ota_service() takes the frame */
        ota_service();                       /* M-UPGRADE handshake */
        if (usb.ota_req) {                   /* F0 22 24 35 7F F7: M-UPGRADE upgrade command */
            usb.ota_req = 0;
            ota_session();                   /* returns only if nothing was committed. Attempted
                                              * even when the JEDEC probe failed: refusing here
                                              * used to disable the only update path silently,
                                              * and a write that cannot verify is reported by the
                                              * protocol and unstaged, not a silent no-op. */
            picovave_boot();                 /* put the cart back on the panel */
            continue;
        }
        if (usb.uboot_req)                   /* SysEx F0 22 24 35 7D F7 from the host */
            picovave_enter_uboot("UBOOT (USB)");
        pv_audio_fill();
        picovave_frame();
    }
}

static void fm1_fault(const fm1_crash_t *c)
{
    static hg_crash_t h;              /* same: the handler must not need stack to say what happened */
    uint32_t t0;
    fm1_audio_stop();
    memset(&h, 0, sizeof h);
    h.pc = c->pc;
    h.vec = c->vec;                                   /* which fatal entry was taken */
    h.emu = c->emu;                                   /* bit 3: sp outside the stack window */
    h.dbg = c->dbg;                                   /* the bus/write trap bits */
    h.sp = pv_app_sp(c, &h.sp_tag, &h.stack_lo, &h.stack_top);
    h.usp = c->usp;
    h.ssp = c->ssp;
    h.sp_min = pv_sp_min;
    h.sp_min_tag = pv_stack_of(pv_sp_min);
    h.sp_bad = pv_sp_bad;
    h.sp_bad_pc = pv_sp_bad_pc;
    h.rets = c->rets;
    h.etm0 = c->etm[0];
    h.etm1 = c->etm[1];
    h.count = c->count;
    h.early = c->early;                               /* crashes < 3 s after boot, in a row */
    h.uptime_ms = c->uptime_ms;
    h.stack_used = pv_stack_depth();                  /* the high-water mark, this run included */
    if (panel_up) {
        hg_crash_screen(&h);
        pv_blit();
    }
    t0 = fm1_ticks();
    while ((uint32_t)(fm1_ticks() - t0) < 3000u * 1000u * FM1_TICKS_PER_US)
        ;
    fm1_reboot();                            /* a reset; the bootguard counts the failure */
}

/* -------------------------------------------------------- the SPL entry point ---
 * crt0.S (Makkuro37's, reused unchanged) maps app.bin at 0x02000120, sets the stacks and
 * calls fm1_cstart. Nothing may run from XIP before .data / .ram_text are copied, so the
 * copies happen here first. */
extern uint32_t _data_start[], _data_end[], _data_load[];
extern uint32_t _bss_start[], _bss_end[], _pool_start[], _pool_end[];
extern uint32_t _rt_start[], _rt_end[], _rt_load[];

void fm1_cstart(void)
{
    uint32_t *s, *d, boot_mode;
    uint8_t p3;
    fm1_time_init();
    fm1_reset_reason();
    p3 = fm1_boot.p3_rst;
    fm1_wdt_arm(0x0Du);                      /* ~8 s, reset mode: taken over before RAM is cleared */
    if ((p3 & 1u) && !(p3 & (4u | 0x40u)))
        bootguard_clear(&picovave_bootguard);   /* a power-on: no failed boot to count */
    boot_mode = bootguard_begin(&picovave_bootguard);
    if (boot_mode == BOOT_ROM)
        fm1_enter_uboot();                   /* too many failed boots: the mask-ROM updater */
    fm1_irq_init();                          /* fatal vectors + CPU-exception handler */
    for (d = _bss_start; d < _bss_end; d++)
        *d = 0;
    for (d = _pool_start; d < _pool_end; d++)
        *d = 0;
    for (s = _data_load, d = _data_start; d < _data_end; s++, d++)
        *d = *s;
    for (s = _rt_load, d = _rt_start; d < _rt_end; s++, d++)
        *d = *s;                             /* flash driver code that must run from RAM */
    fm1_mailbox_clear();
    /* THE EMU STACK-LIMIT WINDOW IS NOT ARMED, AND THAT IS THE FIX FOR "EMU 0008".
     *
     * This newlib's pi32v2 setjmp()/longjmp() use the jmp_buf ITSELF as a scratch stack, and the
     * hardware stack guard must not be pointed at that. From build/picovave/picovave.dis:
     *
     *   setjmp: 201efe4 [r0+] = {r15-r0}       ; save the registers into the buffer
     *           201efe8 r1 = sp
     *           201efec sp = r0                ; sp = jmp_buf + 64  (a .bss/.data address!)
     *           201eff0 sp += 100
     *           201eff2 [--sp] = {sp, ssp, usp, icfg, psr, rets, retx, rete, reti}
     *           201eff6 [sp+32] = r1           ; ...and sp through it
     *   longjmp: restores through the same path (201f008 sp = r0, 201f010 pop)
     *
     * FM1_GUARD_STACK points the EMU's sp windows at [_ustack_lo, _sstack_top], so every access
     * through that scratch sp is an sp-out-of-window violation. The app's setjmp lives in the
     * frame loop and its jmp_buf is a .bss global, so the FIRST frame of the first boot raised
     * EMU_MSG bit 3 and dropped into fm1_fault - which read exactly like a wild stack pointer
     * (PC inside setjmp, sp in jmpbuf_restart) and was never anything but this. Lua's own jmp_bufs
     * are stack locals, inside the window, so only the harness's setjmp tripped it - which is also
     * why the wasm simulator, whose setjmp touches no sp, has always been green.
     *
     * What still guards the stacks, so nothing here is unchecked:
     *   - the two write bands (FM1_GUARD_WRITE) fault a stack that runs into them, which is how a
     *     real overflow presents (that is what caught the pre-fix boot at 1,196 ms);
     *   - LUAI_MAXCCALLS=48 is sized against this 24,320-byte user stack (build_picovave.py
     *     measures the 124 B / 316 B frames), so Lua cannot recurse the engine off it;
     *   - pv_stack_paint()/pv_stack_depth() report what the previous run actually used ("STACK
     *     n/24320" on the boot screen).
     * The one case that can now pass unnoticed is a single frame LARGER than the 256-byte band
     * that jumps over it without writing to it. */
    fm1__dbg_unlock();
    FM1_EMU_CON &= ~(1u << 3);               /* belt and braces: never armed by anything else */
    fm1__dbg_lock();
    fm1_guard_enable(FM1_GUARD_WRITE | FM1_GUARD_BUS | FM1_GUARD_PC);
    /* The panel first, before anything this firmware added. The LCD keeps its last frame through a
     * reset, so an unchanged picture on the panel cannot by itself tell "the flash never landed"
     * from "the new build died before it could draw". A mark naming this build, painted at the
     * earliest moment the panel can be driven, splits those two cases apart. */
    pv_early_mark();
    pv_stack_prev = pv_stack_depth();         /* what the previous boot used, before repainting */
    pv_stack_paint();
    {   /* USB rescue: two failed boots, or OCT- held at power-on. The 50 ms matrix scan is the same
         * one Makkuro37's recovery_key() does before its panel comes up. */
        uint32_t t0 = fm1_ticks();
        fm1_input_init();
        do {
            fm1_wdt_feed();
            fm1_input_scan();
        } while ((uint32_t)(fm1_ticks() - t0) < 50u * 1000u * FM1_TICKS_PER_US);
    }
    if (boot_mode == BOOT_RECOVERY || bootguard_manual(fm1_in.buttons))
        picovave_rescue();                      /* never returns: the app-level updater over USB-MIDI */
    fl_plain_window_init();                     /* XIP-read the SLOOP store (plain, undecrypted) */
    pv_cart_init();                             /* which slot runs: the newest committed one */
    picovave_boot();                            /* hh_time/lcd/input init, p8_init, load, one frame */
    flash_ok = FL_JEDEC_OK(FL_FAR(fl_jedec_ram)());
    if (flash_ok)
        ota_boot_cleanup();                     /* staging area left by an update */
    pv_audio_init();
    pv_audio_fill();
    usb_start();
    /* The sp meter belongs to the run that dies: picovave_boot() has now shown the PREVIOUS run's
     * numbers (pv_crash_report reads them first), so start this run's from here. .noinit is not
     * cleared on a cold power-on, and a stale small value would freeze the meter for the whole
     * run - that is what made one crash report read "DEEP 5731C1F4", a number no stack ever had. */
    pv_sp_min = PV_SP_MIN_NONE;
    pv_sp_bad = 0u;
    pv_sp_bad_pc = 0u;
    fm1_timer5_start(isr_timer5, 4);            /* above ALNK0 (3) */
    fm1_guard_lock_top();                       /* after the last fm1_irq_attach */
    fm1_irq_enable_all();
    picovave_loop();
}
