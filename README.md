# picovave — PICO-8 firmware for the SMK-37 Pro

**English** · [한국어](README.kr.md)

This repository reverse-engineers the custom firmware package format for the M-Vave SMK-37 Pro
(AC791N) and builds **picovave.fwsc: firmware that runs PICO-8 (femto8) carts, maps the
instrument's inputs (18 buttons · 8 encoders · 37 keys · 16 pads · 4 faders · pitch/mod wheels)
straight onto the game pad, enumerates as USB-MIDI so a host can upload carts over SysEx, and can
be re-flashed on the real device (it can never be bricked)**.

It also keeps the most valuable idea from Makkuro37: **the browser simulator compiles the same
sources as the firmware.** The app layer and the cart store/protocol live in shared sources
(`src/picovave_p8.h`, `src/picovave_cart.h`, `src/picovave_input.h`, plus `src/picovave_gfx.h` for
the screen), and the engine beneath them is a vendored femto8 (`femto8/`). The simulator's screen is
not an imitation — it is the pixels the real ROM draws. Two things are pinned by pixel-exact checks:
**the same screen** (no input) and **the same input** (after feeding the same
buttons/keys/encoders/pads/faders/wheels to both) — compared against the host preview
(`host/preview.c`), which compiles the same engine and app layer as the other two.

The device build's platform (HAL · boot · USB/OTA · packer) is **a copy vendored from Makkuro37**
into `platform/`, so picovave builds **self-contained** — it never reads `repo/` at build time (which
is why the USB device name is `picovave` too). And `picovave.fwsc` can be **verified at the simulator
level**, and by the same principle **flashed onto the real device**. The simulator is self-contained
as well: page, worklet and wasm are all picovave's and none of them reference `repo/`.

- Reverse-engineering write-up: [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) (Korean)
- Dual-Core AMP Architecture: [docs/DUAL_CORE_AMP.md](docs/DUAL_CORE_AMP.md)
- UBOOT Recovery Guide: [docs/RECOVERY.md](docs/RECOVERY.md)
- Handover Engineering Journal: [docs/HANDOVER.md](docs/HANDOVER.md) (Korean)
- Artifact: [build/picovave.fwsc](build/picovave.fwsc)
- Simulator: [build/sim/index.html](build/sim/index.html) — `python3 -m http.server -d build/sim 8738`
  (the **한국어 / EN** buttons at the top switch language, and the choice is remembered in the
  browser. The **STAR** at the right of the masthead links to this repository on GitHub — it is the
  only thing on the page that points outside it.)

## One source, three consumers

    femto8/               vendored PICO-8 engine (femto8) + seam                 ← no hardware
    src/picovave_p8.h     app layer (boot · frame loop · setjmp · input→gamepad) ← no hardware
    src/picovave_cart.h   cart store + SysEx upload protocol (slot/CRC/7-bit)    ← no hardware
    src/picovave_input.h  input model (buttons/keys/encoders + pads·faders·wheels) ← no hardware
    src/picovave_gfx.h    128x128 RGB565 canvas (OTA/recovery/crash screens)     ← no hardware
            │
            ├─ src/picovave.c     + platform/firmware/{hal,src}
            │                     + femto8/seam/seam_device.c + femto8/stub/device/  the real device
            ├─ sim/sim.c          + picovave_sim_hal.h + femto8/seam/seam_sim.c      the browser (wasm32)
            └─ host/preview.c     + host/seam_host.c                                 host render / input parity

The device build uses **Makkuro37's HAL, boot and USB/OTA stack and recipe, vendored under
`platform/`** (`fm1_time.h`, `fm1_sys.h`, `fm1_lcd_hw.h`, `lcd.c`, `smk_input.h`, `fm1_timer.h`,
`fm1_audio.h`, `usb.c`, `ota.c`, `fm1_flash.h`, `crt0.S`, `fm1_vec.S`, `fm1_isr.S`, `app.ld`, and
the packer `platform/tools/fm1pkg_make.py`). What picovave writes itself is the app layer
(`src/picovave_p8.h` — boot · frame loop · input→gamepad), the cart store and upload protocol
(`src/picovave_cart.h`), the screen glyphs (`src/picovave_gfx.h`), and the device's mini libc and
allocator (`femto8/stub/device/`) — so the parts already proven on hardware are not rebuilt. The
other consumers (simulator, host) swap only a small hh_* HAL and the seam (`femto8/seam/`).

## Display path and Dual-Core AMP acceleration

The AC791N dual-core RISC processor is decoupled via **Asymmetric Multiprocessing (AMP)**:
- **Core 0 (Application Core)**: Runs the PICO-8 Lua VM, single-writer audio synthesizer (`render_sounds()`), 10kHz timer input scan, and USB MIDI/SysEx. As soon as it finishes drawing to 4bpp 8KB VRAM (`m_memory + 0x6000`), it dispatches asynchronously to Core 1 (`p8_device_dispatch_render()`) and returns to the next task in **0.00 ms**.
- **Core 1 (Graphics Co-processor)**: Handles 4bpp ➔ RGB565 palette LUT conversion, 240 dirty-row FNV-1a hash calculation, 128x128 ➔ 240x240 upscale (`gdi_expand_row`), and ST7789 SPI1 DMA streaming.
- Full architecture, IPC protocol, and safety invariants: [docs/DUAL_CORE_AMP.md](docs/DUAL_CORE_AMP.md).

The panel is fed over **30 MHz SPI**: a full 240x240 RGB565 frame is 115,200 B = **30.72 ms** of
the 33.33 ms a 30 fps tick allows. Core 1 hashes each 128-wide source row **while it renders it**
(FNV-1a-32 over the RGB565 pixels it just wrote), compares that with the previous frame, and evaluates
a 240-byte **PANEL-row mask**. The device opens one ST7789 address window per **run** of consecutive dirty
rows and streams only those (`pv_push_dirty()` in [src/picovave.c](src/picovave.c)); boot, crash, OTA and
rescue screens still repaint the whole panel (`pv_push()` is exactly "all 240 rows dirty"). The mask is a
**superset** of the rows that changed by construction - a false dirty row only costs SPI time, a false
clean row would leave a stale row on the panel - so restarts, a screen-transform/high-colour change, and every
frame that composites the engine's I/O icon mark all 240 rows.

`tools/frame_budget.mjs` is the gate, measured on the eight gallery carts in both idle and scripted
input: the mean panel transfer falls from **35.1 ms to 5.0 ms** (saving **90.4% of the 33.33 ms 30 fps
budget**), every measured row fits 30 fps, and the engine's mask is a superset of the pixels that
changed on every one of the 1,284 frames it was cross-checked on. Its scratch/measurement numbers
for each cart are in the run output. The 764 B of `.bss` the row hashes and mask cost is paid for by
trimming the unused tail of the `.ram_text` region (`app.ld`), so the arena **grows** to 415,696 B.

## Cart (game) upload and execution

The device uses the SLOOP store (flash 0x9D000..0xBB000, 120 KiB) for **one cart** (one slot,
122,880 B). The 64-byte slot header carries magic, name, length, `seq` and CRC16/CCITT, and boot
runs that slot — an upload *is* a launch. One slot, because a **precompiled cart** (`PVB1`: ROM +
Lua bytecode, up to 105,656 B) does not fit a 61,440 B slot. The price is that there is no rollback
slot: if power is lost mid-upload, the bottom of the ladder — the **built-in cart** — comes up (the
panel never stays black). An upload is USB SysEx `F0 22 24 35 7E <cmd> .. F7` (`01 BEGIN` /
`02 DATA` / `03 END` / `05 ERASE` / `06 LIST`).

**The only cart format accepted is `.p8.png`.** The page unwraps the 160×205 image (the low two bits
of each pixel *are* the cart) into a **cooked cart** (ROM 17,152 B + code section 15,648 B =
32,800 B), and those bytes plus a 4-byte `PVC1` magic make the **32,804 B** that BEGIN accepts —
nothing else (any other length is `ERR 7`, not a cart). The device expands only that code section:
femto8's existing expanders (PXA `\0pxa` · `:c:` mini · older raw) are driven by
`p8_load_cooked()` straight into a 64 KiB Lua script buffer (upstream targeted a 32 KiB buffer and
overflowed on e.g. baba's 45,053 B). A text `.p8` is **refused**: its gfx/map/sfx/music are hex, so
it is 2–3× larger than the compressed cart the author saved and does not fit the slot — and two
formats would be confusing (`.p8` → `.p8.png` is PICO-8's own save).

A cooked cart is **binary**, so the payload is **split 8-bit → 7-bit** to travel over SysEx (the
same way the package protocol does it; `off`, `n` and the CRC are over the raw bytes).
**CRC16/CCITT covers the raw payload**, so a committing END is itself byte-level proof that the
split is exactly reversible. The same parser runs in the simulator, so the protocol cannot drift
between them.

There are two upload **targets**, chosen on the page — the same split as the DEVICE box's
SIMULATOR / FLASH(USB):

- **SIMULATOR** — writes into the store inside the web wasm (`sim_store`). The receiving parser is
  the firmware's own (`src/picovave_cart.h`), so it runs unchanged inside the wasm, and the browser
  is the device.
- **FLASH(USB)** — writes to the SLOOP store of a connected **real device** over Web MIDI SysEx. It
  sends **the same frames** (same slot alternation, same CRC16), so an upload that works in the
  simulator works on hardware as-is.

In both cases a commit does not reset the chip but **reboots into that cart** (`pv_cart.changed` →
`picovave_boot()`) and the slot survives POWER-ON. The CLI (`tools/fm1_install.py`) still installs
firmware only and has no cart commands — carts go to hardware through the page's FLASH(USB).

The page's **cart gallery** shows eight community-vetted carts (`carts/*.p8.png`) as cards with the
**cart label image**, name, author and source (BBS thread) — a `.p8.png` *is* the label, so there is
no second image to keep in sync. Picking a card starts the transfer immediately to the **target
chosen above** (SIMULATOR or FLASH(USB)) and the page moves to *firmware and carts* to show the
progress — it runs the **same path** as a dropped file (PNG unwrap → measure in the device pool →
BEGIN/DATA/END), so the gallery cannot skip the check or send something else (`sim/run_page.mjs`
verifies each card's label, served file, source link and the alphabetical order of the names). Each
cart keeps its author's licence, and the source link leads to the original thread.

**A cart the device cannot run is refused, and the device keeps going.** A cart's Lua cost is
10–20× its source size plus runtime data (tables, strings); the device arena is 415,696 B (Lua heap
414,672 B = arena − 1 KiB red zone) and the engine uses ~74 KB of it. So **size cannot predict it** —
a 45 KB script (baba) barely runs, and a 20 KB script with many strings/tables does not. The only
verdict is a **measurement**:

- Before uploading, the page runs the cart on **the device's allocator and arena** and reads
  `status`/`refused` (`measureCart()`), then shows `device OK` / `device tight` / `device no`. One
  refusal is one full Lua GC (terra needs 71 s for 300 frames), so the measurement yields between
  frames and **stops early once the stutter is certain** — the verdict text states how many frames
  it actually measured. For a cart that does not run at all, or visibly stutters, on hardware, the
  page asks once more with `confirm()` before a FLASH(USB) upload.
- The device defends itself with a **rollback ladder**: if the newest valid slot is refused at
  boot/upload (`p8_begin()` returns `LUA_ERRMEM`) → the slot it replaced → the **built-in cart**,
  in that order. So even a cart beyond the device's pool leaves the panel running the built-in cart
  (measured: bunnysurvivor is refused 3 times → built-in fallback, `status=0`).

For the record: the same cart running on an ESP32-WROVER-B port with 8 MB PSRAM (PicoPico) is not an
interpreter difference — femto8 vendors z8lua too (`LUA_NUMBER = fix32_t`). The only difference is
heap size, and the AC791N has no PSRAM interface, so this ceiling is a hardware constraint.

## Input

The device reads 18 buttons · 37 keys · 8 encoders through Makkuro37's `smk_input.h` (SPI2 +
74HC595, plus the ADC mux), and polls the pads (with strike velocity) · 4 faders · pitch/mod wheels
on top of that. `pv_gamepad()` in `src/picovave_p8.h` turns them into PICO-8's six buttons using
**the SMK-37 Pro's game layout as owners verified it** (low keys + a pad row = D-pad, two keys/pads
= O/X). There is **no ESC (stop) input**: ESC-stop returns to a console, and this port runs carts
only, so there is no development environment to return to. The simulator page feeds **the same
logical inputs** through `sim_button` / `sim_key` / `sim_enc` / `sim_pad` / `sim_fader` / `sim_wheel`,
and both paths are handled by the same app layer. The host preview drives the same `hh_input_read`
contract from a script.

## In the simulator

The simulator page carries picovave's panel — wheels (PITCH·MOD) · the screen with its six buttons
underneath · K1..K8 and 16 pads · faders and the 2×6 buttons · the keybed — and **the audio comes
from picovave's own AudioWorklet** (`sim/worklet.js`). The wasm it runs is the picovave firmware's
own sources (`sim/sim.c` includes `src/picovave_p8.h`, `src/picovave_cart.h` and the vendored femto8
engine unchanged), so **the sound the engine makes itself** (femto8's `render_sounds` in
`p8_audio.c`) flows into the worklet — on the device the main loop fills a ring and the ALNK0 ISR
takes half of it (single writer of the engine's audio state); in the simulator the same function
fills the same halves. The worklet contract is `sim_render` / `sim_out_l·r` / `sim_pads` / `sim_leds`
/ `sim_pad` / `sim_fader` / `sim_wheel`, on a 100 ms loop. Page, worklet and wasm are all picovave's
and none of them reference `repo/`.

Because of browser autoplay policy, audio needs one user gesture. The page starts with audio on
(SOUND on), and **the first click or key press** is that gesture: the installed firmware moves into
the worklet right away. **SOUND** is the mute toggle. Before that, the page runs the same wasm on a
*quiet main-thread runner* over the same message protocol — which is also the path the headless
(Node) checks take. In other words: worklet in the browser, main thread headless — **one runner, two
backends**, with the same driver messages.

Installing and restoring happen in the DEVICE box below.

## Simulator install · flashing the real device · restoring v016

The simulator page's **DEVICE** box has two branches.

- **SIMULATOR** — applies in the browser. Installing `picovave.fwsc` brings up a wasm built from the
  same sources as that package, right away. It **verifies** the package with `web/sim/fwsc.mjs`
  (header / file list / `flash.bin` CRC) and shows which flash bytes the install changes. It writes
  `flash.bin` into a 1 MB flash model and never touches the protected regions (0x9d000 VM …
  0xff000 key_mac). **It does not use USB.**
- **FLASH(USB)** — really writes. `web/sim/fm1ota.mjs` (a Web MIDI port of
  `platform/tools/fm1_install.py`) sends the package to the connected SMK-37 Pro over USB-MIDI
  SysEx, and the device installs and reboots itself (handshake → upgrade → package check/loader
  staging → the loader writes the app area → identity check after restart). It needs Chrome
  (https/localhost) and the user's MIDI permission. Neither the flash model nor the wasm is
  involved — the hardware runs its own image. A brand-new SMK-37 Pro exposes **several USB-MIDI
  ports** (`-Private`, `-Master`, `-MCP`, …), and the name alone does not say which one answers an
  update. So when it cannot find the device automatically, **MIDI port diagnostics** queries
  handshakes on **every** port and shows which answered (and its identity), then selects that port
  for you. Sending immediately after opening a port can make CoreMIDI drop the message, so it waits
  0.3 s before querying (as the reference `fm1_install.py` does), and it also queries the same input
  with other families' outputs, because cable in/out names are not always 1:1.
- The page identifies a loaded package as picovave's by its **content** (`flash.bin`) — stock v016
  reports identity `SMK-37 Pro_016`. Any other ROM (stock v016, say) cannot run in the SIMULATOR, so
  it is marked "real device only", but FLASH(USB) can still write it. A browser can run the shared C
  sources; it cannot run pi32v2 machine code.
- Restoring is done by dropping or picking a `SMK-37_Pro_016.fwsc` the user **already owns**, and
  applying it to the SIMULATOR (model) or FLASH(USB) (hardware). The stock firmware is copyrighted,
  so it is not in this repository or on the page. **This has been done on hardware**: a unit running
  picovave was restored with the stock v016 package over FLASH(USB) and came back up normally —
  restore is not a separate code path that could rot while install still works.
- The page also links the `picovave.fwsc` download.

`web/sim/fwsc.mjs` is a port of `tools/fwsc.py`. A port drifts silently, so `tools/verify_final.py`
runs both readers over a real package and requires **the same answers**. For the same reason
`web/sim/fm1ota.mjs` is **cross-checked** against `platform/tools/fm1_install.py` for the wire
format, and `sim/fm1ota_check.mjs` runs a **whole update** against a fake device and compares every
byte with the package.

## Quick start

    # 1. firmware + install package (picovave; self-contained build on the vendored platform)
    JIELI_TOOLCHAIN=~/.jieli/toolchain AC79_SDK=~/fw-AC79_AIoT_SDK python3 tools/build_picovave.py  # → build/picovave.fwsc

    # 2. browser simulator (picovave wasm + page + worklet)
    python3 tools/build_sim.py                                 # default: heap = device allocator (femto8/stub/device/alloc.c) · device arena
    python3 tools/build_sim.py --stack 24320                   # the device's own C stack (24,320 B): does the engine fit inside it?
    python3 tools/build_sim.py --probe-alloc                   # heap as a cost model (sim/heap_probe.c) — a conservative estimate, no arena
    python3 -m http.server -d build/sim 8738                   # → http://localhost:8738

    # 3. verification (app/package + screen and input pixel parity (host preview ↔ wasm) + simulator page + real-device flasher cross-check, 229 checks)
    AC79_SDK=~/fw-AC79_AIoT_SDK python3 tools/verify_final.py

Requirements: the JieLi pi32v2 toolchain (`~/.jieli/toolchain`), the JieLi AC79 SDK, Pillow, Node.
The wasm build picks emcc or the emscripten/emsdk Docker image automatically. On macOS the JieLi
toolchain commands run inside a linux/amd64 Docker container as well.

## Layout

| Path | Role |
|---|---|
| [src/picovave_input.h](src/picovave_input.h) | input model (hin_t: buttons/keys/encoders/pads/faders/wheels) — shared by all three consumers |
| [src/picovave_p8.h](src/picovave_p8.h) | app layer: femto8 boot · frame loop (`p8_begin`/`p8_step`) · per-frame setjmp · input→gamepad — shared by all three consumers |
| [src/picovave_cart.h](src/picovave_cart.h) | cart store (one slot · CRC16 · `seq`) + SysEx upload protocol — shared by device and simulator |
| [src/picovave_gfx.h](src/picovave_gfx.h) | **128x128** (PICO-8's own size) RGB565 canvas: the surface the engine draws a cart into, which the device's OTA/recovery/crash screens reuse as the `hg_fb` alias (the panel then scales a line at a time). The engine is compiled with `-DLUAI_MAXCCALLS=48` (stock 200 is 316 B per protected level × 200 ≈ 63 KB, over a 24,320 B stack, so deep carts fault in hardware before Lua's own guard can fire). The build also puts **its own tag** (first 8 hex digits of the sha256 of the build inputs) into the NUL padding of the identity reply, and `ota_probe.py --ask`/`--flash` prints it (the panel holds the last frame, so it cannot prove whether a flash happened). `hg_crash_screen()` draws PC · vector · EMU/DBG · SP verdict · USP/SSP · **stack high-water mark** · crash count · ETM in 12 lines under a `PICOVAVE CRASH` headline, and the record left in `.noinit` makes **the next boot redraw the same screen as `LAST CRASH` for 10 seconds** (you cannot miss it) |
| [femto8/](femto8/) | vendored PICO-8 (femto8) engine — **11 documented patches** only ([femto8/README.picovave.md](femto8/README.picovave.md)) |
| [src/picovave.c](src/picovave.c) | device entry point + hh_* HAL + panel push (RGB565 BE swap) + ALNK0/TIMER5 ISRs + USB/OTA/cart glue |
| [platform/firmware/{hal,src}](platform/firmware/hal) | real-device HAL · panel + USB/OTA + loader (a copy vendored from Makkuro37 — picovave is self-contained) |
| [sim/sim.c](sim/sim.c) | wasm simulator (includes the app layer, cart and engine; only the HAL is swapped) |
| [sim/picovave_sim_hal.h](sim/picovave_sim_hal.h) | simulated panel + input HAL |
| [sim/worklet.js](sim/worklet.js) | picovave's own AudioWorklet (the audio engine; reads no `repo/`) |
| [sim/check_api.mjs](sim/check_api.mjs) | do the exports the page/worklet call actually exist (wasm) |
| [sim/run_page.mjs](sim/run_page.mjs) | runs the page's real script against the wasm (SIMULATOR/FLASH(USB) included) |
| [sim/fwsc_check.mjs](sim/fwsc_check.mjs) | `fwsc.mjs` ↔ `fwsc.py` cross-check runner |
| [sim/fm1ota_check.mjs](sim/fm1ota_check.mjs) | `fm1ota.mjs` ↔ `platform/tools/fm1_install.py` cross-check + a full update against a fake device |
| [host/preview.c](host/preview.c) + [seam_host.c](host/seam_host.c) | host preview: the same engine and app layer + capture HAL/scripted input (pixel-identical to the wasm) |
| [host/alloc_test.c](host/alloc_test.c) | **unit/stress test for the device allocator** — runs `femto8/stub/device/alloc.c` in the device's own 415,696 B arena (the simulator and preview use another allocator, so this file is the only one that exercises that code). `verify_final.py` runs it every time |
| [web/sim/index.html](web/sim/index.html) | the simulator page (SMK-37 Pro panel + DEVICE: SIMULATOR(web) / FLASH(USB)) · the cart target uses the same split |
| [web/sim/fm1ota.mjs](web/sim/fm1ota.mjs) | Web MIDI SysEx flasher for hardware (cross-checked against `platform/tools/fm1_install.py`) |
| [web/sim/fwsc.mjs](web/sim/fwsc.mjs) | browser port of the .fwsc reader (cross-checked against `tools/fwsc.py`) |
| [web/sim/p8png.mjs](web/sim/p8png.mjs) | `.p8.png` → cooked cart (magic + ROM + code section) — page and Node tests use **the same module** |
| [tools/fwsc.py](tools/fwsc.py) | standalone .fwsc container parser/codec |
| [tools/picovave_pack.py](tools/picovave_pack.py) | flash image + package builder (independent packer, used for cross-checks) |
| [platform/tools/fm1pkg_make.py](platform/tools/fm1pkg_make.py) | packer (copy vendored from Makkuro37; the reference for device packaging) |
| [tools/build_picovave.py](tools/build_picovave.py) | firmware build driver (vendored platform + fm1pkg_make) |
| [tools/build_sim.py](tools/build_sim.py) | simulator build driver (picovave wasm + page + worklet). `--stack 24320` = device stack; default heap = device allocator (`sim/device_alloc.c`, device arena); `--probe-alloc` = cost model (`sim/heap_probe.c`) |
| [tools/probe_carts.mjs](tools/probe_carts.mjs) | per-cart measurement tool: peak, refusal count and final slot (fallback or not) on the device allocator. `--sweep` varies the arena size (the basis of the §13 table and the long run) |
| [tools/frame_budget.mjs](tools/frame_budget.mjs) | **the display-path gate**: runs every gallery cart idle and with scripted input on the wasm, cross-checks the engine's dirty-row mask against the pixels, and requires the measured gallery to reclaim >= 20% of the 33.33 ms 30 fps budget (exit 0/1; `--full-frame` prints the pre-optimisation baseline) |
| [tools/pvcart.py](tools/pvcart.py) | built-in default cart = **cooked cart** (ROM 0 + raw code section) generator — all three builds bake `cart_default[]` from it |
| [tools/ota_probe.py](tools/ota_probe.py) | real-device MIDI probe + `--flash PKG` (recovery: runs `fm1_install.py`) |
| [tools/mido_boot.py](tools/mido_boot.py) | re-exec so it also runs on a python3 without mido (shared by ota_probe and fm1_install) |
| [tools/fake_device.py](tools/fake_device.py) | a fake device that runs a whole update without hardware (`--test`, or as a CoreMIDI port) |
| [tools/verify_final.py](tools/verify_final.py) | full verification (including screen and input parity) |

## Safety

- The app **enumerates as USB-MIDI, answers M-UPGRADE and can be re-flashed** — it uses the vendored
  `usb.c` + `ota.c`, and the TIMER5 ISR (10 kHz) runs the input scan (`fm1_input_tick`) and USB
  polling together. **The USB device name is `picovave`** (`STR2` in
  `platform/firmware/src/usb.c`, not Makkuro37).
- Only two regions are written directly from the running app: **updater staging (0xBB000-0xBFFFF)**
  and the **cart store (SLOOP, 0x9D000-0xBAFFF — where a host upload/`ERASE` erases and writes in
  4 KiB sectors)**. The app area (0x04000-0x9CFFF) is replaced by the update loader (SPL), and
  VM/BTIF/USRTRIM/USRFLASH/USR/key_mac are preserved.
- **It can never be bricked (escape routes):** (a) hold OCT- + OCT+ for 5 s while running = UBOOT,
  (b) USB SysEx `F0 22 24 35 7D F7` = UBOOT, (c) **hold OCT- while powering on** (or two failed
  boots) = app-level **USB RESCUE** (**headless**: usb.c + ota.c only, no screen — the installer is
  the screen) — the device still enumerates as USB-MIDI and **the web installer / `fm1_install.py`
  re-flashes it as usual** (no jl-uboot-tool needed). RESCUE services USB from the **TIMER5 ISR
  (2 kHz)**: with 500 ms loop polling a 512 B update frame never lands inside `ota_read()`'s window
  and the session cannot even start (which is why (a)/(b) still work in RESCUE and `flash_ok` does
  not block a session), (d) if the app still cannot come up, mask ROM UBOOT. Restore from UBOOT, or
  by reinstalling the stock v016 package.
- **SysEx robustness** — if a slow host cuts a SysEx reply short, the next transfer closes it with a
  lone `F7`, so the host parser never jams (SLOOP/dinghy's `sx_open` fix ported into the vendored
  `usb.c`).
- **Recovering a device USB cannot see** (e.g. an old build without a USB stack) — use mask-ROM
  UBOOT (`WL82 UBOOT1.00`) and `jl-uboot-tool` to write only the app area (`0x4000-0x9CFFF`). The
  full procedure is in [docs/RECOVERY.md](docs/RECOVERY.md).
- **There is no BLE MIDI — do not count on it as a recovery path.** The stock v016 `app.bin` contains
  BLE-MIDI GATT (`03B80E5A-EDE8-4B33-A751-6CE34EC4C700` / `7772E5DB-3868-4112-A1A9-F2669D106BF3`),
  the advertising name `SMK-37 Pro_016`, and `BTSTACK`/`BTCTRLER`, but all of that lives **inside
  the app area we replace**. picovave's `app.bin` has none of the three — picovave has no BLE stack
  and its MIDI is **USB-MIDI only**. The escape routes are the USB/mask-ROM paths (a)–(d).

This is unofficial firmware; installing it is your responsibility.

## License

**GPL-3.0-only** — see [LICENSE](LICENSE). It covers everything picovave wrote (`src/`, `sim/`,
`host/`, `web/`, `tools/`, `docs/`) and the built `picovave.fwsc`, and it is the license the
vendored Makkuro37 platform already carried, so source and binary finally agree.

Two vendored trees are stated separately:

- `femto8/` — femto8 (Ben Baker) and Lua 5.4, both MIT, included in the GPL-3.0 work with their
  notices intact; LodePNG (zlib) is not linked. The PICO-8 compression snippets are Lexaloffle's,
  and PICO-8 itself is not open source.
- `platform/` — GPL-3.0-only (Leo Kuroshita / Hügelton Instruments; Makkuro37 (C) M. Geisler).

Details: [NOTICE](NOTICE).
