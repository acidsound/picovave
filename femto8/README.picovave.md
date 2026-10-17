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
   Measured, not assumed: the wasm reports the pool every cart needs (`sim/heap_probe.c`, which
   charges the device allocator's own 16 B per block), and that is what the page's "will this fit
   the device?" answer and `sim_test.mjs` use - of 293,888 B, the baked cart peaks at 108 KB and
   `flipside` at 204 KB, while `baba_demake-9` needs 622 KB.
9. `src/p8_lua.c` `reload(name)`: **refuses** instead of parsing into the buffers patch 5 leaves
   NULL (`parse_cart_file()` with a NULL destination would fault). Same shape as `cstore()`'s
   existing refusal: `reload(dest, src, len)` without a name still restores from the cart ROM,
   which is what carts actually call.
