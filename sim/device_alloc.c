/* SPDX-License-Identifier: GPL-3.0-only */
/* device_alloc.c - the simulator with the DEVICE's allocator as its heap.
 *
 * sim/heap_probe.c answers "how much would this cart need on the device" by charging every
 * request n + 16 and handing it to emscripten's allocator. That models the device's COST, but it
 * never runs the device's heap - and on 2026-10-17 that blind spot is what let a broken malloc()
 * reach hardware: alloc.c's split wrote the remainder's header at the block AFTER the one being
 * split, so the very first allocation p8_init() makes labelled the sentinel as a 285 KB free
 * block and put its footer past the pool. Every local test was green (the sim and the host use
 * other allocators), and the device crashed on boot, again and again, into recovery.
 *
 * tools/build_sim.py --device-alloc compiles THIS file instead of the probe, in the same build,
 * with the same rename of malloc/free/realloc/calloc to sim_*: the whole simulator - the engine's
 * boot, every cart in sim_test.mjs, the page's uploads - then runs on femto8/stub/device/alloc.c
 * over the DEVICE's own arena. A cart that the modelled probe says fits but this allocator cannot
 * serve now fails the run instead of failing a flash.
 *
 * The peak is read from alloc.c's own headers (the block size, header + footer + rounding), so it
 * is what the pool really spends - a little above the probe's charge when a first-fit hands a
 * block over whole. */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/* app.ld: the arena is [_pool_start,_pool_top) - .data/.bss, then .pool, one RAM region, so its
 * size is 0x01C74000 (. _guard0) minus wherever .bss ends. tools/verify_final.py reads the same
 * number back out of build/picovave/picovave.map and fails if this constant has drifted from it
 * (and from the page's, sim_test.mjs's and host/alloc_test.c's copies). */
#define PV_ARENA 414184u                  /* app.ld: [_pool_start,_pool_top), 0x01C0EE18..0x01C74000 */

/* ...but this is a SIMULATOR, so the arena can be any size a harness asks for: PV_ARENA_MAX is
 * just the array, and sim_arena_set() picks how much of it alloc.c may use - which answers "how
 * big an arena would this cart NEED?" without a rebuild (that question is what decided the squeeze
 * in docs/HANDOVER.md 12: a cart's cost-model peak is not the same as the arena it can survive in,
 * because the device allocator's refusal makes Lua run a full GC and retry). set it BEFORE the
 * first allocation (sim_init()/p8_init()): alloc.c initialises its heap once, lazily, and there is
 * no reset. PV_ARENA is what the DEVICE links and what every other copy must match. */
#define PV_ARENA_MAX (1024u * 1024u)
static uint8_t pv_arena[PV_ARENA_MAX] __attribute__((aligned(8), used));
static uint32_t pv_arena_len = PV_ARENA;

#define PV_POOL_START pv_arena            /* alloc.c's seam: the arena, not the linker's POOL */
#define PV_POOL_TOP  (pv_arena + pv_arena_len)

/* The build renames the four functions for the probe; undo that for alloc.c's own definitions,
 * then give them pv_ names so this file's sim_* wrappers can count what they do. */
#undef malloc
#undef free
#undef realloc
#undef calloc
#define malloc  pv_malloc
#define free    pv_free
#define realloc pv_realloc
#define calloc  pv_calloc
#include "../femto8/stub/device/alloc.c"
#undef malloc
#undef free
#undef realloc
#undef calloc

static long long used, peak;
static unsigned refused;        /* requests alloc.c answered with NULL */

/* the block a payload pointer belongs to (alloc.c's header sits right in front of it) */
static uint32_t blk_of(const void *p) { return ((const bhdr_t *)((const uint8_t *)p - PV_HDR))->size; }

void *sim_malloc(size_t n)
{
    void *p = pv_malloc(n);
    if (!p) {
        refused++;
        return NULL;
    }
    used += blk_of(p);
    if (used > peak)
        peak = used;
    return p;
}

void sim_free(void *p)
{
    if (!p)
        return;
    used -= blk_of(p);
    pv_free(p);
}

void *sim_realloc(void *p, size_t n)
{
    uint32_t before = p ? blk_of(p) : 0;
    void *q;
    if (n == 0) {                          /* alloc.c frees and returns NULL */
        sim_free(p);
        return NULL;
    }
    q = pv_realloc(p, n);
    if (!q) {
        refused++;
        return NULL;
    }
    used += (long long)blk_of(q) - before;
    if (used > peak)
        peak = used;
    return q;
}

void *sim_calloc(size_t n, size_t size)
{
    void *p = sim_malloc(n * size);
    if (p)
        memset(p, 0, n * size);
    return p;
}

#if defined(__EMSCRIPTEN__)
#define EXPORT __attribute__((used, visibility("default")))
#else
#define EXPORT __attribute__((used))
#endif

EXPORT unsigned sim_heap_peak(void) { return (unsigned)peak; }
EXPORT unsigned sim_heap_used(void) { return (unsigned)used; }
/* A fresh measurement (sim_init calls this on every boot): the peak restarts at what is
 * live and the refusal count restarts at zero, so a cart is judged on its own run. */
EXPORT void sim_heap_reset(void) { peak = used; refused = 0; }
/* The number that decides whether the device boots: femto8's engine treats a NULL from malloc as
 * a failed boot (p8_init/femto8's own allocators), so a cart may only be called device-runnable
 * if this stayed at zero. */
EXPORT unsigned sim_heap_refused(void) { return (unsigned)refused; }
/* 1: this build's heap IS femto8/stub/device/alloc.c. sim/heap_probe.c says 0. */
EXPORT unsigned sim_device_alloc(void) { return 1u; }
/* The sweep knob: how much of the arena array alloc.c may use. Returns what is in force now, so a
 * harness can check what it set. Must be called before the first allocation (see above). */
EXPORT void sim_arena_set(unsigned bytes)
{
    pv_arena_len = (bytes >= 4096u && bytes <= PV_ARENA_MAX) ? (bytes & ~(uint32_t)7) : PV_ARENA;
}
EXPORT unsigned sim_arena_get(void) { return (unsigned)pv_arena_len; }
