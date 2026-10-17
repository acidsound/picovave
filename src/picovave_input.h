/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave_input.h - the logical input model shared by the firmware and the simulator.
 *
 * This file is hardware-free on purpose: it only says WHAT the app can observe, not how
 * it is read. Each consumer supplies the two hh_* HAL calls below in its own HAL:
 *
 *   firmware   picovave/src/picovave.c          Makkuro37's firmware/hal/smk_input.h (74HC595 SPI2 scan)
 *   simulator  picovave/sim/picovave_sim_hal.h  whatever the page set through sim_button/key/enc
 *   preview    picovave/host/preview.c       a scripted HAL for the host render
 *
 * Because hin_t and the edge logic live in the shared code, the same physical press and
 * the same click on the page produce the same bytes on the panel. That equality is what
 * tools/verify_final.py pins down with a scripted-input pixel comparison.
 *
 * The SMK-37 Pro's inputs, digital and analog (stock v016, work/spec/input.md):
 *   18 buttons   -> bit 0..17 of hin_t.buttons   (KNOB BANK, FADER BANK, <, >, OCTAVE-/+, ...)
 *   37 keybed    -> bit 0..36 of hin_t.keys      (lowest C .. highest C)
 *   8 encoders   -> hin_t.enc[i], detents since the previous read (K1..K8)
 *   16 pads      -> bit 0..15 of hin_t.pads      (velocity of the hit in pad_vel[p])
 *   4 faders     -> hin_t.fader[0..3]            (0..127)
 *   2 wheels     -> hin_t.wheel[0..1]            (raw: 0 = pitch, 1 = mod)
 *
 * The pads and the keybed are the same physical switches the device scans; the faders and the
 * two wheels reach it through the ADC mux (Makkuro37's smk_input.h reads them the same way).
 * Keeping all of it in this one model is what lets the ROM, the simulator and the preview
 * observe the SAME panel.
 */
#pragma once
#include <stdint.h>

#define HIN_NBTN 18u
#define HIN_NKEY 37u
#define HIN_NENC 8u
#define HIN_NPAD 16u
#define HIN_NFADER 4u
#define HIN_NWHEEL 2u

/* The keybed is C..C (37 keys). We name key 0 MIDI 48 (C3), so key 24 is C5 (523 Hz). The
 * simulator's square-wave voice turns a key into that note; the mapping itself is shared. */
#define HIN_KEY_MIDI0 48u

typedef struct {
    uint32_t buttons;                    /* bit i = button i held */
    uint64_t keys;                       /* bit k = key k held */
    int8_t   enc[HIN_NENC];              /* detents since the previous read (signed, consumed) */
    uint16_t pads;                       /* bit p = pad p held */
    uint8_t  pad_vel[HIN_NPAD];          /* velocity 1..127 of pad p while it is held */
    uint8_t  fader[HIN_NFADER];          /* 0..127 */
    uint16_t wheel[HIN_NWHEEL];          /* raw 0..1023 (pitch springs to ~540, mod bottom 0) */
} hin_t;

/* The two calls each HAL must provide (static, defined before picovave_p8.h is included):
 *
 *   void hh_input_init(void);          bring the input hardware up; called by picovave_boot()
 *   void hh_input_read(hin_t *in);     fill in the current state; encoder detents are
 *                                      returned once and then cleared
 */
