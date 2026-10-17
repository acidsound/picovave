/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave seam: femto8's allocator. p8_emu.h maps malloc/free onto these under
 * OS_FREERTOS. The device backs them with a fixed arena in .pool; the simulator
 * with the wasm heap. */
#pragma once
#include <stddef.h>

void *rh_malloc(size_t n);
void  rh_free(void *p);
