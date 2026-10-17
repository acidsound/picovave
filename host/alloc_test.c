/* SPDX-License-Identifier: GPL-3.0-only */
/* alloc_test.c - the host test for the DEVICE's allocator (femto8/stub/device/alloc.c).
 *
 * Why this file exists: the device's malloc/free/realloc/calloc is the only allocator the SMK-37
 * Pro ever runs, and until 2026-10-17 nothing tested it. sim/heap_probe.c measures what a cart
 * WOULD need and allocates from emscripten's heap; host/preview.c links the host's libc; the wasm
 * page runs the probe too. So a bug in the allocator was invisible to every test the project had,
 * and the one that shipped - malloc() splitting a free block by writing the remainder's header at
 * pv_next(b), the block AFTER the one being split, instead of at b + need - labelled the 8-byte
 * sentinel as a 285 KB free block and wrote its footer outside the pool on the engine's very first
 * allocation. On hardware that is a bus fault or a watchdog reset on every boot, twice, and then
 * the bootguard's recovery mode: exactly the "flashing picovave.fwsc lands in recovery" report,
 * with the whole local suite green.
 *
 * Run it with tools/verify_final.py, or directly:
 *     cc -O1 -o build/alloc_test host/alloc_test.c && ./build/alloc_test
 * The arena is the device's own POOL (app.ld: 0x01C2C000 + 0x48000 = 294,912 B minus the 1 KiB red
 * zone), so the sizes and the peak it reports are the device's.
 *
 * What it checks, beyond "it did not crash":
 *   - the whole-arena block splits exactly once and the walk still reaches the sentinel
 *   - every block's size is 8-aligned and >= PV_MIN_BLK, and its footer equals its header
 *   - the blocks tile the arena with no gap and no overlap (their sizes sum to the arena)
 *   - no two free blocks are ever adjacent (coalescing is complete in both directions)
 *   - no block ever extends past the red zone
 *   - payload bytes written by the caller are never touched by the allocator, including across
 *     realloc (which may now grow in place)
 *   - calloc returns zeroed memory; a full free makes the whole arena allocatable again */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ARENA 294912u                   /* app.ld: POOL 0x01C2C000 + 0x48000 */
#define CANARY 4096u                    /* after the pool: a write here is a bug even if the walk lives */

static uint8_t arena[ARENA + CANARY] __attribute__((aligned(8)));
#define PV_POOL_START arena             /* alloc.c's seam: this test's arena, not the linker's POOL */
#define PV_POOL_TOP  (arena + ARENA)
#include "../femto8/stub/device/alloc.c"

#define GUARD    PV_POOL_GUARD
#define USABLE   (ARENA - GUARD)        /* what the pool really serves: 293,888 B */
#define NHAND    512
#define CANARY_BYTE 0x5A

static struct { uint8_t *p; uint32_t n; uint8_t pat; } h[NHAND];
static int fails;
static long in_place_grows;

static void fail(const char *what, long got, long want)
{
    if (fails++ < 20)
        printf("  FAIL %s (got %ld, expected %ld)\n", what, got, want);
}

/* the sentinel: the last header, never allocated, terminates every scan */
static uint8_t *sentinel(void) { return PV_POOL_START + (USABLE - PV_HDR); }

/* the biggest contiguous free block: an allocation of this size must succeed */
static uint32_t largest_free(void)
{
    uint8_t *b = PV_POOL_START, *end = sentinel();
    uint32_t best = 0;
    long n = 0;
    while (b != end && n++ < 1000000) {
        bhdr_t *hd = (bhdr_t *)b;
        if (hd->free && hd->size - PV_HDR - PV_FTR > best)
            best = hd->size - PV_HDR - PV_FTR;
        b = pv_next(b);
    }
    return best;
}

/* walk the chain and check every invariant the allocator depends on */
static void check_heap(const char *where)
{
    uint8_t *b = PV_POOL_START, *end = sentinel();
    uint32_t sum = PV_HDR;              /* the sentinel's own header */
    long n = 0;
    int free_prev = 0;

    while (b != end) {
        bhdr_t *hd = (bhdr_t *)b;
        if (n++ > 1000000) { fail("the block walk does not terminate", 0, 0); return; }
        if (hd->size < PV_MIN_BLK) { fail("a block is smaller than PV_MIN_BLK", hd->size, PV_MIN_BLK); return; }
        if (hd->size & 7u) { fail("a block size is not a multiple of 8", hd->size, 0); return; }
        if (*pv_ftr(b) != hd->size) { fail("a block's footer does not match its header", *pv_ftr(b), hd->size); return; }
        if ((uint32_t)(b - PV_POOL_START) + hd->size > USABLE) {
            fail("a block extends past the pool's red zone", (long)(b - PV_POOL_START) + hd->size, USABLE);
            return;
        }
        if (hd->free && free_prev) { fail("two free blocks are adjacent (no coalescing)", (long)(b - PV_POOL_START), 0); return; }
        free_prev = hd->free ? 1 : 0;
        sum += hd->size;
        b = pv_next(b);
    }
    if (b != end) { fail("the walk did not reach the sentinel", (long)(b - PV_POOL_START), (long)(end - PV_POOL_START)); return; }
    if (sum != USABLE) { fail("the blocks do not tile the arena", sum, USABLE); return; }
    {   /* the canary after the pool must never be touched */
        uint32_t i;
        for (i = 0; i < CANARY; i++)
            if (arena[ARENA + i] != CANARY_BYTE) { fail("a write landed past the pool", (long)i, CANARY_BYTE); return; }
    }
    (void)where;
}

static void touch(uint8_t *p, uint32_t n, uint8_t pat)
{
    uint32_t i;
    for (i = 0; i < n; i++) p[i] = (uint8_t)(pat + i);
}
static int intact(const uint8_t *p, uint32_t n, uint8_t pat)
{
    uint32_t i;
    for (i = 0; i < n; i++) if (p[i] != (uint8_t)(pat + i)) return (int)i + 1;
    return 0;
}

/* The regression case, on its own: the FIRST allocation takes the whole-arena free block, so it
 * has to split it at b + need. Split at pv_next(b) instead and the sentinel is relabelled (the
 * walk then runs into allocated memory) and a footer is written far outside the pool. */
static void first_allocation_split(void)
{
    /* what malloc has to turn 8192 into: header + footer, rounded up to 8 */
    const uint32_t need = ((8192u + PV_HDR + PV_FTR) + 7u) & ~(uint32_t)7;
    uint8_t *p = malloc(8192);
    bhdr_t *b0, *tail;

    if (!p) { fail("the first 8 KiB allocation failed", 0, 1); return; }
    b0 = (bhdr_t *)(p - PV_HDR);
    tail = (bhdr_t *)pv_next(b0);
    if (b0->free || b0->size != need)
        fail("the first allocation did not take exactly its rounded size", b0->size, need);
    if ((uint8_t *)tail != (uint8_t *)b0 + need)
        fail("the split left the remainder at the wrong address", (long)((uint8_t *)tail - (uint8_t *)b0), need);
    if (!tail->free || tail->size != (USABLE - PV_HDR) - need)
        fail("the remainder is not the rest of the arena", tail->size, (USABLE - PV_HDR) - need);
    check_heap("first_allocation_split");
    free(p);
    check_heap("after freeing the first allocation");
    {   /* ...and the arena must be one free block again */
        bhdr_t *b = (bhdr_t *)PV_POOL_START;
        if (!b->free || b->size != USABLE - PV_HDR)
            fail("the arena did not coalesce back into one block", b->size, USABLE - PV_HDR);
    }
}

static int stress(unsigned seed, long iters)
{
    long i;
    uint32_t maxused = 0, used = 0, cap = 32768;
    srand(seed);
    fails = 0;
    in_place_grows = 0;
    { void *q = malloc(1); free(q); }              /* the arena is initialised on the first malloc */

    for (i = 0; i < iters; i++) {
        int k = rand() % NHAND;
        int op = rand() % 100;
        if (op < 45) {                             /* malloc */
            if (!h[k].p) {
                uint32_t n = 1u + (uint32_t)rand() % cap;
                uint8_t pat = (uint8_t)(rand() & 0xFF);
                uint8_t *p = malloc(n);
                if (p) {
                    if ((uintptr_t)p & 7u) fail("a payload is not 8-byte aligned", (long)((uintptr_t)p & 7u), 0);
                    if (p < PV_POOL_START + PV_HDR || p + n > PV_POOL_TOP - GUARD) fail("a payload is outside the pool", (long)(p - arena), n);
                    else { touch(p, n, pat); h[k].p = p; h[k].n = n; h[k].pat = pat; used += n; }
                }
                if (used > maxused) maxused = used;
            }
        } else if (op < 75) {                      /* free */
            if (h[k].p) {
                int r = intact(h[k].p, h[k].n, h[k].pat);
                if (r) fail("a payload was corrupted before its free", (long)(h[k].p - arena), r);
                free(h[k].p);
                used -= h[k].n;
                h[k].p = NULL;
                h[k].n = 0;
            }
        } else if (op < 90) {                      /* realloc */
            if (h[k].p) {
                uint32_t n = 1u + (uint32_t)rand() % cap;
                uint8_t pat = (uint8_t)(rand() & 0xFF);
                uint8_t *p = realloc(h[k].p, n);
                if (p) {
                    uint32_t keep = h[k].n < n ? h[k].n : n;
                    int r = intact(p, keep, h[k].pat);   /* what the caller wrote must survive */
                    if (r) fail("realloc lost payload bytes", (long)(p - arena), r);
                    if (p == h[k].p) in_place_grows++;
                    touch(p, n, pat);
                    used = used - h[k].n + n;
                    h[k].p = p; h[k].n = n; h[k].pat = pat;
                    if (used > maxused) maxused = used;
                }
            }
        } else if (op < 95) {                      /* calloc */
            if (!h[k].p) {
                uint32_t n = 1u + (uint32_t)rand() % 4096u;
                uint8_t *p = calloc(1, n);
                if (p) {
                    uint32_t j;
                    for (j = 0; j < n; j++) if (p[j]) { fail("calloc did not zero its memory", (long)j, p[j]); break; }
                    touch(p, n, 0);
                    h[k].p = p; h[k].n = n; h[k].pat = 0;
                    used += n;
                }
            }
        } else {                                   /* fill the pool, then give it all back */
            void *v[64];
            int m = 0;
            uint32_t before = largest_free();
            while (m < 64) { v[m] = malloc(4096u); if (!v[m]) break; memset(v[m], 0xAB, 4096u); m++; }
            if (!m && before >= 4096u) fail("no 4 KiB block although that much was free", before, 4096);
            while (m--) free(v[m]);
        }
        if ((i & 1023) == 0) { check_heap("stress"); if (fails) break; }
        if ((i & 4095) == 0) {
            int j;
            for (j = 0; j < NHAND; j++) {
                if (h[j].p && intact(h[j].p, h[j].n, h[j].pat)) { fail("a live payload was corrupted", (long)(h[j].p - arena), h[j].n); break; }
            }
            if (fails) break;
        }
    }
    while (fails == 0) {                           /* drain and check the final state */
        int j, done = 1;
        for (j = 0; j < NHAND; j++) if (h[j].p) { free(h[j].p); h[j].p = NULL; done = 0; }
        if (done) break;
    }
    check_heap("final");
    {   /* after a full free the whole pool must be available as one block again */
        void *big = malloc(USABLE - PV_HDR - 64);
        if (!big) fail("the pool could not be re-taken whole after a full free", 0, 1);
        else free(big);
    }
    check_heap("after re-taking the pool");
    printf("seed %-7u peak in flight %6u B of %u B, %5ld reallocs grew in place, %d failure(s)\n",
           seed, maxused, USABLE, in_place_grows, fails);
    return fails;
}

int main(void)
{
    unsigned seeds[] = {12345u, 1u, 7u, 999u, 31337u, 424242u};
    unsigned s;
    int bad = 0;

    setvbuf(stdout, NULL, _IOLBF, 0);   /* a wild write can abort this test: keep the progress */
    memset(arena + ARENA, CANARY_BYTE, CANARY);
    printf("device allocator (femto8/stub/device/alloc.c): arena %u B, red zone %u B, usable %u B\n",
           ARENA, GUARD, USABLE);

    first_allocation_split();
    if (fails) { printf("the split of the first free block is broken (this is the bug that shipped)\n"); return 1; }
    printf("the whole-arena block splits correctly and coalesces back\n");

    for (s = 0; s < sizeof seeds / sizeof seeds[0]; s++)
        bad |= stress(seeds[s], 200000) != 0;

    printf(bad ? "FAILED\n" : "all ok\n");
    return bad != 0;
}
