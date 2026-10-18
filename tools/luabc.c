/* SPDX-License-Identifier: GPL-3.0-only */
/* luabc.c - the cart compiler the PAGE runs, built from the DEVICE's own Lua.
 *
 * Why this exists: a cart is Lua source that the device compiles on every boot (p8_emu.c's
 * p8_materialize_script -> p8_lua.c's lua_init_script -> luaL_loadbuffer). That compile costs
 * the pool ~150 KB at its peak (baba_demake-9: 152,517 B) and ~12 ms, and the source buffer
 * another 45 KB. Compiling it once, on the host, and uploading the bytecode instead removes both
 * from the device's load path.
 *
 * The bytecode MUST be produced by the same Lua the device runs: same sources (femto8/src/lua),
 * same luaconf (LUA_NUMBER = fix32_t), same parser patches. That is why this links the vendored
 * tree rather than a stock Lua. The dump header records sizeof(int), sizeof(size_t),
 * sizeof(Instruction), sizeof(lua_Number) and the endianness, and the device's luaU_undump checks
 * them (lundump.c LoadHeader) - so a wasm32 build is accepted exactly when those four are 4 and
 * the byte order is little-endian, which is what the device has.
 *
 * Debug info is stripped (luaU_dump's strip=1): lineinfo alone is 4 B per instruction and the
 * device has no console to print a Lua line number to. Measured: 50-58% of the chunk.
 *
 * Exports (see web/sim/luabc.mjs):
 *   luabc_in()                  writable buffer for the 32,800-byte cooked cart
 *   luabc_out()                 readable buffer the bytecode is written to
 *   luabc_compile(n)            cooked cart of n bytes -> bytecode length, or -1..-3
 *   luabc_error()               the Lua error message when luabc_compile() < 0
 */
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "lua.h"
#include "lauxlib.h"
#include "lstate.h"
#include "lobject.h"
#include "lundump.h"

#define CART_ROM_SIZE 0x4300   /* p8_emu.h: CART_MEMORY_SIZE - the cartridge ROM half */
#define IN_MAX  (32800)
#define OUT_MAX (192 * 1024)

static unsigned char g_in[IN_MAX];
static unsigned char g_out[OUT_MAX];
static char g_err[160];

struct wbuf { size_t n; int over; };

static int writer(lua_State *L, const void *p, size_t sz, void *ud)
{
    (void)L;
    struct wbuf *w = (struct wbuf *)ud;
    if (w->n + sz > OUT_MAX) { w->over = 1; return 1; }
    memcpy(g_out + w->n, p, sz);
    w->n += sz;
    return 0;
}

__attribute__((used, visibility("default"))) unsigned char *luabc_in(void) { return g_in; }
__attribute__((used, visibility("default"))) unsigned char *luabc_out(void) { return g_out; }
__attribute__((used, visibility("default"))) const char *luabc_error(void) { return g_err; }

/* The code section starts after the 17,152-byte cartridge ROM; pico8_code_section_decompress()
 * is the device's own expander (PXA / ':c:' / raw), so the page and the device agree on what the
 * script is. */
/* Does the chunk we just produced load? Same call the device makes (mode "b"), same Lua. */
__attribute__((used, visibility("default"))) int luabc_verify(int n)
{
    lua_State *L = luaL_newstate();
    int st;
    g_err[0] = 0;
    if (!L) return -1;
    st = luaL_loadbufferx(L, (const char *)g_out, (size_t)n, "@cart", "b");
    if (st) {
        const char *msg = lua_tostring(L, -1);
        if (msg) { strncpy(g_err, msg, sizeof g_err - 1); g_err[sizeof g_err - 1] = 0; }
    }
    lua_close(L);
    return st;
}

__attribute__((used, visibility("default"))) int luabc_compile(int cooked_len)
{
    extern int pico8_code_section_decompress(uint8_t *in, uint8_t *out, int max);
    static char src[0x10001];

    g_err[0] = 0;
    if (cooked_len <= CART_ROM_SIZE || cooked_len > IN_MAX) return -1;

    pico8_code_section_decompress(g_in + CART_ROM_SIZE, (uint8_t *)src, 0x10000);
    size_t len = strnlen(src, 0x10000);
    if (len == 0) return -2;

    lua_State *L = luaL_newstate();
    if (!L) return -3;
    int st = luaL_loadbuffer(L, src, len, "@cart");
    if (st) {
        const char *msg = lua_tostring(L, -1);
        if (msg) { strncpy(g_err, msg, sizeof g_err - 1); g_err[sizeof g_err - 1] = 0; }
        lua_close(L);
        return -3;
    }
    /* strip = 1: lineinfo/locvars/upvalue names are 50-58% of the chunk and this device has no
     * console to print a line number to. The device's LoadDebug reads the zero counts. */
    struct wbuf w = { 0, 0 };
    int d = luaU_dump(L, clLvalue(L->top - 1)->p, writer, &w, 1);
    lua_close(L);
    if (d || w.over) return -4;
    return (int)w.n;
}
