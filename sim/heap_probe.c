/* SPDX-License-Identifier: GPL-3.0-only */
/* heap_probe.c - how much heap a cart needs, measured in the simulator.
 *
 * The device has ONE pool for the engine and Lua (app.ld's arena - .bss's end to the stack guard,
 * 414,184 B - minus the allocator's 1 KiB red zone), so "will this cart run on the SMK-37 Pro?" is
 * exactly "how high does that pool ever get?" - and the only honest way to answer it is to measure,
 * because a cart's Lua costs 10-20x its source size (a 45 KB script is charged half a megabyte by
 * this probe's cost model) and no static estimate survives a real game.
 *
 * tools/build_sim.py compiles the whole simulator with malloc/free/realloc/calloc renamed to the
 * four functions below - on top of femto8's own p8_emu.h mapping, exactly as the device has it -
 * so every allocation the engine and Lua make is counted, and nothing else is: emscripten's
 * runtime and the page's own JS keep using the real allocator.
 *
 * What is counted is what the DEVICE's pool would spend, not what this allocator happened to
 * return: a cart's memory is mostly many small Lua blocks, and femto8/stub/device/alloc.c's
 * boundary-tag heap spends an 8-byte header + 4-byte footer, rounded up to 8, on every one of
 * them. Each request is therefore charged n + 16 (see probe_hdr, sized to that same 16), which is
 * what makes the figure portable between this 32-bit wasm, the 32-bit device, and a 64-bit host -
 * where the same cart needs about twice as much, because Lua is pointer-heavy and its pointers are
 * 8 bytes there instead of 4.
 *
 * sim_heap_peak() is what the wasm reports to the page (web/sim/index.html shows it next to every
 * uploaded cart) and what sim_test.mjs asserts.
 *
 * The build is single-command (all sources, same flags), so the width of the rename is the
 * build's, not this file's: #undef here is what lets the probe call the REAL allocator. */
#undef malloc
#undef free
#undef realloc
#undef calloc
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define PROBE_MAGIC 0x50564831ull          /* "PVH1" */

/* One per allocation: what the device's heap would spend on its own bookkeeping. 16 bytes is its
 * 8 + 4 rounded up to 8, i.e. the cost of any block, which is why this is also the size of the
 * header this allocator returns. */
typedef struct { uint64_t magic; uint64_t n; } probe_hdr;
#define PROBE_COST(n) ((long long)(sizeof(probe_hdr) + (n)))

static long long probe_used = 0;
static long long probe_peak = 0;

static probe_hdr *probe_hdr_of(void *p)
{
    probe_hdr *h;
    if (!p) return NULL;
    h = ((probe_hdr *)p) - 1;
    return h->magic == PROBE_MAGIC ? h : NULL;     /* not ours (emscripten's, the page's JS) */
}

void *sim_malloc(size_t n)
{
    probe_hdr *h = malloc(sizeof(probe_hdr) + n);
    if (!h) return NULL;
    h->magic = PROBE_MAGIC;
    h->n = n;
    probe_used += PROBE_COST(n);
    if (probe_used > probe_peak) probe_peak = probe_used;
    return h + 1;
}

void sim_free(void *p)
{
    probe_hdr *h = probe_hdr_of(p);
    if (!h) { free(p); return; }                   /* never counted, so never un-counted either */
    probe_used -= PROBE_COST(h->n);
    h->magic = 0;
    free(h);
}

void *sim_realloc(void *p, size_t n)
{
    probe_hdr *h;
    if (!p) return sim_malloc(n);                  /* Lua allocates through realloc(NULL, ..) */
    h = probe_hdr_of(p);
    if (!h) return realloc(p, n);
    if (n == 0) { sim_free(p); return NULL; }
    h = realloc(h, sizeof(probe_hdr) + n);
    if (!h) return NULL;                           /* the old block is still allocated and counted */
    probe_used += PROBE_COST(n) - PROBE_COST(h->n);
    h->magic = PROBE_MAGIC;
    h->n = n;
    if (probe_used > probe_peak) probe_peak = probe_used;
    return h + 1;
}

void *sim_calloc(size_t n, size_t size)
{
    size_t total = n * size;
    void *p = sim_malloc(total);
    if (p) memset(p, 0, total);
    return p;
}

#if defined(__EMSCRIPTEN__)
#define EXPORT __attribute__((used, visibility("default")))
#else
#define EXPORT __attribute__((used))
#endif

/* The high-water mark since sim_heap_reset(), i.e. what the device's pool would have to hold.
 * Returned as a 32-bit count: the pool is 414,184 B, so anything that reaches 4 GiB is a bug -
 * and an i64 would reach the page as a BigInt, which mixes badly with JS numbers. */
EXPORT unsigned sim_heap_peak(void) { return (unsigned)probe_peak; }
EXPORT unsigned sim_heap_used(void) { return (unsigned)probe_used; }

/* Called by sim_init(): every boot (POWER-ON, or a cart commit rebooting the engine) starts a
 * fresh measurement at whatever the engine already holds, so the peak is the device's total. */
/* A fresh measurement: sim_init calls this on every boot, so a cart is judged on its own
 * run (peak restarts at what is live, and sim/device_alloc.c also clears its refusals). */
EXPORT void sim_heap_reset(void) { probe_peak = probe_used; }

/* This probe counts and never refuses anything itself (emscripten's allocator is only out of
 * memory if the whole wasm is), so the answer is always 0. It exists because the DEVICE's
 * allocator can refuse - sim/device_alloc.c runs it, and sim_test.mjs asserts the same zero
 * against both builds: a cart the device cannot serve must not be called device-runnable. */
EXPORT unsigned sim_heap_refused(void) { return 0u; }

/* 0: this is the cost model over emscripten's heap. sim/device_alloc.c says 1. */
EXPORT unsigned sim_device_alloc(void) { return 0u; }
