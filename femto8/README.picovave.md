# femto8 (vendored) — picovave

The PICO-8 engine that picovave runs, vendored into the picovave tree.

- Upstream: femto8 (bbaker / Chris January, `femto8`), **pin `f9e5097c882d0c9203616689f3de5531e0597c43`** ("Fix Windows build").
- License: see `LICENSE` (MIT). The Lua engine under `src/lua/` is Lua 5.4 (MIT); `src/lodepng/` is LodePNG (zlib, **not built** — see below).
- What is kept: the engine (`p8_emu.c`, `p8_lua.c`, `p8_parser.c`, `p8_input.c`,
  `p8_audio.c`, `p8_dsp.c`, `p8_cstore.c`, `queue.c`, `strtcpy.c`), the embedded
  Lua 5.4 (`src/lua/`), the cart codec (`src/lexaloffle/`), and the data headers
  (`src/data/`). Headers for the dropped GUI files are kept so the engine compiles.
- What is **not built**: `main.c`, the SDL/desktop GUI (`p8_browse.c`,
  `p8_dialog.c`, `p8_options.c`, `p8_pause_menu.c`), and `lodepng`.
  `stub/` provides no-op dialogs / pause menu and a failing LodePNG so the tree
  links without a window, without a file browser, and without a PNG decoder —
  picovave's carts are `.p8.png`, but the **page** decodes the image (the low two bits of
  each 160x205 pixel are the cart) and hands the device the 32,800-byte cooked cart, so
  neither the device nor the wasm needs a PNG decoder.

## The two seams

1. `seam/` — the small platform surface the `__DA1470x__` (= `OS_FREERTOS`) branch
   of `p8_emu.c` asks for: `gdi.h` (the RGB565 layer), `FreeRTOS.h`/`task.h`/
   `semphr.h`, `retro_heap.h` (`rh_malloc`/`rh_free`), `ble_controller.h`
   (`gamepad`), `pthread.h`. Each target supplies the implementation, one file
   each: the device over its panel + `POOL` arena (`seam/seam_device.c`, with the
   mini-libc and allocator in `stub/device/`), the simulator over its wasm panel
   (`seam/seam_sim.c`), and the host preview over a capture buffer
   (`../host/seam_host.c`). The engine and the shared app layer
   (`../src/picovave_p8.h`) are compiled unchanged by all three.
   `seam/device/` is a **device-only** include directory (added to the ROM build's
   `-I` list, never the simulator's): `dirent.h` there replaces newlib's, which is
   "#error not supported" on pi32v2, so `p8_lua.c`'s cart listing compiles. The
   device's `opendir()` reports no directory, so the browser finds nothing (there is
   no filesystem: the cart lives in the SLOOP store).
2. `stub/` — the no-window GUI and the LodePNG replacement.

## Patches to the vendored sources (documented, minimal)

All are marked with `picovave:` comments in the source. The first three are compile
fixes the shipped `__DA1470x__` (embedded) path needs — upstream builds the SDL path, so
these do not surface there.

1. `src/p8_emu.c` `p8_render()`: upstream uses an undeclared `color_pal` in the
   `__DA1470x__` branch. Changed to the `pal` that is already in scope. No behaviour
   change intended.
2. `src/p8_emu.c`: includes `p8_print_helper.h`, which declares the `draw_text()` that
   `p8_render()`'s fps readout calls (upstream reaches it only through `p8_lua.c`).
3. `src/p8_emu.h`: under `OS_FREERTOS`, includes `retro_heap.h` before the
   `#define malloc/free` macros, so every translation unit that includes `p8_emu.h`
   sees the `rh_malloc`/`rh_free` declarations.
4. `src/p8_emu.c`: a **frame-driven entry** — `p8_begin()` + `p8_step()` — so a
   superloop (the device, the wasm simulator) can run one emulated frame per
   iteration instead of blocking in `p8_main_loop()`. A cart's `reset()`/`run()`
   longjmps to `jmpbuf_restart`, which the harness owns with a per-frame `setjmp`.
   `p8_run()` is unchanged for the desktop build. (`jmpbuf_restart` itself is made
   non-static and declared `extern` in `p8_emu.h` for this.)
5. `src/p8_emu.c`: a **cooked-cart loader** — `p8_load_cooked(cooked, size)` — and the
   buffers the unreachable file paths used are no longer allocated. picovave's carts are
   `.p8.png` (the page unwraps the image), so the only cart input the engine ever sees is
   the 32,800-byte cooked cart, and `p8_load_cooked()` applies exactly what `parse_png_ram()`
   does after its PNG decode: the cartridge ROM verbatim, then the code section expanded (patch 8
   later drops the RAM copy; the ROM is read out of the caller's cart). Two details are
   deliberate, not cosmetic:
   - the expansion writes into **`m_lua_script`** (the full 64 KiB), not upstream's
     `m_decompression_buffer` (32 KiB): with upstream's pairing a script over 32 KiB
     overflows (baba is you demake expands to 45,053 B of Lua).
   - `p8_init()` leaves `m_file_buffer`, `m_decompression_buffer` and `m_temp_cart_memory`
     **NULL**: every user of them is `fopen`-guarded (a device `fopen` always fails) or
     `cstore`-guarded, and those 114,688 B are worth 6.25x the device's Lua heap
     (22,016 -> 137,472 B). `p8_load_ram()` (the text path) is therefore not usable on the
     device; nothing calls it.
6. `src/lua/lparser.c`: **`?` where an expression is expected** (`primaryexp`). Upstream
   handles `?` only as a statement, but PICO-8's preprocessor turns `?` into `print(` and
   closes the parenthesis at the end of the line, so `local w = ?text, 0, -1000` is
   `local w = print(text, 0, -1000)` — and `print()` returns the x after the text, which is
   how a cart measures a string's width (`baba_demake-9` does exactly this, and without the
   patch it fails to load with a syntax error). `shortprint()` gained an out-parameter: a
   statement discards the call, an expression keeps its one result. This is additive — the
   statement path is unchanged, and PICOVAVE's parser rule is faithful to PICO-8's own
   preprocessing (a `?` consumes the rest of the line).
7. `src/p8_lua.c` `lua_init_script()`: **clears the cached cart callbacks** (`m_lua_update`,
   `m_lua_update60`, `m_lua_draw`, `m_lua_init`) and resets `m_fps` when a cart is loaded.
   Upstream only ever SETS those four — in `lua_init_script()`'s discovery and in the lazy
   re-checks inside `lua_update()`/`lua_draw()` — and never clears them, which is harmless
   when a process runs one cart. picovave runs cart after cart in one process (an upload **is**
   a boot, `picovave_boot()` after the SysEx END), so a cart that defines no `_update`
   inherited the previous cart's cached pointer, whose function object the new load had
   already collected; the frame driver then called a nil `_update` and the cart died at frame 0
   with "attempt to call a nil value". Found by the wasm suite, which now uploads **every**
   real cart in `carts/` one after another (`sim_test.mjs`); the host reproduces it with two
   carts in one process.
8. `src/p8_emu.c`: **the cart stays where the caller put it.** Two buffers that used to be RAM
   copies are now taken from the cooked cart on demand, because the device's pool is what every
   real cart runs out of:
   - `m_cart_memory` **points at the cooked cart's ROM half** (the caller's buffer: the device's
     SLOOP slot through XIP, the simulator's baked array) instead of a 17,152-byte `malloc`.
     `p8_reset_cart()` and Lua's `reload()` read it exactly as before - it is the cart's original
     bytes either way - and nothing writes it (`cstore()` is refused first, `p8_new_cart()` is not
     reachable from the app).
   - `m_lua_script` (64 KiB) is **materialised only around `lua_init_script()`**
     (`p8_materialize_script()` / `p8_release_script()`): expanded out of the cart's code section,
     handed to `luaL_loadbuffer()`, and freed the moment Lua owns the compiled chunk. `p8_begin()`
     re-materialises it when a cart's `reset()`/`run()` re-enters the harness, so a reset still
     restarts the cart from its own source.
   Together the engine's footprint for the whole time a cart runs drops from 156,416 B to 73,728 B
   (PICO-8's 64 KiB address space + the overlay screen + the small fixed buffers), i.e. the Lua heap
   grows by 82,688 B.
   Measured, not assumed: the wasm runs the cart on the device's own allocator inside the device's
   own arena (`femto8/stub/device/alloc.c`, through `sim/device_alloc.c` - the default
   `tools/build_sim.py` flavor) and reports the pool's high-water mark and how many allocations it
   refused (`sim_heap_peak()`/`sim_heap_refused()`) - which is what the page's "will this fit the
   device?" answer, `sim_test.mjs` and `tools/probe_carts.mjs` all read. Of the 413,160 B the
   device's Lua actually gets, the baked cart peaks at 108 KB and `flipside` at 204 KB; a 45 KB
   script like `baba_demake-9` needs a pool of about 400 KB, which is what `docs/HANDOVER.md` 13
   is about.
9. `src/p8_lua.c` `reload(name)`: **refuses** instead of parsing into the buffers patch 5 leaves
   NULL (`parse_cart_file()` with a NULL destination would fault). Same shape as `cstore()`'s
   existing refusal: `reload(dest, src, len)` without a name still restores from the cart ROM,
   which is what carts actually call.
10. `src/p8_emu.c` `p8_render()` (**the `__DA1470x__` branch**): the cart's screen goes into the
    seam's own 128x128 GDI surface, 1:1 - PICO-8's screen size - and not into a 240x240 panel
    framebuffer. Upstream's device branch scaled here, with a hand-unrolled 15-destination-pixels-
    per-8-source nearest scaler; that scale is `gdi_expand_row()` (`seam/gdi.h`) now, applied one
    destination row at a time by whoever pushes a panel (the board blits a row into the ST7789, the
    wasm and the host preview expand into their own capture). Two things follow: the app's static
    screen drops from 115,200 B to 32,768 B, and `app.ld` puts `.pool` right behind `.bss`, so all
    82,432 B are Lua heap (`docs/HANDOVER.md` 12) - the difference between a 13 KB cart and a 45 KB
    one; and a whole panel frame never exists in RAM on the device.
    The same rewrite makes the device branch draw what the **SDL** branch draws: the screen
    transform at 0x5f2c (`screen_transform_pixel()`) and high-colour mode
    (`high_color_resolve()`) are applied, where upstream's device branch ignored both. Those two
    helpers **moved out of the `#ifdef SDL` block** - nothing in them is SDL-specific, they only
    read `m_memory` - so all three consumers (device, wasm simulator, host preview) render a cart
    identically; that is what `tools/verify_final.py`'s pixel parity compares.
11. `src/p8_emu.c` `p8_render()` (**the `__DA1470x__` branch**): **per-frame dirty rows**, and a new
    seam entry point `gdi_display_update_dirty()` (`seam/gdi.h`) that says which PANEL rows (0..239)
    changed. The board pushes a frame over 30 MHz SPI, so a full 240x240 panel is 115,200 B =
    **30.72 ms** of the 33.3 ms a 30 fps frame has - 92% of the budget, spent whether or not the
    cart moved. Measured on the eight gallery carts (`tools/frame_budget.mjs`), the median rows per
    frame run from **0** (baba, clockwise, steps idle) to **227** (OMEGA ZONE idle); typical moving
    carts are bownmobs **4**, carrot **13-16**, loop-4 **14-25**, Celeste **57-110**. So the engine
    now tells the target what to skip.
    How, without a previous-frame buffer: `.bss` is Lua heap on this board (`app.ld` puts `.pool`
    right behind it), so a 32,768 B copy of the last screen would cost the 45 KB carts. Instead each
    **source** row (the 128 rows of PICO-8's screen) is hashed with FNV-1a-32 *as it is written* -
    the render loop is already touching every pixel, so this is one multiply-xor per pixel and no
    second pass over 16,384 pixels - and compared with the same row's hash from the previous frame.
    Cost, measured on the object rather than counted by hand: **760 B** of `.bss` (512 B of hashes +
    240 B of mask + the four flags, with padding) plus **4 B** of `.common` for the row count -
    **764 B** in all, against the ~16 KB of headroom the arena has.
    The mask is indexed by **panel** row, the unit the target's push is in: source row `s` feeds
    panel rows `[s*240/128, (s+1)*240/128)` - the exact rows `gdi_expand_row()` reads from it - and
    a dirty source row marks all of them. It is a **superset**, never a subset: a false "dirty" only
    costs SPI time, a false "clean" is a stale row on the panel. So every frame also marks all 240
    rows when the hashes cannot be trusted: the first frame after a (re)start (`p8_common_reset_cart()`
    - a boot, a `reset()`/`run()` longjmp, a USB upload), a screen-transform or high-colour-mode
    change (they change the pixels, not the screen bytes the hash sees), and any frame the I/O-icon
    overlay composite runs - plus the frame after it, which is when `p8_show_io_icon(false)` erases
    the icon. That last one is why the overlay path is unchanged: it still composites all 128 rows,
    it just costs a full panel frame while a cart touches `cartdata()`, which is rare and correct.
    The seam ships a **weak** `gdi_display_update_dirty()` that just calls
    `gdi_display_update_async()`, so `seam_sim.c` and `host/seam_host.c` needed no change: they
    still expand all 240 rows and their `p8_panel_bytes` still means "115,200 B per push", because
    that is what they really do. The board defines the symbol itself (`src/picovave.c`) and its
    definition wins at link time; a target that does not define it keeps the full-repaint behaviour
    by construction.
    The durable measurement is `tools/frame_budget.mjs` (gallery carts, idle and scripted input):
    the mean panel transfer falls from **35.1 ms to 5.0 ms** (90.4% of the 33.33 ms 30 fps budget
    reclaimed), every measured row fits, and the mask is a superset of the pixels that changed on
    all 1,284 frames it was checked on (`loop-4`'s `_update60` two-push ticks are oracle-only).
    The hashes and mask cost **764 B** of `.bss`; `app.ld` trims the unused tail of `.ram_text`
    (0x1400 -> 0x0E00) to pay for it, so the device arena grows to 415,696 B rather than shrinking.
