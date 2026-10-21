/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave_p8.h - what picovave DOES: run a PICO-8 cart on the SMK-37 Pro.
 *
 * This is the shared app layer. The simulator (sim/sim.c) includes it unchanged and
 * swaps only the hh_* HAL, exactly as it did for the hello skeleton. The device
 * (src/picovave.c) includes it too, with its own HAL.
 *
 * The engine is the vendored femto8 (femto8/). femto8's own main loop blocks, so
 * picovave drives it frame by frame: p8_begin() once, then one p8_step() per frame
 * (femto8/README.picovave.md documents the patch). A cart's reset()/run() longjmps
 * to jmpbuf_restart, which this loop owns with a per-frame setjmp().
 *
 * The HAL this file depends on (the same hh_* surface picovave_input.h names):
 *
 *   hh_time_init()      start the time base
 *   hh_lcd_init()       bring the panel up
 *   hh_input_init()     bring the inputs up
 *   hh_input_read(&in)  the current buttons/keys/encoders/pads/faders/wheels
 *   hh_boot_stage(n)    mark boot stage n (1 time/LCD/input, 2 engine init, 3 cart decoded,
 *                       4 cart started, 5 first frame done). A target with a panel shows the
 *                       last stage reached, so a device that dies on the way up is not a black
 *                       screen with no information; a target without one ignores it.
 *
 * plus the cart source, which differs per target:
 *
 *   hh_cart_data()/hh_cart_size()   the cooked cart to run: the 32,800-byte cooked cart a
 *                                   .p8.png carries (sim: the baked cart; device: the SLOOP
 *                                   store, past the magic its slot header is checked against).
 *                                   p8_load_cooked() re-checks the size, so a target that
 *                                   hands over something else fails with a status, not a crash.
 *   hh_cart_next()                  drop to the next cart this target can offer, when the engine
 *                                   could not take the one it was given (device: the store holds
 *                                   the cart uploaded last AND the one it replaced, and the
 *                                   newest one may be too big to run; the last candidate is
 *                                   always something that runs). Returns 0 when there is
 *                                   nothing more to try.
 */
#pragma once
#include <setjmp.h>
#include <stdint.h>
#include <string.h>

#include "picovave_input.h"
#include "p8_emu.h"
#include "p8_audio.h"
#include "ble_controller.h"

uint32_t picovave_boots;              /* frames stepped; the link check names it */
int32_t  picovave_status;             /* 0 while running, else the last engine error */

#ifdef PV_DEBUG_REMOTE
/* Diagnostic build only: a host can drive the gamepad and read the engine's state over the cart
 * SysEx channel. Both are declared here because pv_gamepad() is in this header; the command
 * parser lives in src/picovave.c. */
extern uint32_t pv_dbg_gamepad;
extern uint16_t pv_dbg_gamepad_frames;
#endif

/* Provided by the target. */
extern const uint8_t *hh_cart_data(void);
extern uint32_t       hh_cart_size(void);
extern int            hh_cart_kind(void);   /* 0: cooked cart, 1: precompiled bytecode */
extern int            hh_cart_next(void);   /* 0: this was the last one the target can offer */
extern void           hh_boot_stage(int stage);

/* The owner-verified SMK-37 Pro game permutation (ref/gamedude src/smk37_game_controls.h,
 * tests/smk37_game_controls.c). The keybed's low white/black keys and the pad rows both
 * drive the D-pad, and two high keys / two pads are O and X.
 *
 * One more pad and its key carry PICO-8's sixth button: p8_input.c turns the seam's Start button
 * (the pause button on any gamepad) into BUTTON_MASK_PAUSE, which a cart reads as PICO-8's btn(6).
 * PICO-8's other keyboard control is ESC, which stops the cart and hands the player back to the
 * console. This port is execution-only - there is no console or editor to return to - so it
 * carries no ESC input at all. All seven are polled exactly like the six above, from the keys on
 * the bed and the pads beside them. */
static uint32_t pv_gamepad(void)
{
    hin_t in;
    uint32_t lo, hi, g = 0;

    hh_input_read(&in);

    lo = (uint32_t)in.keys;
    hi = (uint32_t)(in.keys >> 32);

    if ((lo & ((1u << 5) | (1u << 7))) || (in.pads & (1u << 8)))
        g |= AXIS_L_LEFT;
    if ((lo & (1u << 9)) || (in.pads & (1u << 9)))
        g |= AXIS_L_DOWN;
    if ((lo & ((1u << 11) | (1u << 12))) || (in.pads & (1u << 10)))
        g |= AXIS_L_RIGHT;
    if ((lo & ((1u << 8) | (1u << 10))) || (in.pads & (1u << 1)))
        g |= AXIS_L_UP;
    if ((hi & (1u << 1)) || (in.pads & (1u << 15)))
        g |= BUTTON_1;                /* A = PICO-8 O */
    if ((hi & (1u << 4)) || (in.pads & (1u << 14)))
        g |= BUTTON_2;                /* B = PICO-8 X */
    if ((hi & (1u << 0)) || (in.pads & (1u << 13)))
        g |= BUTTON_START;            /* the seam's Start: a cart reads it as PICO-8's btn(6) */

#ifdef PV_DEBUG_REMOTE
    /* The diagnostic harness overrides the panel, one frame at a time. This is deliberately
     * after the physical map: a harness that sends a mask means exactly that mask, not "also
     * hold the keys the bench is leaning on". */
    if (pv_dbg_gamepad_frames) {
        g = pv_dbg_gamepad;
        pv_dbg_gamepad_frames--;
    }
#endif

    return g;
}

static void picovave_frame(void)      /* one 1/30 s tick */
{
    unsigned steps, i;

    gamepad = pv_gamepad();

    if (picovave_status != 0) {
        picovave_boots++;
        return;
    }

    /* _update60 carts run 60 times a second: step twice per 1/30 s tick, as femto8's own
     * main loop does (m_fps is 60 for them, 30 otherwise). */
    steps = (m_fps >= 60u) ? 2u : 1u;

    for (i = 0; i < steps; i++) {
        /* THE harness's setjmp. Two things to know before touching it:
         *   - it must be re-established every frame: setjmp() saves the CURRENT sp, so a jmp_buf
         *     left over from another call depth is a jump into a dead frame;
         *   - picovave_boot() runs the ladder's p8_begin() OUTSIDE any setjmp, so a cart whose
         *     _init calls reset()/run() longjmps into whatever jmpbuf_restart last held. The
         *     default cart does not, and a cart that does would have to be handled by wrapping the
         *     ladder the same way this loop wraps the frame (docs/HANDOVER.md §7).
         * (The device's hardware sp window is deliberately not armed: this jmp_buf is a .bss global
         * and newlib's setjmp points sp into it. See the comment at fm1_guard_enable.) */
        if (setjmp(jmpbuf_restart)) {
            /* a cart's reset()/run() unwound here: reinitialise and carry on */
            if (p8_begin() != 0)
                picovave_status = -1;
            continue;
        }
        int r = p8_step();
        if (r != 0) {
            picovave_status = r;
            break;
        }
    }

    picovave_boots++;
}

static void picovave_boot(void)       /* everything before the loop; re-runnable (POWER-ON) */
{
    static int engine_up;             /* p8_init() allocates once and asserts !m_initialized */
    const uint8_t *p;
    uint32_t n;

    if (!engine_up) {
        hh_time_init();
        hh_lcd_init();
        hh_input_init();
        hh_boot_stage(1);
        if (p8_init() != 0) {
            picovave_status = -1;
            return;
        }
        hh_boot_stage(2);
        engine_up = 1;
    }

    /* A reboot re-runs the cart, as p8_run() does on a cart's reset(): reload and step once.
     * p8_load_cooked()/p8_begin() are repeatable; only p8_init() is not.
     *
     * The target may hold more than one cart (the device's store keeps the one uploaded last and
     * the one it replaced), and the newest is not always one this device can RUN: a cart's Lua
     * costs 10-20x its source and the pool is small, so p8_begin() can honestly fail. Walk down
     * the target's ladder rather than leave the panel dark until the next upload; the last
     * candidate is always a cart that runs. */
    picovave_status = 0;
    picovave_boots = 0;

    for (;;) {
        /* Empty the pool before anything is judged against it. What is in it belongs either to
         * the cart the device is still running (every USB upload boots with the old cart live,
         * and it holds up to the whole Lua budget) or to a candidate that failed just now - and
         * a cart that ran the pool out leaves that memory unreachable but uncollected, since Lua
         * throws out of the compile and nothing runs a GC after it. p8_load_cooked() needs 64 KiB
         * of the pool for the Lua source buffer, so without this a cart that FITS was refused for
         * memory another cart was standing on, and the ladder could not take a single step: the
         * panel stayed on the boot screen after baba was uploaded and never moved again. That was
         * the real SMK-37 Pro symptom (docs/HANDOVER.md §7), not a hardware fault. */
        p8_stop_cart();

        p = hh_cart_data();
        n = hh_cart_size();
        /* The page may have compiled the cart already: then the payload is the ROM plus the
         * bytecode, and the device never materialises the source or runs the compiler. */
        if (p && (hh_cart_kind() ? p8_load_bytecode(p, n) : p8_load_cooked(p, n)) == 0) {
            hh_boot_stage(3);
            if (p8_begin() == 0) {
                picovave_status = 0;          /* THIS candidate runs: the ones before it do not count */
                hh_boot_stage(4);
                break;                        /* it runs */
            }
        }
        /* It did not start. Step down the ladder; the embedded default cart is the last
         * candidate, so this can only run out if even that cannot be loaded. */
        if (!hh_cart_next()) {
            picovave_status = -1;             /* nothing left to try: the boot screen stays up */
            break;
        }
    }

    picovave_frame();
    hh_boot_stage(5);
}
