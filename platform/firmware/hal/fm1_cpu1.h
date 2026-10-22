/* SPDX-License-Identifier: GPL-3.0-only
 * Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments */
/* The AC79's second core: startup, IPC mailbox, and probe.
 * Starts Core 1 from vector 0x01C7FFF8 via C1_CON and FM1_C1_CLK. */
#pragma once
#include <stdint.h>
#include "fm1_sys.h"
#include "fm1_guard.h"
#include "fm1_time.h"

extern void fm1_c1_entry(void);

enum {
    C1_JOB_IDLE       = 0,
    C1_JOB_PING       = 1,
    C1_JOB_RENDER     = 2,
    C1_JOB_PUSH_DIRTY = 3,
};

typedef struct {
    volatile uint32_t c0_cnum;
    volatile uint32_t c1_cnum;
    volatile uint32_t c1_alive;
    volatile uint32_t c0_heartbeat;
    volatile uint32_t c1_heartbeat;
    volatile uint32_t job;
    volatile uint32_t job_cmd;
    volatile uint32_t job_arg;
    volatile uint32_t done;
    volatile uint32_t done_res;
    volatile uint32_t pingpong_ok;
    volatile uint32_t pingpong_total;
    volatile uint32_t elapsed_us;
    volatile uint32_t render_jobs;
    volatile uint32_t render_done;
    volatile uint32_t has_dirty_mask;
    uint8_t row_dirty[240];
} fm1_c1_probe_t;

extern fm1_c1_probe_t pv_c1_probe;
extern volatile uint32_t fm1_c1_trace;

extern void p8_render(void);
extern void pv_c1_do_push(void);

static inline uint32_t fm1_core_id(void)
{
    uint32_t id;
    __asm__ volatile("%0 = cnum" : "=r"(id));
    return id;
}

static inline void fm1_sync(void)
{
    __asm__ volatile("csync" ::: "memory");
}

void fm1_c1_main(void) __attribute__((noreturn, used));
void fm1_c1_main(void)
{
    uint32_t id;
    __asm__ volatile("%0 = cnum" : "=r"(id));
    pv_c1_probe.c1_cnum = id;
    fm1_c1_trace = 0xC1000002;
    __asm__ volatile("csync" ::: "memory");
    pv_c1_probe.c1_alive = 1;
    __asm__ volatile("csync" ::: "memory");

    for (;;) {
        uint32_t j;
        pv_c1_probe.c1_heartbeat++;
        __asm__ volatile("csync" ::: "memory");

        j = pv_c1_probe.job;
        if (j != pv_c1_probe.done) {
            uint32_t cmd = pv_c1_probe.job_cmd;
            if (cmd == C1_JOB_RENDER) {
                p8_render();
                pv_c1_probe.render_done = j;
            } else if (cmd == C1_JOB_PUSH_DIRTY) {
                pv_c1_do_push();
                pv_c1_probe.render_done = j;
            } else if (cmd == C1_JOB_PING) {
                uint32_t arg = pv_c1_probe.job_arg;
                pv_c1_probe.done_res = arg ^ 0x55AA55AAu ^ (j * 0x9E3779B1u);
            }
            __asm__ volatile("csync" ::: "memory");
            pv_c1_probe.done = j;
            __asm__ volatile("csync" ::: "memory");
        }
    }
}

static inline void fm1_c1_kick_render(void)
{
    uint32_t next = pv_c1_probe.job + 1u;
    if (next == 0u)
        next = 1u;
    pv_c1_probe.job_cmd = C1_JOB_RENDER;
    pv_c1_probe.render_jobs = next;
    fm1_sync();
    pv_c1_probe.job = next;
    fm1_sync();
}

static inline void fm1_c1_kick_push_dirty(void)
{
    uint32_t next = pv_c1_probe.job + 1u;
    if (next == 0u)
        next = 1u;
    pv_c1_probe.job_cmd = C1_JOB_PUSH_DIRTY;
    pv_c1_probe.render_jobs = next;
    fm1_sync();
    pv_c1_probe.job = next;
    fm1_sync();
}

static inline int fm1_cpu1_ping(uint32_t round)
{
    pv_c1_probe.job_cmd = C1_JOB_PING;
    pv_c1_probe.job_arg = round * 10007u;
    fm1_sync();
    pv_c1_probe.job = round;
    fm1_sync();

    uint32_t w0 = fm1_ticks();
    while (pv_c1_probe.done != round) {
        fm1_sync();
        if ((uint32_t)(fm1_ticks() - w0) > 24000u * 5u)
            return 0;
    }
    if (pv_c1_probe.done == round &&
        pv_c1_probe.done_res == ((round * 10007u) ^ 0x55AA55AAu ^ (round * 0x9E3779B1u))) {
        return 1;
    }
    return 0;
}

static inline int fm1_cpu1_start(void)
{
    uint32_t saved, t0, i;
    fm1_cpu1_halt();

    pv_c1_probe.c1_alive = 0;
    pv_c1_probe.c1_cnum = 0xEEEEEEEEu;
    pv_c1_probe.c1_heartbeat = 0;
    fm1_c1_trace = 0xC1000000;
    __asm__ volatile("csync" ::: "memory");

    for (i = 0; i < 32u; i++)
        *(volatile uint32_t *)(0x1EEF300u + 4u * i) = 0;

    fm1_guard_unlock_top();
    *(volatile uint32_t *)0x01C7FFF8u = (uint32_t)(uintptr_t)&fm1_c1_entry;

    saved = FM1_C1_CLK;
    FM1_C1_CLK = saved | 0x8u;
    __asm__ volatile("csync" ::: "memory");

    FM1_C1_CON |= 0x8u;
    FM1_C1_CON &= ~0x2u;
    __asm__ volatile("csync" ::: "memory");

    t0 = fm1_ticks();
    while (!pv_c1_probe.c1_alive && (uint32_t)(fm1_ticks() - t0) < 24000u * 50u) {
        __asm__ volatile("csync" ::: "memory");
    }
    FM1_C1_CLK = saved;
    if (!pv_c1_probe.c1_alive) {
        fm1_cpu1_halt();
        return -1;
    }
    return 0;
}
