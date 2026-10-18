/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: the controller bitmask femto8 polls each frame (p8_input.c reads
 * `gamepad` under OS_FREERTOS). The board fills it from the keybed/buttons/pads. */
#pragma once
#include <stdint.h>

extern uint32_t gamepad;

#ifndef AXIS_L_LEFT
#define AXIS_L_LEFT    (1u << 0)
#define AXIS_L_RIGHT   (1u << 1)
#define AXIS_L_UP      (1u << 2)
#define AXIS_L_DOWN    (1u << 3)
#define AXIS_L_TRIGGER (1u << 4)
#define AXIS_R_LEFT    (1u << 5)
#define AXIS_R_RIGHT   (1u << 6)
#define AXIS_R_UP      (1u << 7)
#define AXIS_R_DOWN    (1u << 8)
#define AXIS_R_TRIGGER (1u << 9)
#define DPAD_UP        (1u << 10)
#define DPAD_RIGHT     (1u << 11)
#define DPAD_DOWN      (1u << 12)
#define DPAD_LEFT      (1u << 13)
#define BUTTON_1       (1u << 14)
#define BUTTON_2       (1u << 15)
/* PICO-8's other two: ⬜ and 🌵. 🌵 is a player button a cart reads itself - p8_input.c turns it
 * into BUTTON_MASK_PAUSE, which is bit 6 of the mask btn() hands out, i.e. PICO-8's btn(6). ⬜ is
 * not: p8_input.c turns it into BUTTON_MASK_ESCAPE, which is outside btn()'s 0..7 and acts on the
 * engine instead (p8_emu.c p8_check_for_pause() calls p8_abort(), and this harness's setjmp re-
 * begins the cart). Both are polled exactly like the six. */
#define BUTTON_SELECT  (1u << 16)
#define BUTTON_START   (1u << 17)
#endif
