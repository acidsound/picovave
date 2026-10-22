/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* App entry. The SPL maps app.bin at XIP 0x02000120 and jumps to its first
 * byte with r0 = boot-parameter block (0x01C7FE08, inside the vector table).
 * The SPL expects `goto +4; rts` as the first instruction. The core state
 * the SPL left is saved first; then interrupts off and our stacks. */
	.section .text.crt0,"ax",@progbits
	.globl _start
_start:
	goto 1f
	rts
1:	r1 = icfg
	r2 = psr
	cli
	r3 = 0x1C07F00		/* STATUS (debug area) */
	[r3 + 0x20] = r1	/* icfg as handed over */
	[r3 + 0x24] = r2	/* psr */
	[r3 + 0x28] = r0	/* boot-parameter pointer */
	r1 = sp
	[r3 + 0x2C] = r1
	r1 = ssp
	[r3 + 0x30] = r1
	sp = _ustack_init	/* not _ustack_top: the ABI stores at positive sp offsets */
	ssp = _sstack_init
	call fm1_cstart
	goto .

	.balign 4
	.globl fm1_c1_entry
fm1_c1_entry:
	cli
	r1 = fm1_c1_trace
	r2 = 0xC1000001
	[r1 + 0] = r2
	icfg = 0
	usp = _c1_ustack_init
	sp = _c1_ustack_init
	ssp = _c1_sstack_init
	reti = fm1_c1_main
	rti

	.section .noinit.c1stack,"aw",@nobits
	.balign 8
	.globl _c1_ustack
_c1_ustack:
	.space 1792
	.globl _c1_ustack_init
_c1_ustack_init:
	.space 256
	.globl _c1_ustack_top
_c1_ustack_top:
_c1_sstack:
	.space 192
	.globl _c1_sstack_init
_c1_sstack_init:
	.space 64
	.globl _c1_sstack_top
_c1_sstack_top:
