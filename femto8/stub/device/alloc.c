/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave device stub: malloc / free / realloc / calloc.
 *
 * The JieLi toolchain's newlib ships no allocator at all - the AC79 SDK brings its
 * own. picovave is self-contained, so the device gets one here: a boundary-tag
 * first-fit heap with forward and backward coalescing, over a fixed arena in .pool
 * (app.ld sizes POOL exactly for it: the panel lives in .bss, so all of .pool is
 * heap).
 *
 * Everything goes through this one allocator: femto8's seven big buffers (p8_emu.h
 * maps malloc/free onto rh_malloc/rh_free, which the seam forwards here) and Lua's
 * own churn (lauxlib.c calls realloc/free directly). Keeping them in one heap is
 * what lets the engine's boot allocations sit low and contiguous while Lua grows
 * above them.
 *
 * Interrupt safety: femto8's audio ISR (render_sounds) allocates nothing, and the
 * device calls no Lua or allocation from an interrupt, so there is no lock. */
#include <stddef.h>
#include <stdint.h>

/* The arena IS the linker's POOL region (app.ld), minus a red zone. Sizing it
 * from the script instead of a fixed array means a repartition of RAM/POOL moves
 * the Lua heap with it - and how much Lua gets is exactly POOL - (the engine's
 * 271,872 B) - (the red zone). build_picovave.py asserts that remainder.
 *
 * PV_POOL_START/PV_POOL_TOP are expressions, so a test can point this allocator at
 * an arena of its own (tests/alloc_host.c and sim/device_alloc.c do exactly that:
 * neither this file nor the device build changes). */
#ifndef PV_POOL_START
extern uint8_t _pool_start[], _pool_top[];
#define PV_POOL_START _pool_start
#define PV_POOL_TOP   _pool_top
#endif
#define PV_POOL_GUARD 1024u

typedef struct {
    uint32_t size;                 /* total block bytes (header + payload + footer), multiple of 8 */
    uint32_t free;                 /* 1 while the block is unallocated */
} bhdr_t;

#define PV_HDR     8u              /* bhdr_t */
#define PV_FTR     4u              /* the size copy at the block's end, for backward coalescing */
#define PV_MIN_BLK 24u             /* header + footer + 8 bytes of payload */

static uint8_t *pv_beg;            /* the first block */
static uint8_t *pv_end;            /* the sentinel: never allocated, terminates every scan */
static int      pv_up;

static uint32_t *pv_ftr(void *b) { return (uint32_t *)((uint8_t *)b + ((bhdr_t *)b)->size - PV_FTR); }
static uint8_t  *pv_next(void *b) { return (uint8_t *)b + ((bhdr_t *)b)->size; }

static void pv_set(void *b, uint32_t size, uint32_t free)
{
    ((bhdr_t *)b)->size = size;
    ((bhdr_t *)b)->free = free;
    *pv_ftr(b) = size;
}

static void pv_init(void)
{
    uint32_t total = ((uint32_t)(PV_POOL_TOP - PV_POOL_START) - PV_POOL_GUARD) & ~(uint32_t)7;
    pv_beg = PV_POOL_START;
    pv_end = PV_POOL_START + total - PV_HDR;       /* the sentinel occupies the last header */
    pv_set(pv_beg, total - PV_HDR, 1);             /* one free block from pv_beg to the sentinel */
    pv_set(pv_end, PV_HDR, 0);
    pv_up = 1;
}

void *malloc(size_t n)
{
    uint8_t *b;
    uint32_t need;

    if (!pv_up)
        pv_init();
    if (n == 0)
        n = 1;
    need = (uint32_t)n + PV_HDR + PV_FTR;
    need = (need + 7u) & ~(uint32_t)7;
    if (need < PV_MIN_BLK)
        need = PV_MIN_BLK;

    for (b = pv_beg; b != pv_end; b = pv_next(b)) {
        bhdr_t *h = (bhdr_t *)b;
        if (!h->free || h->size < need)
            continue;
        if (h->size - need >= PV_MIN_BLK) {
            /* split: the tail starts where this block's payload part ENDS (b + need) and stays
             * free. pv_next(b) is b + h->size - the block AFTER b (the sentinel, for the whole
             * arena, which is the very first allocation p8_init() makes): a header and footer
             * written there label the sentinel as a huge free block, its footer lands outside
             * the pool, and the scan then walks into the middle of allocated memory (on the
             * device: a bus-fault report and a reset, or a watchdog reset in the scan). */
            pv_set((uint8_t *)b + need, h->size - need, 1);
            pv_set(b, need, 0);
        } else {
            pv_set(b, h->size, 0);                 /* too small to split: hand it over whole */
        }
        return b + PV_HDR;
    }
    return NULL;
}

void free(void *p)
{
    uint8_t *b, *next;

    if (!p)
        return;
    b = (uint8_t *)p - PV_HDR;
    pv_set(b, ((bhdr_t *)b)->size, 1);

    next = pv_next(b);
    if (next != pv_end && ((bhdr_t *)next)->free)  /* merge forward */
        pv_set(b, ((bhdr_t *)b)->size + ((bhdr_t *)next)->size, 1);

    if (b != pv_beg) {                             /* merge backward (the footer makes this exact) */
        uint32_t prev = *(uint32_t *)(b - PV_FTR);
        bhdr_t *pb = (bhdr_t *)(b - prev);
        if (pb->free)
            pv_set(pb, pb->size + ((bhdr_t *)b)->size, 1);
    }
}

void *realloc(void *p, size_t n)
{
    uint8_t *b, *next;
    uint32_t cap, need, total;
    void *q;
    size_t copy;

    if (!p)
        return malloc(n);
    if (n == 0) {
        free(p);
        return NULL;
    }
    b = (uint8_t *)p - PV_HDR;
    cap = ((bhdr_t *)b)->size - PV_HDR - PV_FTR;
    if (cap >= n)
        return p;
    /* Grow in place over a free block that follows. Lua grows tables and strings by doubling, so
     * without this every doubling has to hold the old block and the new one at once - the pool's
     * real peak for a cart, and what decides whether a cart fits the device at all. */
    need = (uint32_t)n + PV_HDR + PV_FTR;
    need = (need + 7u) & ~(uint32_t)7;
    if (need < PV_MIN_BLK)
        need = PV_MIN_BLK;
    next = pv_next(b);
    if (next != pv_end && ((bhdr_t *)next)->free
        && ((bhdr_t *)b)->size + ((bhdr_t *)next)->size >= need) {
        total = ((bhdr_t *)b)->size + ((bhdr_t *)next)->size;
        if (total - need >= PV_MIN_BLK) {
            pv_set((uint8_t *)b + need, total - need, 1);
            pv_set(b, need, 0);
        } else {
            pv_set(b, total, 0);
        }
        return p;
    }
    q = malloc(n);
    if (!q)
        return NULL;
    copy = cap < n ? cap : n;
    {   /* memcpy in a for: libc.a's memcpy is fine, but keep this file freestanding */
        uint8_t *d = q, *s = p;
        size_t i;
        for (i = 0; i < copy; i++)
            d[i] = s[i];
    }
    free(p);
    return q;
}

void *calloc(size_t nmemb, size_t size)
{
    size_t n = nmemb * size;
    void *p;
    if (size != 0 && n / size != nmemb)
        return NULL;
    p = malloc(n);
    if (p) {
        uint8_t *d = p;
        size_t i;
        for (i = 0; i < n; i++)
            d[i] = 0;
    }
    return p;
}
