/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* Felucca memory map.
 * Code + constants execute in place from XIP (app.bin offset 0 = 0x02000120).
 * 0x01C00000..0x01C00E00 holds .ram_text (code that must run while the flash is
 * busy: the flash driver; it used to be given 24 KiB and the 20 KiB it never
 * used, plus the 8 KiB "RAM-run debug" reserve after it, are the Lua heap now).
 * RAM  : .data/.bss, then the pool right behind them - ONE region, so the heap
 *        starts at whatever .bss ends at. (It used to be two regions with a
 *        hand-set 1,848-byte hole between them, 0x01C2C000; the smallest cart
 *        that still failed was 527 B short.) The app's static half is now
 *        PICO-8's own 128x128 screen (32,768 B) rather than a 240x240 panel
 *        frame (115,200 B): the 128->240 scale happens a row at a time on the
 *        way out, in src/picovave.c.
 * POOL : [_pool_start,_pool_top) is picovave's Lua heap; section .pool itself
 *        holds nothing (alloc.c sizes its arena from the linker's symbols).
 * STACKS with 256-byte guard bands; NOINIT survives resets and UBOOT entry.
 * 0x01C7FD50.. is reserved: boot_info, boot-state mailbox, vector table. */
MEMORY
{
    XIP    (rx)  : ORIGIN = 0x02000120, LENGTH = 0x8DFBC
    RAMTEXT (rwx): ORIGIN = 0x01C00000, LENGTH = 0x0E00
    RAM    (rwx) : ORIGIN = 0x01C00E00, LENGTH = 0x73200
    NOINIT (rw)  : ORIGIN = 0x01C7C000, LENGTH = 0x3D50
}
SECTIONS
{
    .text : { KEEP(*(.text.crt0)) *(.text*) *(.rodata*) . = ALIGN(4); _etext = .; } > XIP
    .ram_text : { _rt_start = .; KEEP(*(.ram_text*)) . = ALIGN(4); _rt_end = .; } > RAMTEXT AT > XIP
    _rt_load = LOADADDR(.ram_text);
    .data : { . = ALIGN(4); _data_start = .; *(.data*) *(.sdata*) . = ALIGN(4); _data_end = .; } > RAM AT > XIP
    _data_load = LOADADDR(.data);
    .bss (NOLOAD) : { _bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); _bss_end = .; } > RAM
    .pool (NOLOAD) : { . = ALIGN(8); _pool_start = .; *(.pool*) . = ALIGN(8); _pool_end = .; } > RAM
    /* The pool runs to the stack guard: every byte between .bss and here is the Lua heap. */
    _pool_top = ORIGIN(RAM) + LENGTH(RAM);
    .noinit (NOLOAD) : { *(.noinit*) } > NOINIT
    _guard0 = 0x01C74000; _ustack_lo = 0x01C74100; _ustack_top = 0x01C7A000;
    _guard1 = 0x01C7A000; _sstack_lo = 0x01C7A100; _sstack_top = 0x01C7C000;

    /* crt0 sets sp/ssp a little BELOW the tops. This ABI stores at POSITIVE sp offsets - the
     * first thing a callee does can be [sp+32] - so a call made with sp at the very top writes
     * above the stack: into guard band 1 (_guard1 == _ustack_top, DBG bit 13) or past the
     * guard's window end (_sstack_top, EMU bit 3). The outermost frame of the app is exactly
     * that shallow, which is why the crash landed on picovave_frame()'s first setjmp().
     * 0x400 covers every call-site offset in the image (largest observed: [sp+32]); the
     * supervisor stack only runs the vector stubs and ISR bodies, so 0x100 is plenty there. */
    _ustack_init = _ustack_top - 0x400;
    _sstack_init = _sstack_top - 0x100;
    /DISCARD/ : { *(.comment) *(.note*) }
}
