/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave_cart.h - the cart store and the SysEx cart-upload protocol.
 *
 * Shared by the device (src/picovave.c) and the simulator (sim/sim.c): the SAME parser runs on
 * both, so the simulator verifies the protocol the device will speak, and the device cannot
 * drift from what was tested. The target provides the store (three flat operations inside its
 * own region) and the reply path; the slot layout, the framing, the 7-bit fields, the state
 * machine and the CRC are all here.
 *
 * A cart is a .p8.png: the compressed form PICO-8 saves and the boards hand out, and the only
 * cart format picovave takes. What travels on the wire - and what a slot holds - is the COOKED
 * cart that image carries in the low two bits of its pixels (160x205, one byte per pixel):
 *   [0, 17152)       the cartridge ROM (gfx, map, sprite flags, music, sfx)
 *   [17152, 32800)   the code section: PXA ('\0pxa'), ':c:' mini, or raw text (pre-0.1.7 carts)
 * The PAGE unwraps the image (web/sim/p8png.mjs) and the device only decompresses that code
 * section, which femto8 already vendors (pico8_code_section_decompress; see p8_load_cooked in
 * femto8/src/p8_emu.c). Why not decode the PNG on the device: lodepng materialises the 160x205
 * label as 160*205*4 = 131,200 B of RGBA - more than the whole pool the engine and Lua share
 * (293,888 B), and at the time that was written the pool itself was 22,016 B.
 *
 * A text .p8 is NOT a cart here. gfx/map/sfx/music are hex in it, so it is 2-3x the size of the
 * compressed cart its author saved, and no real game fits a slot as text; two formats for one
 * cart is also exactly the confusion to avoid. .p8 -> .p8.png is PICO-8's own save.
 *
 * The cooked cart is BINARY, while SysEx data bytes are 7-bit: the payload is SPLIT 8 bits into
 * 7 (the packing the package protocol uses too: 8 payload bits take 7/8 of a wire byte and the
 * leftovers spill into the next). `off`, `n` and the CRC are RAW counts, not wire bytes.
 *
 * Wire format:
 *   host -> device   F0 22 24 35 7E <cmd> <args...> F7
 *     01 BEGIN <slot> <len:3> <namelen> <name...>   start an upload into a slot
 *     02 DATA  <off:3> <n:3> <packed...>            n payload bytes (8->7 packed) at off
 *     03 END   <crc:3>                              verify the CRC, commit, and run the slot
 *     05 ERASE <slot>                               empty the slot
 *     06 LIST                                       ask for the slots
 *     (04 is unused: an upload runs what it wrote, and the older slot is a rollback.)
 *   device -> host   F0 22 24 35 7E 10 OK <code> F7
 *                    F0 22 24 35 7E 11 ERR <code> F7
 *                    F0 22 24 35 7E 12 INFO <slot> <len:3> <namelen> <name...> F7
 * <x:n> is n 7-bit bytes, little-endian; len/off are 24-bit, so a cart smaller than 16 MiB fits.
 * A 16-bit CRC needs THREE 7-bit bytes (2 would carry only 14 bits). A DATA frame carries exactly
 * ceil(8n/7) packed bytes for its n payload bytes, so a host that gets the packing wrong is told
 * so, instead of being silently mis-read. The 22 24 35 identity is the
 * firmware's own (the updater and the bootloader share it); 7E is the cart command, which neither
 * uses. BEGIN's len must be exactly PV_CART_LEN (a magic, then one cooked cart) and a slot whose
 * payload is not that is ignored rather than run, so a slot written by another format cannot
 * masquerade as a cart.
 */
#pragma once
#include <stdint.h>
#include <string.h>

/* ---- the store --------------------------------------------------------------
 * The device keeps carts in its own flash region - the SLOOP store at 0x9D000..0xBB000, which a
 * plain firmware update never touches, so an uploaded cart survives reflashing. The simulator
 * keeps the same layout in a RAM array, so both run this code over the same offsets.
 *
 * ONE cart at a time: the region is 120 KiB and it holds a single slot, so a cart may use the
 * whole store. That is what a precompiled cart needs - the bytecode plus the 17,152-byte ROM is
 * 50-110 KB, where the cooked form is 32,800 B (two 61,440-byte slots could not hold it). The
 * trade is the rollback slot: an upload now erases the cart that is playing, so a power cut in
 * the middle of one leaves the store empty. The boot ladder still ends on the embedded default
 * cart, so the panel never goes dark - the floor moved from "the cart it replaced" to "the cart
 * the firmware ships with".
 *
 * The region is 120 KiB and the flash's erase unit is 4 KiB:
 *   slot 0  0x0000..0x1E000  (122,880 B = 30 sectors)
 * A slot is a 64-byte header then the payload: a 4-byte magic and either the 32,800-byte cooked
 * cart or the precompiled form below. BEGIN erases the whole slot before writing, so it is
 * written whole. pv_run_next() below is what the app drives when the engine refuses the cart:
 * the slot first, then the embedded default cart. That ladder matters because a cart is not
 * always one the device can RUN (its Lua costs 10-20x its source), and a refused cart must not
 * leave the panel dark until the next USB upload. */
#define PV_STORE_SIZE   (120u * 1024u)                 /* 0x9D000..0xBB000 */
#define PV_SLOT_COUNT   1u                             /* one cart at a time: see above */
#define PV_SLOT_SIZE    (PV_STORE_SIZE / PV_SLOT_COUNT)
#define PV_SLOT_BASE(s) ((uint32_t)(s) * PV_SLOT_SIZE)
#define PV_SLOT_DATA    64u                            /* payload offset inside a slot */
#define PV_COOKED_SIZE  32800u                         /* 160*205: the cooked cart a .p8.png carries */
#define PV_COOKED_MAGIC "PVC1"                         /* the payload's first four bytes */
#define PV_COOKED_MAGIC_LEN 4u
#define PV_CART_LEN     (PV_COOKED_MAGIC_LEN + PV_COOKED_SIZE)   /* 32,804: a cooked cart payload */

/* ---- precompiled carts ------------------------------------------------------
 * The page can compile a cart's Lua and upload the bytecode instead of the source, so the device
 * never runs the compiler and never materialises the source buffer (p8_emu.c's
 * p8_load_bytecode()). The payload is:
 *   [0,4)                 "PVB1"
 *   [4, 4+17152)          the cartridge ROM, verbatim - the same 17,152 bytes a cooked cart starts
 *                         with, because gfx/map/sfx live there and the engine reads them in place
 *   [4+17152, +4)         u32 bc_len, little-endian: the bytecode length that follows
 *   [.., len)             the bytecode (Lua's own dump, produced by the same Lua the device runs)
 * A slot holds PV_SLOT_SIZE - PV_SLOT_DATA = 61,376 payload bytes, so bc_len <= 44,216. The page
 * uploads the cooked cart instead when a cart's bytecode does not fit (nothing regresses: that is
 * the path every cart takes today). */
#define PV_ROM_SIZE     17152u                         /* = femto8's CART_MEMORY_SIZE (0x4300) */
#define PV_BCODE_MAGIC  "PVB1"
#define PV_BC_HDR       (PV_COOKED_MAGIC_LEN + PV_ROM_SIZE + 4u)   /* 17,160: magic + ROM + bc_len */
#define PV_SLOT_PAYLOAD (PV_SLOT_SIZE - PV_SLOT_DATA)             /* 61,376 */
#define PV_BC_MAX       (PV_SLOT_PAYLOAD - PV_BC_HDR)             /* 44,216 */
#define PV_DATA_MAX     512u                           /* payload bytes one DATA frame may carry */
#define PV_SECTOR       4096u                          /* the flash erase unit; slots sit on these */
#define PV_NAME_MAX     31u
#define PV_SLOT_MAGIC   0x4356u                        /* 'V','C' */

/* Reply codes. */
enum { PV_C_BEGIN = 1, PV_C_DATA = 2, PV_C_END = 3, PV_C_ERASE = 5, PV_C_LIST = 6 };
enum { PV_E_NOBEGIN = 1, PV_E_CRC = 2, PV_E_RANGE = 3, PV_E_BIG = 4, PV_E_EMPTY = 5, PV_E_SLOT = 6,
       PV_E_FMT = 7 };

/* Provided by the target. Offsets are within the store, [0, PV_STORE_SIZE). */
int  hh_store_read (uint32_t off, void *dst, uint32_t n);
int  hh_store_erase(uint32_t off, uint32_t n);              /* erase the sectors covering [off, off+n) */
int  hh_store_write(uint32_t off, const void *src, uint32_t n);
void hh_cart_reply (const uint8_t *msg, uint32_t n);        /* a full F0..F7 frame, to send back */

typedef struct {
    uint16_t magic;
    uint16_t name_len;
    uint32_t len;
    uint32_t seq;             /* the newest seq wins on boot */
    uint16_t crc;
    char     name[PV_NAME_MAX + 1];
} pv_slot_hdr_t;

typedef struct {
    int      active;          /* slot index, or -1 -> the embedded default */
    uint32_t active_len;
    uint8_t  slot_kind[PV_SLOT_COUNT];   /* 0 cooked cart, 1 precompiled bytecode */
    uint32_t slot_len[PV_SLOT_COUNT];    /* payload length as stored */
    int      up;              /* an upload is in progress */
    uint8_t  up_slot;
    uint32_t up_len, up_off;
    uint16_t up_crc;
    uint8_t  up_name_len;
    char     up_name[PV_NAME_MAX + 1];
    int      changed;         /* this message committed/erased: the app must reboot */
    uint32_t replies;
    uint8_t  last_cmd, last_code;
} pv_cart_t;

static pv_cart_t pv_cart;

static uint16_t pv_crc16(uint16_t crc, const uint8_t *p, uint32_t n)
{
    uint32_t i;
    int k;
    for (i = 0; i < n; i++) {
        crc ^= (uint16_t)((uint16_t)p[i] << 8);
        for (k = 0; k < 8; k++)
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
    }
    return crc;
}

static uint32_t pv_get7(const uint8_t *p, int n)
{
    uint32_t v = 0;
    int i;
    for (i = 0; i < n; i++)
        v |= (uint32_t)(p[i] & 0x7fu) << (7 * i);
    return v;
}

/* Undo the 8->7 packing: n raw bytes come back from ceil(8n/7) wire bytes. Returns n, or -1 when
 * the wire length is not exactly what n implies - a host that packs wrongly is told, not guessed
 * at. (pack7 in web/sim/fm1ota.mjs is the exact inverse.) */
static int pv_unpack7(uint8_t *dst, uint32_t n, const uint8_t *src, uint32_t sn)
{
    uint32_t i, acc = 0, nb = 0, k = 0;
    if (sn != (n * 8u + 6u) / 7u)
        return -1;
    for (i = 0; i < sn && k < n; i++) {
        acc |= (uint32_t)(src[i] & 0x7fu) << nb;
        nb += 7u;
        if (nb >= 8u) { dst[k++] = (uint8_t)(acc & 0xffu); acc >>= 8; nb -= 8u; }
    }
    return k == n ? (int)n : -1;
}

static void pv_put7(uint8_t *p, int n, uint32_t v)
{
    int i;
    for (i = 0; i < n; i++)
        p[i] = (uint8_t)((v >> (7 * i)) & 0x7fu);
}

static void pv_send(uint8_t cmd, const uint8_t *body, uint32_t n)
{
    uint8_t m[PV_NAME_MAX + 16];
    uint32_t k = 0, i;
    if (n > sizeof m - 6u)
        n = sizeof m - 6u;
    m[k++] = 0xF0; m[k++] = 0x22; m[k++] = 0x24; m[k++] = 0x35; m[k++] = 0x7E; m[k++] = cmd;
    for (i = 0; i < n; i++)
        m[k++] = body[i];
    m[k++] = 0xF7;
    hh_cart_reply(m, k);
    pv_cart.replies++;
    pv_cart.last_cmd = cmd;
    pv_cart.last_code = n ? body[0] : 0;
}

static void pv_ok(uint8_t code)  { uint8_t b[1] = { code }; pv_send(0x10, b, 1); }
static void pv_err(uint8_t code) { uint8_t b[1] = { code }; pv_send(0x11, b, 1); }

/* A slot header is only a cart if the length and the payload's magic say so: a slot written by
 * an older format (or anything else) is skipped, so it can never be run as a cart. */
static int pv_payload_kind(uint8_t slot, uint32_t len)
{
    uint8_t m[PV_COOKED_MAGIC_LEN];
    /* A bytecode payload can be SMALLER than a cooked cart (flipside: 27,157 B), so the lower
     * bound is the bytecode header, not PV_CART_LEN; the magic below decides which it is. */
    if (len <= PV_BC_HDR || len > PV_SLOT_PAYLOAD)
        return -1;
    if (hh_store_read(PV_SLOT_BASE(slot) + PV_SLOT_DATA, m, sizeof m) != 0)
        return -1;
    if (memcmp(m, PV_COOKED_MAGIC, sizeof m) == 0)
        return len == PV_CART_LEN ? 0 : -1;               /* 0 = cooked cart */
    if (memcmp(m, PV_BCODE_MAGIC, sizeof m) == 0) {       /* 1 = precompiled bytecode */
        uint8_t b[4];
        uint32_t bc;
        if (len <= PV_BC_HDR)
            return -1;
        if (hh_store_read(PV_SLOT_BASE(slot) + PV_SLOT_DATA + PV_BC_HDR - 4u, b, 4) != 0)
            return -1;
        bc = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
        if (bc == 0 || bc > PV_BC_MAX || bc != len - PV_BC_HDR)
            return -1;
        return 1;
    }
    return -1;
}

static int pv_hdr_read(uint8_t slot, pv_slot_hdr_t *h)
{
    if (slot >= PV_SLOT_COUNT)
        return -1;
    if (hh_store_read(PV_SLOT_BASE(slot), h, sizeof *h) != 0)
        return -1;
    if (h->magic != PV_SLOT_MAGIC)
        return -1;
    if (h->name_len > PV_NAME_MAX || h->name[h->name_len] != 0)
        return -1;
    return pv_payload_kind(slot, h->len) < 0 ? -1 : 0;
}

static uint32_t pv_seq_newest(void)
{
    pv_slot_hdr_t h;
    uint32_t best = 0;
    uint8_t slot;
    for (slot = 0; slot < PV_SLOT_COUNT; slot++)
        if (pv_hdr_read(slot, &h) == 0 && h.seq > best)
            best = h.seq;
    return best;
}

/* Which slot runs: the valid one with the newest `seq`; else none, and the default cart runs. */
static void pv_run_reset(void);

static void pv_cart_init(void)
{
    pv_slot_hdr_t h;
    uint32_t best = 0;
    uint8_t slot;
    memset(&pv_cart, 0, sizeof pv_cart);
    pv_cart.active = -1;
    for (slot = 0; slot < PV_SLOT_COUNT; slot++)
        if (pv_hdr_read(slot, &h) == 0) {
            pv_cart.slot_kind[slot] = (uint8_t)pv_payload_kind(slot, h.len);
            pv_cart.slot_len[slot] = h.len;
            if (pv_cart.active < 0 || h.seq > best) {
                best = h.seq;
                pv_cart.active = slot;
                pv_cart.active_len = h.len;
            }
        }
    pv_run_reset();
}

/* ---- the rollback ladder ----------------------------------------------------
 * Which cart the app is being offered. -1 is the embedded default cart (nothing stored, or both
 * slots refused), PV_RUN_END is nothing left to try. pv_run_reset() starts the walk at the newest
 * valid slot, so the cart uploaded last is the one that runs; the app steps down with
 * pv_run_next() only when the engine could not take the cart it was given (out of memory on the
 * device - a cart's Lua costs 10-20x its source, and the pool is 288 KiB). Without the ladder a
 * slot the device cannot run would leave the panel dark until the next USB upload. */
#define PV_RUN_END (-2)
static int8_t pv_run_slot = PV_RUN_END;

static void pv_run_reset(void) { pv_run_slot = (int8_t)pv_cart.active; }

/* Step down one: the slot the newest replaced (if it is a cart at all), then the embedded default
 * cart, then nothing. Returns 1 when the cart hh_cart_data()/hh_cart_size() name changed. */
static int pv_run_next(void)
{
#if PV_SLOT_COUNT > 1
    pv_slot_hdr_t h;
#endif
    if (pv_run_slot == PV_RUN_END)
        return 0;
    if (pv_run_slot >= 0) {
#if PV_SLOT_COUNT > 1
        uint8_t other = (uint8_t)((pv_run_slot + 1) % (int)PV_SLOT_COUNT);
        if ((int)pv_run_slot == pv_cart.active && pv_hdr_read(other, &h) == 0) {
            pv_run_slot = (int8_t)other;           /* the cart the newest replaced */
            return 1;
        }
#endif
        pv_run_slot = -1;                          /* the embedded default cart */
        return 1;
    }
    pv_run_slot = PV_RUN_END;
    return 0;
}

static void pv_begin(const uint8_t *a, uint32_t an)
{
    uint8_t slot, nl;
    uint32_t len, i;
    if (an < 5)
        return pv_err(PV_E_RANGE);
    slot = a[0];
    if (slot >= PV_SLOT_COUNT)
        return pv_err(PV_E_SLOT);
    len = pv_get7(a + 1, 3);
    nl = a[4];
    if (nl > PV_NAME_MAX || an < 5u + nl)
        return pv_err(PV_E_RANGE);
    if (len != PV_CART_LEN && !(len > PV_BC_HDR && len <= PV_SLOT_PAYLOAD))
        return pv_err(PV_E_FMT);          /* a cooked cart, a bytecode payload, or nothing */
    if (hh_store_erase(PV_SLOT_BASE(slot), PV_SLOT_SIZE) != 0)
        return pv_err(PV_E_SLOT);
    pv_cart.up = 1;
    pv_cart.up_slot = slot;
    pv_cart.up_len = len;
    pv_cart.up_off = 0;
    pv_cart.up_crc = 0xFFFFu;
    pv_cart.up_name_len = nl;
    for (i = 0; i < nl; i++)
        pv_cart.up_name[i] = (char)a[5 + i];
    pv_cart.up_name[nl] = 0;
    pv_ok(PV_C_BEGIN);
}

static uint8_t pv_data_buf[PV_DATA_MAX];     /* one DATA frame, unpacked */

static void pv_data(const uint8_t *a, uint32_t an)
{
    uint32_t off, n;
    if (!pv_cart.up)
        return pv_err(PV_E_NOBEGIN);
    if (an < 6)
        return pv_err(PV_E_RANGE);
    off = pv_get7(a, 3);
    n = pv_get7(a + 3, 3);
    if (n == 0 || n > PV_DATA_MAX)
        return pv_err(PV_E_BIG);
    if (off != pv_cart.up_off || off + n > pv_cart.up_len)
        return pv_err(PV_E_RANGE);
    if (pv_unpack7(pv_data_buf, n, a + 6, an - 6) != (int)n)
        return pv_err(PV_E_RANGE);
    if (hh_store_write(PV_SLOT_BASE(pv_cart.up_slot) + PV_SLOT_DATA + off, pv_data_buf, n) != 0)
        return pv_err(PV_E_SLOT);
    pv_cart.up_crc = pv_crc16(pv_cart.up_crc, pv_data_buf, n);
    pv_cart.up_off += n;
    pv_ok(PV_C_DATA);
}

static void pv_end(const uint8_t *a, uint32_t an)
{
    pv_slot_hdr_t h;
    int kind;
    if (!pv_cart.up)
        return pv_err(PV_E_NOBEGIN);
    if (an < 3)                                   /* a 16-bit CRC needs three 7-bit bytes */
        return pv_err(PV_E_RANGE);
    if (pv_cart.up_off != pv_cart.up_len)
        return pv_err(PV_E_EMPTY);
    if ((uint16_t)pv_get7(a, 3) != pv_cart.up_crc) {
        pv_cart.up = 0;                       /* the payload is unmarked: a corrupt upload never runs */
        return pv_err(PV_E_CRC);
    }
    /* The CRC says the bytes are the ones the host sent; this says they are a cart at all. A
     * payload whose magic and length disagree is refused here, so a slot can never hold something
     * the loader would then misread. */
    kind = pv_payload_kind(pv_cart.up_slot, pv_cart.up_len);
    if (kind < 0) {
        pv_cart.up = 0;
        return pv_err(PV_E_FMT);
    }
    memset(&h, 0, sizeof h);
    h.magic = PV_SLOT_MAGIC;
    h.name_len = pv_cart.up_name_len;
    h.len = pv_cart.up_len;
    h.seq = pv_seq_newest() + 1;              /* newest wins, so this slot boots next */
    h.crc = pv_cart.up_crc;
    memcpy(h.name, pv_cart.up_name, pv_cart.up_name_len);
    if (hh_store_write(PV_SLOT_BASE(pv_cart.up_slot), &h, sizeof h) != 0) {
        pv_cart.up = 0;
        return pv_err(PV_E_SLOT);
    }
    pv_cart.slot_kind[pv_cart.up_slot] = (uint8_t)kind;
    pv_cart.slot_len[pv_cart.up_slot] = h.len;
    pv_cart.active = pv_cart.up_slot;
    pv_cart.active_len = h.len;
    pv_cart.up = 0;
    pv_cart.changed = 1;
    pv_run_reset();                           /* the just-committed slot runs, ladder reset */
    pv_ok(PV_C_END);
}

static void pv_erase(const uint8_t *a, uint32_t an)
{
    uint8_t slot;
    if (an < 1)
        return pv_err(PV_E_RANGE);
    slot = a[0];
    if (slot >= PV_SLOT_COUNT)
        return pv_err(PV_E_SLOT);
    hh_store_erase(PV_SLOT_BASE(slot), PV_SLOT_SIZE);
    pv_cart.slot_kind[slot] = 0;
    pv_cart.slot_len[slot] = 0;
    if (pv_cart.active == (int)slot) {
        pv_cart.active = -1;
        pv_cart.active_len = 0;
        pv_cart.changed = 1;
    }
    pv_run_reset();                           /* erasing the running slot re-runs the other one */
    pv_ok(PV_C_ERASE);
}

static void pv_list(void)
{
    uint8_t slot;
    for (slot = 0; slot < PV_SLOT_COUNT; slot++) {
        pv_slot_hdr_t h;
        uint8_t b[PV_NAME_MAX + 8];
        uint32_t k = 0;
        if (pv_hdr_read(slot, &h) != 0)
            continue;
        b[k++] = slot;
        pv_put7(b + k, 3, h.len); k += 3;
        b[k++] = h.name_len;
        memcpy(b + k, h.name, h.name_len); k += h.name_len;
        pv_send(0x12, b, k);
    }
    pv_ok(PV_C_LIST);
}

/* Feed one F0..F7 frame. Returns 1 when the app must reboot (a slot was committed/erased). */
static int pv_cart_sysex(const uint8_t *msg, uint32_t n)
{
    pv_cart.changed = 0;
    if (n < 7 || msg[0] != 0xF0 || msg[n - 1] != 0xF7)
        return 0;
    if (msg[1] != 0x22 || msg[2] != 0x24 || msg[3] != 0x35 || msg[4] != 0x7E)
        return 0;
    switch (msg[5]) {
    case 0x01: pv_begin(msg + 6, n - 7); break;
    case 0x02: pv_data(msg + 6, n - 7); break;
    case 0x03: pv_end(msg + 6, n - 7); break;
    case 0x05: pv_erase(msg + 6, n - 7); break;
    case 0x06: pv_list(); break;
    default: return 0;
    }
    return pv_cart.changed;
}
