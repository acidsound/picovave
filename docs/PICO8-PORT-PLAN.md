> **출처 / 상태 (salvaged).** 이 문서는 이전 `picovave/work/pico8`(GameDude 베이스 — FM-1/SMK GB
> 엔진) 시도의 포트 계획을 그대로 옮긴 것이다. 그 베이스(`board.c` · `vendor/felucca` · GB 엔진 ·
> `tools/emu` pi32v2 CPU 시뮬레이터)는 폐기했지만, **femto8 엔진과의 접점(seam) · 카트 형식 ·
> `pico8 -x` 오라클 방법 · 소유자 결정(240x240 스트레치, 패드/건반 매핑, 2×32 KiB 카트, Pro 전용)** 은
> hello 스켈레톤을 계승한 이 트리에서 그대로 유효하다. 본문의 "GameDude base", `gp_*`, `board.c`,
> `firmware/`는 역사적 맥락이며, 현재 트리의 등가물은 `picovave/src/picovave_p8.h`(앱 계약) +
> `picovave/src/picovave_cart.h`(카트/SysEx) + vendored `picovave/femto8/`(엔진)와
> `picovave/platform/`(하드웨어 HAL · USB/OTA · 로더 · 패커)이다. femto8 엔진 원본은
> 이전 시도 트리 `../../picovave/femto8`(upstream MIT, pin `f9e5097`)에 pristine 사본으로 남아 있다.
> **이식은 완료되었다**: 기기·시뮬레이터·호스트 프리뷰가 같은 엔진을 돌리고 `tools/verify_final.py`가
> 204개 검사로 그린이다(아래 "Done" 참조). 이 문서는 **계획 기록**이다 — 현재 상태·남은 일·실기 확인
> 절차는 [`HANDOVER.md`](HANDOVER.md)에 있다.

---

# PICO-8 on the SMK-37 — port plan (base: GameDude, engine swapped)

Status: **ported and green (2026-10-17).** The engine, the shared app layer and the cart store all
build for the device, and the wasm simulator and the host preview render the same pixels. See the
"Done" section at the end for what landed.
Base tree: `work/pico8` = https://github.com/ChanceTheMaker/gamedude-fm1-smk @ `12f6185`
(2026-10-13), GPL-3.0, plus its vendored Felucca/MusicCoach HAL (Apache-2.0/GPL-3.0
per-file, pinned in `vendor/provenance.json`). Reference copies stay pristine in
`ref/gamedude`, `ref/sloop-smk37`, `ref/makkuro37`, `kit/`, `femto8/`.

The owner's instruction: this firmware is the base, **only the game engine changes**.

## 1. Reproduced baseline (this Mac, 2026-10-14)

Upstream drives the Linux pi32v2 toolchain through WSL; this host has no WSL, so
`tools/build.py` gained a host-agnostic toolchain call (`tool_argv`/`tc_run`: WSL
when present, otherwise Docker linux/amd64 with the repo at `/work` and the
toolchain at `/opt/jieli`). `tools/zigshim` lets the host C tests build with the
system clang where Zig is absent (the wasm target still needs Zig).

| Step | Command | Result |
|---|---|---|
| SMK-37 candidate | `python3 tools/build.py smk37 --toolchain ~/.jieli/toolchain` | **PASS** — `build/smk37/gb-smk37-NOT-FOR-INSTALL.bin`, 55,248 B, sha256 `f435b22c3367bb45…`, every layout assert (entry stub `04818000`, `_start` `0x0200120`, XIP/`_dsp_text`/RAM/POOL/CPU1 stacks, no `fm1_*` init symbols in the SMK build) |
| FM-1 candidate | same with `fm1` | **PASS** — 64,344 B, sha256 `ee361e84f0f34905…`; required USB updater symbols present |
| Host C tests | `python3 tools/build.py test --zig ./tools/zigshim` | **PASS** — cartridge validation / MBC2 save RAM / input mapping / 1000 frames / exact audio timing (738,353 samples) / backpressure; SMK37 keys, pad rows, lights, PLAY+REC, taps; video scaler + worker (1.5x, torn/corrupt/timeout/fault refusal); splash + chime |
| Browser/JS suites | `npm test` | **PASS** — branding, audio, cartridge packing (32…512 KiB, integrity, invalid-ROM rejection), install handoff/resume/interruption, restore, discovery, update progress |
| Diagnostic cartridge | `build/diagnostic.gb` | sha256 `5ee5649cc32110b0…` (matches the FM-1 manifest's `rom_sha256`) |

No device was touched; both manifests say `installable: false`, `hardware_validated: false`.

### What "verified" means here — read this before promising anything

- **FM-1 path:** upstream's README + `docs/CPU1_TRIAL.md` record physical runs
  (FM-1_934/937: picture, controls, sound confirmed by the owner, CPU1 worker
  counts rising, no faults). That is the hardware-verified half.
- **SMK-37 path:** upstream says plainly "**SMK37 flashing is not enabled.** Its
  firmware compiles and its controls pass host tests, but device installation and
  physical key/pad behavior remain unverified." Our baseline reproduces exactly
  that: compiles, host tests pass, nothing on a unit.
- Consequence: a PICO-8 port on this SMK-37 layer would be **its first hardware
  run**. So the playbook's flash gates (verified full backup, allow-list, bootguard,
  forced entry) are not optional, and the owner does every hardware action.

## 2. The engine boundary (what actually gets replaced)

`firmware/board.c` (276 lines) consumes the engine only through `src/gb_port.h`:

`gp_load`, `gp_frame`, `gp_frame_count`, `gp_buttons`, `gp_pixels`,
`gp_audio_read`, `gp_audio_queued`, `gp_error`, `gp_input_keys`,
`gp_smk37_pads` / `gp_smk37_panel` / `gp_smk37_pad_lights` (input + lights tables).

Everything else in `board.c` is board work and stays: boot (`gb_cstart`:
watchdog → bootguard → `fm1_enter_uboot` fallback), splash + landing chime,
display (CPU1 `gb_video_job` scaling of an immutable frame; LCD window
`0,12,240,216`), audio (512-frame DMA halves at 44.1 kHz through `smk37_audio` /
`fm1_audio`), input (SMK: 37-key keyscan IRQ + 16-pad ADC scan + panel buttons;
FM-1: TIMER5 key scan + piano-key LEDs + PLAY/REC), master volume, fault handler,
and the per-board `#if GB_SMK37` split. Plus, unchanged: `tools/` (build, packer,
site, catalog, diagnostics), `web/` (browser player, packer, installer, restore,
progress UI, discovery fixes), `tests/`, the updater/loader and the pinned HAL.

## 3. The swap

1. **`src/p8_port.c`** — the same `gp_*` surface implemented over femto8
   (`/Users/…/picovave/femto8`, MIT, pin `f9e5097c`): cart load from RAM, one
   frame per `gp_frame()`, 128x128 RGB565 pixels, PICO-8's own audio
   (`render_sounds` into the same ring/DMA halves), 6-button input, pause/reset,
   cart label as `gp_title()`, error codes. `vendor/peanut-gb` and
   `tools/prepare_apu.py`'s minigb_apu leave the build.
2. **Dimensions.** PICO-8 is 128x128. `GB_VIDEO_PIXELS`, the A,A,B 2→3 scaler and
   the 240x216 window become a 128x128 source with a matching integer-ish
   scale (1.5x = 192x192 centred is the natural fit; exact window/scale is an
   owner decision below). The job protocol (hash, torn/tamper refusal, CPU1
   ownership, timeout) is dimension-agnostic and stays.
3. **Cartridge slot.** Keep the embedded cart in the app image (the browser
   patches the template before packing) so a PICO-8 cart can ship in the image —
   the slot geometry stays, only its contents and validation change (a PICO-8
   cart is ≤ 32 KiB, `.p8` or `.p8.png`; **superseded 2026-10-17: `.p8.png` only** —
   see §11).
4. **Cart upload over SysEx (required).** Add to the existing USB-MIDI path:
   a cart store in **our own app-slot space** (never in the protected ranges,
   allow-list + assert as the playbook demands) and a small SysEx protocol
   (BEGIN/DATA/END with CRC, plus list/run/erase), with the browser tool growing a
   *Cartridges* section that reuses the FM-1 installer's transport, discovery,
   progress and log code. Carts become swappable over USB without reflashing.
5. **SMK-37 packaging + installing.** GameDude ships an FM-1 package path only.
   The SMK-37 container/loader/installer knowledge comes from `kit/`
   (fm1-to-smk37: `docs/02-update-and-recovery.md`, per-model identities,
   installer model checks) — one package per model (`pro`, `elite`, `mke`) per
   the playbook's step 7, and the installer's resume path checks the model
   against the package.

## 4. Gates before anything is flashed

Playbook step 1/2/5 kept as-is: byte-identical baseline hashes for the untouched
base (recorded above), the flash allow-list + `_Static_assert`s + the second guard
in the RAM flash driver, bootguard/watchdog/UBOOT entry, host tests green after
every area, and an on-instrument acceptance record (port holds, identity answers,
one cart round trip) before any release path is opened.

## 5. Owner decisions — locked 2026-10-14

1. **Screen:** PICO-8's 128x128 is **stretched to the full 240x240** panel.
   Lucky match: femto8's own embedded branch (`__DA1470x__`) already renders
   128x128 → 240x240 stretched (2 output pixels per pixel horizontally, the
   `y & 7` row-pair pattern vertically) straight into an RGB565 framebuffer —
   upstream-tested code, so the port does not write a scaler. What our gdi seam
   must supply is the destination buffer and the async completion hook.
2. **Pads:** pads 8/9/10 = left/down/right, pad 1 = up (the owner-verified
   permutation in `src/smk37_game_controls.h`), pads 14/15 = X/O, panel PLAY =
   pause; remaining pads = cart slot 1 / slot 2 / restart.
3. **Keys:** keep the keybed rails as a second controller (whites 4/5 left,
   6 down, 7/8 right, blacks 4/5 up, chromatic 33/36 = O/X).
4. **Carts:** two 32 KiB slots, uploadable over SysEx, plus slot pads.
5. **Models:** **SMK-37 Pro only** to start (per-model packages later).

## 6. Platform surface femto8 actually asks for (read from the source, 2026-10-14)

femto8's `__DA1470x__` branch (= `OS_FREERTOS`) is the seam. What it calls,
and nothing else:

| Surface | Where | What we provide |
|---|---|---|
| `retro_heap.h`: `rh_malloc`/`rh_free` (via `#define malloc/free`) | `p8_emu.h:66` | allocator over `app.ld`'s `.pool` arena (`_pool_start`/`_pool_end`) |
| FreeRTOS shims: `vTaskDelay`, `pdMS_TO_TICKS`, `xTaskGetTickCount`, `xSemaphoreCreateBinary/Give/Take`, `portMAX_DELAY` | `p8_emu.c:145,195,556,801` | 24 MHz tick + IRQ-off critical sections |
| `gdi.h`: `HW_LCDC_LAYER_0`, `GDI_FORMAT_RGB565`, `gdi_set_layer_start/enable/src`, `gdi_get_frame_buffer_addr`, `gdi_display_update_async` | `p8_emu.c:246-252,567,741,777` | our 240x240 RGB565 buffer + the panel transfer completion |
| `gamepad` + `AXIS_*`/`DPAD_*`/`BUTTON_*` (via `ble_controller.h` → `gdi.h`) | `p8_input.c:418-448` | board input → PICO-8 buttons |
| `pthread.h`: `pthread_mutex_t` + lock/unlock | `p8_audio.c:141,215,352` | IRQ-off critical section (sound queue shared with the audio ISR) |
| `render_sounds(int16_t*, int)` — **mono**, 44100 Hz, 4 channels | `p8_audio.c:478` | called per emulated frame into the ring that feeds the DMA halves (stereo duplex copy, as `board.c` expects stereo) |
| `p8_load_ram(buffer, size)` + `p8_init()`/`p8_run()`/`p8_render()`/`p8_flip()` | `p8_emu.c` | the gp_* surface in `src/p8_port.c` |
| libc: `sprintf/snprintf/strlen/memcpy/qsort/…`, no `stdio` filesystem | `p8_emu.c`, `p8_lua.c` | GameDude's vendored freestanding libc + stubs |

Dropped from the device build: `src/main.c`, `src/p8_browse.c`, `lodepng/*`
(the browser extracts the raw cart from `.p8.png`, so the device never decodes a
PNG), SDL and DA1470x-GUI files (`p8_dialog.c`, `p8_options.c`, pause-menu GUI
files as far as they need a window). Kept: `p8_emu.c`, `p8_lua.c`, `p8_input.c`,
`p8_audio.c`, `p8_dsp.c`, `p8_parser.c`, `lexaloffle/*` (cart decompression),
`queue.c`, `strtcpy.c`, `lua/*`, `data/*`.

Test carts exist upstream: `femto8/carts/Celeste.p8.png` (+ `.p8`), `btntest.p8`,
`apitest1.p8`, `Desert Drift.p8` — the host milestone can use them as-is.

### Cartridge formats (measured against the engine, 2026-10-14)

The fixture is now ours: `tools/diagnostic_p8.py` writes `build/diagnostic.p8`
(colour bars, live button indicators, frame/time readout, a sweeping pixel and a
three-note arpeggio) — the PICO-8 counterpart of `tools/diagnostic.py`. Verified
by compiling **Femto8's own parser** (host, `tests/p8_cart_parse.c`):

- `p8_load_ram()` sniffs the buffer: PNG signature → PNG path (lodepng, so the
  device build drops `lodepng/`), otherwise the line-based **text `.p8`** parser.
- The text cart parses to 879 B of Lua, and `sfx 0` lands at `MEMORY_SFX`
  (0x3200) exactly as the audio decoder reads it: `pitch | waveform<<6 |
  volume<<9 | effect<<12`, header bytes at +64..+67 (mode, speed, loop start,
  loop end). Text sfx lines are 8 header characters then 32 notes of
  `pitch(2) waveform(1) volume(1) effect(1)`.
- The **official** `.p8.png` (exported with PICO-8 itself) parses to the same
  program and the same sfx block, with exactly one byte of difference: the export
  drops the code section's trailing newline (878 B vs 879 B). Compare cart
  programs with trailing whitespace normalised, never by raw bytes.
- `pico8_code_section_decompress()` handles both compressed headers (`:c:`,
  `:pxa`) **and** a header-less code section, which it treats as raw text
  (`memcpy` 0x3d00). The cart blob layout is `[0..0x42FF]` data
  (gfx/map/gfx2/sfx/music, `CART_MEMORY_SIZE` = 0x4300) then the code section,
  so a device-side `p8_load_cart_binary(blob)` is a `memcpy` plus that call.
- Therefore, at the time: text `.p8` for our fixture and host tests (no
  compression, no lodepng), and `.p8.png` for user carts, where the **browser**
  extracts the 32 KiB blob (as it already did for Game Boy ROMs) and the device
  unpacks it with the engine's own decompressor.
- **Revised 2026-10-17 (§11):** `.p8.png` only, and there is no text-`.p8` path anywhere —
  not in the device, not in the page, not in the build. What travels and what a slot
  holds is the **cooked cart** (magic + ROM + code section), which is exactly what the
  image carries; the browser still does the extraction. All five real `.p8.png` carts in
  `carts/` use the `\0pxa` code section, so `pico8_code_section_decompress()` is the
  branch that matters (raw is what the baked default cart uses).

Upstream quirk worth knowing, not used by us: `femto8/tools/png_to_p8.c`
segfaults on our exported `.p8.png` (it pins `m_lua_script`/`m_file_buffer` to
NULL); the parser we depend on handles the same file correctly.

### Open hardware question (measure, do not assume)

The panel is driven over SPI: a full 240x240 RGB565 frame is 115 KB, so frame
rate is transfer-bound. GameDude ran the panel at ~10 updates/s while emulating
at ~60 fps (its own trial doc). PICO-8 carts expect 30/60 fps visuals, so the
port must either raise the LCD baud, send only changed rows, or accept a lower
refresh with the emulator running at full speed. This is a measurement on the
unit, not a decision to guess now.

## 8. Reference: the original PICO-8 0.2.7, mined locally (2026-10-14)

There is no need to emulate the JieLi CPU to learn what PICO-8 *is*: the
authoritative engine is already on this machine and is not stripped.

- Target: `/Applications/PICO-8.app/Contents/MacOS/pico8`, version 0.2.7,
  universal Mach-O (x86_64 + arm64), sha256 `e7e93cf1e5da31e4…`; provider for the
  analysis is Hopper (`inspect_macho`, `analyze_function`, `search_strings`).
- **2,796 symbols, 450 of them in the engine's own `_codo_*` namespace** — the
  function map is readable by name, so targets can be picked instead of guessed:
  `compress`/`decompress`, `int6_to_ascii`/`ascii_to_int6`, `_codokey*`,
  `fread_lz4`/`fwrite_lz4`, `load_pico8_cart`/`save_pico8_cart`,
  `determine_cart_version`, `font1_dat`/`bitwise_font_dat`/`font_spacing`,
  `palette`/`set_palette`, `synth_generate`/`synth_generate_slice`/
  `apply_synth_filter*`, `play_sfx*`/`sfx_is_playing`/`get_sfx_playing_note`,
  `draw_sprite*`/`blit*`/`flip_screen`, `create_pod`/`load_pod_set*`.
- **Cart code compression, confirmed in the original**: the literal table
  `^\n 0123456789abcdefghijklmnopqrstuvwxyz!#%(){}[]<>+=/*:;.,~_` sits at
  `0x100123c21` (byte-identical to the table Femto8 uses, which is why our
  decompressor parity holds), and the engine pairs it with a 6-bit character
  encoder (`int6_to_ascii`/`ascii_to_int6`) plus a keyed stream (`_codokey`,
  `_codokey_buffer`). `_codo_decompress` itself is a **zlib** wrapper (LodePNG
  `inflate` + `adler32`, zlib header check `CM=8`, `CINFO<=7`), which is what the
  pod/asset containers use.
- **Asset container**: `pico8.dat` (11,243,101 B) is `CPODD` + 31 `CFIL` records
  (`CFIL`, u32, u32 size, 64-byte name, content). It holds the BIOS cart
  `pod/pico8_boot.p8` (36,987 B, plain text .p8), the built-in carts
  (`hello`, `api`, `auto2d`, `bounce`, `cast`, `collide`, `dots3d`, `drippy`,
  `jelpi`, `sort`, `wander`, `waves`), the BBS carts
  (`bbsc/*.p8.png`: celeste, frog_home, hug, archeos_2020, dusk, 8legs, embrace),
  the font/demo pods (`pod/f_pico8.pod`, `f_demos.pod`, `f_bin.pod`,
  `gfx1.pod`, `f_html5.pod`, `f_bbsc.pod`, `f_pico8_bigcaps.pod`) and
  `pod/def_fav.txt`. Extracted for study into
  `~/.hermes/cache/scratch/pico8-pods/`, deliberately **not** in this repository.

### What this means for "bring the binary along selectively"

Size is not the real constraint for the port — the runtime essentials are tiny:

| Need | Source | Size |
|---|---|---|
| Cart code decompression | already in the engine (Femto8) and now confirmed against the original | code only |
| Palette | 16 RGB triples, trivially re-derived | 48 B |
| Font | `font1_dat`/`bitwise_font_dat` tables | ~1–2 KB |
| Audio synthesis | the synth/filter *math*, reimplemented (not a data asset) | code only |
| BIOS cart, demos, BBS carts | proprietary PICO-8 content in `pico8.dat` | 36 KB–8 MB |

So the device build needs none of PICO-8's own content to run a cart: it needs
the codec, the palette, a font and the synth. The BIOS cart and the demo/BBS
carts are *optional content*, and shipping them inside a product is a licensing
decision for the owner (the same question as shipping PICO-8's font artwork),
not an engineering one. Our own build ships our diagnostic cart and lets the
owner upload their own carts, which sidesteps it.

### The original as a programmable oracle (the working method)

`pico8 -x cart.p8` runs a cart headlessly and **`printh()` goes to stdout**, so the
original engine can be *asked* instead of guessed — any address the VM can read,
plus every API's behaviour, can be dumped from the real thing:

```
$ pico8 -x build/probe.p8      # -> PROBE-START / pal .. / stat0 24.6016 / PROBE-END
```

What that gives us per target:

- **Screen (② palette/font, and the visual half of ③)**: the cart draws, then
  `printh`s the screen memory (0x6000, 0x2000 bytes = 128x128 4bpp) and the
  sprite sheet (0x0000). That is the exact framebuffer the original produced for
  a deterministic cart, so parity is a byte diff against our engine, not a
  table-layout archaeology exercise. `-export *.png` does **not** help here: it
  dumps static cart data (an empty `__gfx__` exports a black sheet) and 0.2.7's
  CLI only offers `.png` (spritesheet), `.wav`, `.html`, `.bin`, `.map.png`,
  `.lua.png` — no animated GIF.
- **Wrong guesses this corrected**: the ROM font is *not* readable at 0x5600 in
  0.2.7 (peek returns zeros there), and 0x5f10 is the palette *mapping* (identity
  0..15), not RGB values. The engine's runtime font is built at startup:
  `_init_bitwise_font` (0x1007af88) memsets 0x800 = 2048 B = 256 glyphs x 8 rows
  into `_bitwise_font_dat` and decodes it from `_codo_font1_dat`
  (0x10010d5fc, 768 B, 3 bytes per glyph: 4x6 pixels) with a character-map table
  in the tail. Femto8's `data/pico_font.h` instead carries a 8192 B indexed
  128x64 sheet (glyph 0 = space, blank), so the two are not comparable byte for
  byte — only by rendered output, which is what the screen dump does.
- **Audio (③)**: `-export build/oracle.wav` produced 22050 Hz mono audio and
  PICO-8 reported `sfx 0 :: ticks 256`, but the samples are **all zero** for both
  our cart and an upstream cart, so that path is not a usable audio oracle yet.
  Open: either a scripted interactive capture, or parity at the sfx-data level
  (memory 0x3200, which is fully readable) plus a hardware measurement later.

Artifacts: `tools/diagnostic_p8.py --oracle` writes the deterministic oracle cart
(16 palette bars, 128-glyph atlas, two known sfx, screen copy into the sprite
sheet); `build/probe.p8` and `build/probe_font.p8` are the runtime probes.

## 8-0. 우선순위 (사용자 지시 2026-10-15, 두 번째)

"SMK37Pro Simulator 가 먼저다. 로컬에서 작동하는 게임 시뮬을 자꾸 먼저 만들려고 하지
말아라."

즉 **기기 시뮬레이터가 먼저**이고, 호스트에서 도는 게임/PICO-8 쪽 작업(엔진 렌더·GB ROM·
femto8 통합)은 그 뒤다. 지금 순서:

  1. **SMK37Pro 시뮬레이터** — `.fwsc`를 받아 (a) 플래시가 안전한가(brick 게이트),
     (b) 부팅해 **패널이 켜지는가** 를 기기 없이 판정한다. 대상은 **스톡 v016과
     picovave 두 패키지 모두**.
  2. 그 위에서 PICO-8(femto8) 앱을 만들고 `.fwsc`로 패키징한다.

시뮬레이터에 이미 있는 자산:
  `tools/simflash_smk.py` (실제 로더/업데이터를 1MiB NOR+SMK 레이아웃에서 실행:
  쓰기 창·보호구역·쓰기 원장), `tools/package_smk37.py` (`.fwsc` 생성·검증),
  `tools/stock_plaintext.py` (스톡 앱 평문 복호), `tools/emu/pi32v2_cpu.py` +
  `pi32v2_decoder.py` (툴체인 디스어셈블 기반 실행기), `tools/emu/soc.py`
  (JL_TIMER4 0x10800 타이머, SPI1 0x11D00 + DMA 버스트 + DC 라인 0x50040 bit5),
  `tools/emu/st7789.py` (SPI 스트림 → 이미지 = **패널 점등 판정**),
  `tools/emu/capture_lcd.py`, `tools/emu/run_firmware.py`.

시뮬레이터의 현재 구멍(다음 작업):
  * **스톡 v016**: 39스텝에서 `0x1C058D2`(RAM)로 호출한다. 앱이 RAM을 채우지 않고,
    flash 헤드(0x0-0x3FFF)에도 RAM 주소 상수가 없다(전수 확인) → 그 바이트들은
    **SPL/부트 ROM 또는 복사 엔진**이 넣는다. 이 복사 모델이 스톡 부팅의 관문이다.
    (우리 앱은 ELF의 `.dsp_text`/`.data` 프리로드로 이 관문을 우회해 왔다.)
  * 패널 판정 경로는 준비 완료(`st7789.py`, 자체검증 PASS): 스톡/우리 앱이 SPI1로
    프레임을 밀어내는 순간 이미지로 판정된다.

## 8a. 결정: 앱 펌웨어에 GB 엔진·GB ROM을 쓰지 않는다 (시뮬레이터 뒤 작업)

사용자 지시(2026-10-15): "GB ROM 을 자꾸 집착하지 마라. 전혀 GB ROM, Gearboy 등은
필요없는 구현이다."

지금 `firmware/board.c`는 GameDude의 GB 엔진(프리빌트 `gp_*`)과 내장 GB ROM
(`cartridge.h`, 실체는 `FM1 SMK GB TEST` 진단 ROM)을 부팅 경로의 중심에 두고 있다.
이는 목표와 무관하다. 목표는 **`.fwsc`를 플래시해 화면을 켜는 시뮬레이터**이고,
그 위에 **PICO-8(femto8)** 을 올리는 것이므로 앱은 다음만 하면 된다:

    SoC 초기화(클럭/GPIO) → ST7789 초기화(우리 lcd_command/lcd_data/display_frame)
    → 스플래시 → femto8 프레임 → 패널

제거 대상(실측 목록, 2026-10-15 grep):
    firmware/board.c:10        #include "cartridge.h"
    firmware/board.c:206-212   boot_cart / boot_warm / pv_boot_stages (gp_load/gp_frame)
    firmware/board.c:310,313   gp_frame() / gp_pixels() / gp_frame_count()
    firmware/board.c:317,319   gp_frame_count() / gp_error()
    firmware/fm1_recovery.h:60-61  gp_frame_count/gp_error 진단 필드
    firmware/pv_splash.h       "cartridge/engine가 로드되는 동안" 서술

디스어셈블 실측이 이를 뒷받침한다: 부팅 스테이지 테이블 `pv_boot_stages`는
`{boot_cart, boot_warm}` 두 개뿐이고, `boot_cart`은 내장 ROM 헤더의 크기 코드를
검사한 뒤 `gp_load(cartridge, rom_size)`를 호출한다. 그 ROM의 **GB 헤더 체크섬이
틀려 있어**(계산 0xD8 ≠ 파일 0x0F) `gp_load`가 6을 반환하고, 펌웨어가
`0x2004b1e if (r4 == 0)` 실패 → `0x2004b20 goto -2`(자체 halt)로 멈춘다.
즉 GB 경로를 손보는 일은 목표에 기여하지 않는다 — 그 경로 자체를 없앤다.

시뮬레이터 쪽에서 계속 유효한 자산(GB와 무관한 실제 하드웨어 계층):
    pi32v2 인터프리터(툴체인 디스어셈블 기반), JL_TIMER4(0x10800) 타이머 모델,
    SPI1(0x11D00) 모델, SPI 바이트 스트림 캡처 = **화면 판정의 입력**.

## 8b. What the simulator is for (scope — do not drift)

The request is a **`.fwsc` flashing simulator**: prove what a given package does to a
unit without a unit. It is *not* a PICO-8 engine simulator, and engine-render parity
(see PARITY.md) does not substitute for it.

How it is built, and why no CPU emulator is needed: the flashing path is **our own
portable C** (`firmware/loader/ldr_core.c`, `firmware/src/ota.c`), so it is compiled
for the host and run against a faithful memory model — a 1 MiB NOR with the SMK map,
the four protected ranges as tripwires, and the real SysEx framing. What cannot be run
(the SPL in the mask ROM, the stock app's check step) is modelled **behaviourally from
`kit/docs/02-update-and-recovery.md`**, and the model is labelled as a model.

Today (`tools/simflash_smk.py`): stage 1 (the app-side check step, staging and record)
and stage 2 (the loader session) each run the real code and pass — over our own image
and over the unit's own stock firmware — and the tool now prints a region-named write
ledger, a protected-region check and the next power-on verdict:

    over the unit stock firmware: app area 626,688 B erased/programmed, record window
    1 op, protected regions touched: NONE, verdict: the record is gone -> SPL boots the app

**Completion means the panel lights up — so the simulator must execute the
firmware.** "Bricked" is decided by whether the screen comes up after a flash, and
nothing short of running the flashed code decides that. An earlier version of this
section listed only wiring two host tests together; that was the easy part and it is
**not** the simulator. The hard path, in order:

0. **The acceptance image is readable now** (`tools/stock_plaintext.py`): the stock app
   area is one SFC region from flash 0x4000 (32-byte blocks, position-dependent key, so it
   must be decrypted in one pass), its entry is flash 0x4120 = XIP 0x2000120 with the
   `_start` signature `04 81 80 00`, and the decrypted entry disassembles with **zero
   unknown instructions** — the same layout this project's own build uses. Vendor material
   stays in scratch.
1. **A pi32v2 interpreter.** The ISA is variable length (2/4/6/8-byte opcodes) and the
   toolchain disassembles it (verified: `_start` at `0x2000120` reads `cli`,
   `sp = 0x1C80000`, `call gb_cstart`). Validate it on **our own** image first, where
   the source tells us what must happen.
2. **A SoC model up to the first panel write.** The panel is an **ST7789V on SPI1**,
   not a memory framebuffer (`kit/docs/01-hardware.md`), so the screen check is
   *decode the SPI1 byte stream into an image*. That needs clock/PLL, timers and IRQ,
   GPIO defaults, watchdog feeding, and the SPI1 + DMA path — nothing more, and not
   the whole SoC. Register definitions come from the local AC79 SDK
   (`include_lib/driver/cpu/wl82/asm/`: clock, dma, gpio, hwi, irq, spi, osd).
3. **A boot model**: the SPL decides app vs loader from the head and the update
   record, and it lives in the mask ROM (not in flash, not executable here), so its
   decision stays a labelled behavioural model taken from
   `kit/docs/02-update-and-recovery.md`.
4. **Then the scenario**: both packages — the unit's own `SMK-37_Pro_016.fwsc` and
   `picovave.fwsc` — over the model flash, booted, with a screen verdict and the
   region-named write ledger that already exists.

The stock app is SFC-encrypted in flash, but the chip key is known, so its plaintext
can be produced for disassembly and for the acceptance run. Vendor material stays in
scratch (`~/.hermes/cache/scratch/`), never in this repository.

What the current tool already proves (do not mistake it for the above): every erase
and program the real update code performs, by region, with the protected ranges
untouched, and what the next power-on would do about the record.

## 9. Milestones

| # | Milestone | Gate |
|---|---|---|
| M1 | Host seam proof: `src/p8_port.c` + host shims implement the gp_* surface over femto8; a real cart (Celeste) loads and runs N frames headlessly | new `tests/p8_runtime.c` passes: cart validation, N frames advance, pixels non-blank and geometry exact (240x240 stretch), audio non-silent with the exact per-frame sample count, 6-button input + pause/reset, no leaks in the pool arena |
| M2 | Device build: `python3 tools/build.py pico8` produces an app image through Femto8's own sources in the GameDude body; all baseline suites still green | layout asserts (entry stub, sections, pool, CPU1 stacks) + bootguard/watchdog/UBOOT symbols present |
| M3 | Cart store + SysEx protocol (BEGIN/DATA/END + CRC, LIST, ERASE) over the SLOOP store, in our own app-slot space (allow-list + assert) | ran in the **simulator** already (`src/picovave_cart.h`: host protocol test + `sim_test.mjs` uploads a `.p8` and it boots); the device half lands with M2 |
| M4 | Browser tool: *Cartridges* section (upload / list / run / erase) reusing the installer transport, discovery and progress code | JS suite green; the tool refuses mismatched identity |
| M5 | SMK-37 package + handover (Pro only), with flash-backup and recovery gates | owner flashes; on-instrument acceptance = holds + identity + one cart round trip, reproducible after power-cycle |

## 10. Status — the browser simulator boots a cart (2026-10-16)

This pass did the thing the plan calls the seam, and stopped at the first gate: **the wasm
simulator runs femto8**. It is the milestone-1 "host seam proof", done in the browser harness
instead of a separate host shim (the simulator already was the ROM's code; the engine now lives
in that same body).

What is on disk now, and what was measured:

- `femto8/` — the engine, vendored from pin `f9e5097` (see `femto8/README.picovave.md`).
  Kept: `p8_emu.c`, `p8_lua.c`, `p8_parser.c`, `p8_input.c`, `p8_audio.c`, `p8_dsp.c`,
  `p8_cstore.c`, `queue.c`, `strtcpy.c`, the embedded **Lua 5.4**, and `src/lexaloffle/`.
  Dropped: `main.c` and the SDL GUI (`p8_browse`, `p8_dialog`, `p8_options`, `p8_pause_menu`)
  and `lodepng` — replaced by `femto8/stub/` (no-op dialogs; a LodePNG that always fails).
  Neither target decodes a PNG (see §11): the page unwraps the `.p8.png` and what the
  device takes is the cooked cart, so PNG decoding is gone from both.
- `femto8/seam/` — the surface the `__DA1470x__` branch asks for: `gdi.h` (the RGB565 layer),
  `FreeRTOS.h`/`task.h`/`semphr.h`, `retro_heap.h`, `ble_controller.h`, `pthread.h`.
  `femto8/seam/seam_sim.c` implements it for wasm (the panel buffer, the wasm heap, the pad
  bitmask, the RTOS shims). This is the piece the device build swaps out.
- `src/picovave_p8.h` — the shared app: boot, then one `p8_step()` per 1/30 s tick, with the
  owner-verified SMK-37 Pro control permutation
  (from `ref/gamedude src/smk37_game_controls.h`). `sim/sim.c` includes it unchanged and swaps
  only the HAL, exactly as the hello skeleton did.
- `femto8/src/p8_emu.c` / `p8_emu.h` — the first of the documented patches (the full list is
  `femto8/README.picovave.md`, nine as of 2026-10-17): three compile fixes the shipped
  `__DA1470x__` path needs (`color_pal`; the missing `p8_print_helper.h` include; the missing
  `retro_heap.h` include), plus
  a **frame-driven entry** (`p8_begin()`/`p8_step()`, `jmpbuf_restart` made extern) so a superloop
  — the device, and the wasm audio worklet — never blocks in `p8_main_loop()`. A cart's
  `reset()`/`run()` longjmps to `jmpbuf_restart`, which the app owns with a per-frame `setjmp`.
- `src/picovave_p8.h` — the shared app: `picovave_boot()` then one `p8_step()` per 1/30 s tick,
  with the owner-verified SMK-37 Pro control permutation (from `ref/gamedude
  src/smk37_game_controls.h`). Boot is **re-runnable** (the page's POWER-ON calls `sim_init()`
  again): `p8_init()` allocates once and asserts `!m_initialized`, so the engine is brought up
  under a one-time guard and each boot just re-loads the cart (`p8_load_cooked` + `p8_begin`, which
  is what `p8_run()` does on a cart reset). `sim/sim.c` includes it unchanged and swaps only the
  HAL, exactly as the hello skeleton did.
- `tools/build_sim.py` — compiles the whole engine (`-D__DA1470x__ -DOS_FREERTOS -DENABLE_AUDIO
  -sSUPPORT_LONGJMP=wasm`, native wasm EH so there is no JS `invoke_*` glue) and bakes
  `tools/pvcart.py`'s cooked cart into the wasm as a C array. `sim/sim_test.mjs`, `sim/worklet.js` and
  `web/sim/index.html` stub the few WASI imports the engine's libc names (they write through
  out-pointers, so `() => 0` is not enough). The build stamps its inputs and skips the emcc
  recompile when nothing changed — minutes, on an emulated toolchain.
- `src/picovave_cart.h` — the cart store and the SysEx upload protocol, **shared by the device
  and the simulator** so the two cannot drift. The store is the SLOOP flash region
  `0x9D000..0xBB000` (120 KiB, 4 KiB erase sectors): two sector-aligned slots of 61,440 B, each a
  64-byte header (magic, name, length, CRC16, `seq`) then the payload. The wire protocol is
  `F0 22 24 35 7E <cmd> … F7` — BEGIN/DATA/END (upload **and** run), ERASE, LIST — with
  OK/ERR/INFO replies; a 16-bit CRC rides in **three** 7-bit bytes (two would carry only 14 bits).
  Uploads write the slot that is **not** running, and boot runs the newest `seq`, so an
  interrupted upload can never disturb the cart that is playing, and the older slot is a rollback.
  A DATA frame SPLITS its payload 8 bits into 7 (the same packing the package protocol uses);
  `off`, `n` and the CRC are raw counts.
  - **Updated 2026-10-17 (§11):** that payload is the **cooked cart** a `.p8.png` carries, with a
    4-byte magic: 32,804 B, and the only length BEGIN accepts (anything else is `ERR 7`). It is
    binary, which is *why* the 8→7 split exists. `.p8.png` is no longer "a browser-side preview";
    it is the cart format.

**The cart upload is proven in the simulator.** `sim/sim.c` implements the device's three store
calls (`hh_store_read`/`erase`/`write`) over a RAM array and includes the same header, so the wasm
runs the firmware's own parser. It exports `sim_sysex_in()`/`sim_sysex()` (the page writes one
frame in and calls it). `sim_test.mjs` drives the protocol end to end — a bad CRC is refused, a
good BEGIN + DATA× + END commits a slot and reboots onto it (the panel changes from the baked
default cart to the uploaded one), and LIST reports the slot — and `run_page.mjs` does the
same through the page's own *카트(.p8.png)* control. That control now names its target, the way the
DEVICE box does: **SIMULATOR** writes the wasm's store, **FLASH(USB)** sends the SAME frames over
Web MIDI to a real SMK-37 Pro — same `cartFrames()`, same slot alternation, same
`src/picovave_cart.h` parser on both ends — so "it uploaded in the browser" says something about
the device, which it did not before. `tools/build_sim.py` now hashes headers as well as sources, so
editing one can no longer reuse a stale wasm.

Measured on this Mac (emscripten/emsdk docker, `-O2`): `sim.wasm` = 284,746 B ≈ 278 KiB.

**The gate is green.** `python3 tools/build_sim.py --engine docker` → `rc=0`, with:
`check_api.mjs` ok (the page names only exports the wasm has), `run_page.mjs` all ok (the page's
own code runs — boot, install into the modelled flash, POWER-ON, the DEVICE box, the panel
inputs, the v016 foreign-ROM branch), and `sim_test.mjs` all ok — boots `btntest.p8`, 60 frames
paint 87 lit pixels, a pad-15 (O) press changes 18 px and a pad-8 (LEFT) press changes 24 px, the
audio rate is 44100 and `sim_render` advances the audio clock. `build/sim/screen.png` shows the
cart, so the wasm **is** the engine, running a real cart.

**Done (2026-10-17).** The device body (`src/picovave.c`) and the host preview (`host/preview.c`)
now run the same `picovave_p8.h` + vendored engine, and `tools/verify_final.py` is **green**
(136 checks) — including the wasm-vs-host-preview pixel parity, both for the idle screen and for a
scripted sequence of presses (the third build of the same code). What that pass added:

- `femto8/stub/device/` — the device's mini-libc (`stdio.h`/`stdio.c`/`syscalls.c`) and a
  boundary-tag allocator over the linker's POOL region (`alloc.c`), because the JieLi `pi32v2`
  newlib has no stdio/malloc; `femto8/seam/seam_device.c` is the device seam.
- `src/picovave.c` — the device body: the `hh_*` HAL, the panel push (`gdi_display_update_async`,
  which byte-swaps the engine's native RGB565 into the ST7789's big endian **in place**), the ALNK0
  ring audio fed by `render_sounds()` from the main loop, and the USB/OTA/cart glue.
- `host/preview.c` + `host/seam_host.c` — the host consumer, so parity has a third, independent
  build of the same engine and app layer.
- `platform/firmware/app.ld` — RAM `.data/.bss` 144K and POOL `0x01C2C000`+`0x48000`; everything
  above the engine arena is the Lua heap. Measured: `.data+.bss` 144,696 B of 147,456,
  `.pool` in use 0 B, Lua heap 293,888 − 271,872 = **22,016 B**. The flashing simulation
  (the DEVICE box) is independent of this and was already green.

**Buffer sizing — decided and applied.** The save scratch is gone and the big buffers are
right-sized in `femto8/src/p8_emu.h` (one source of truth, so a cart that exceeds a cap fails the
same way on both targets): `FILE_BUFFER_SIZE` 128K→64K, `DECOMPRESSION_BUFFER_SIZE` 128K→32K,
`LUA_SCRIPT_SIZE` 128K→64K, and `m_temp_lua_script` (upstream's second 64 KiB, used only by
`cstore()`/save) is no longer allocated — `p8_lua.c`'s `cstore()` now refuses when the target has
no writable cart filesystem. That is an **arena of 271,872 B**: 64K PICO-8 memory + two 17,152 B
cart buffers + 8K overlay + 64K file + 32K decompress + 64K Lua script. The simulator was rebuilt
with these values and is still green, so sim and ROM stay identical.

  - **Further 2026-10-17 (§11):** with the cart format settled, the three remaining scratch
    buffers of the text/`fopen` paths (`m_file_buffer`, `m_decompression_buffer`,
    `m_temp_cart_memory`) are **not allocated either** — 114,688 B more for Lua. The arena is
    **156,416 B** (64K memory + 17,152 cart + 8K overlay + 64K Lua script) and the Lua heap is
    **137,472 B** (was 22,016 B). This is what makes real carts — baba's code section expands to
    45,053 B of Lua — fit at all.

**The engine already compiles for the device.** Ran the real toolchain: all 43 engine objects
build for `pi32v2` (`-D__DA1470x__ -DOS_FREERTOS -DENABLE_AUDIO -isystem $TC/pi32v2/include`).
The only source that did not was `p8_lua.c`, whose cart listing uses `<dirent.h>` — newlib on
pi32v2 has none; `femto8/seam/device/dirent.h` supplies it and `opendir()` reports an empty
directory. Their footprint: `.text` 112,304 B + `.rodata` 48,820 B (both XIP, the slot is
~8.9 MB), `.data`+`.bss` under 1 KB. So the RAM cost really is the arena + the panel buffer,
not the engine's own globals.

**What the device link needed (done — kept for the record).** The device links with
`pi32v2/bin/ld` and no libc, so the engine's `setjmp`/`longjmp`/`snprintf`/`memcpy` had to be
brought in: newlib is present (`pi32v2/lib/libc.a` exports `setjmp`/`longjmp`/`memcpy`) and the
link adds the engine objects plus `-L$TC/pi32v2/lib -lc -lm -lcompiler_rt` and a device seam
(`gdi_get_frame_buffer_addr` → the 240×240 RGB565 panel buffer, `gdi_display_update_async` →
`lcd_blit`, `rh_malloc`/`rh_free` → the POOL arena, the FreeRTOS/pthread shims, `gamepad`), the
ALNK0 ISR switched from the square wave to femto8's `render_sounds()`, and
`hh_cart_data()`/`hh_cart_size()` from the SLOOP store with the three flash-backed store calls
behind `src/picovave_cart.h`. **RAM ended up:** 442,368 B (RAM 96K + POOL 344K) − 271,872 B (arena)
− the panel buffer, with `.data+.bss` 144,696 B of 147,456 and 22,016 B left for the Lua heap —
enough for small carts. (`parse_cart_ram` rewrites its input in place, so the cart cannot be
parsed straight from XIP; it is copied once into a RAM buffer at load. With §11's cooked cart that
copy is a read-only `memcpy` from the store, and the parse step disappears entirely.)

## 11. Status — the cart is `.p8.png`, and only that (2026-10-17)

The port ran; the cart *format* did not match the requirement. Carts are `.p8.png` (the compressed
form PICO-8 itself saves and the boards hand out) and only that — a text `.p8` is no longer
accepted anywhere. This pass settled the format, removed the text path from the device, the page
and the build, and reclaimed the memory the text path had been holding.

**What was measured first.** All five real carts in `carts/` (`baba_demake-9`, `bunnysurvivor-9`,
`fighter_street_ii-1`, `flipside_-1`, `terra_1cart-44`) are 160×205, bit depth 8, colour type 6
(RGBA), non-interlaced, and their code section header is `\0pxa` — **PXA**, the femto8 decompressor
the device compiled but had never exercised. The label's low two bits per channel are the cart, so
the "cooked cart" is 32,800 B: `[0, 17152)` cartridge ROM (verbatim — no nibble swap, unlike the
text parser) and `[17152, 32800)` the code section.

**The division of labour.** The **page** decodes the PNG (`web/sim/p8png.mjs`: signature/IHDR
checks, zlib inflate via `DecompressionStream`, PNG unfilter, `(r&3)|((g&3)<<2)|((b&3)<<4)`) and
sends the cooked cart. The **device** only decompresses the code section, which it already
vendors (`pico8_code_section_decompress` via `p8_load_cooked()`), so **no encoder and no PNG
decoder exist in the firmware**. Decoding on the device was rejected on arithmetic, not taste:
lodepng materialises 160×205×4 = **131,200 B** of RGBA, and the Lua heap was **22,016 B**.

**Why the text path is gone, not just discouraged.** gfx/map/sfx/music are hex in a `.p8`, so it
is 2–3× the size of the compressed cart its author saved — no real game fits a slot as text — and
two accepted formats for one cart is precisely the confusion to avoid. Its removal also deletes
the last user of three big buffers.

**The payload.** `PV_CART_LEN` = 4-byte magic `"PVC1"` + 32,800 B cooked = **32,804 B**, the only
length BEGIN accepts (`ERR 7` otherwise), and a slot whose payload lacks the magic is skipped
rather than run. The cooked cart is binary, which is why DATA frames split 8→7 bits (the CRC16
over the raw payload makes a committing END a byte-for-byte proof of the split).

**The reclaim (this is the part real carts needed).** `p8_init()` no longer allocates
`m_file_buffer` (64 KiB), `m_decompression_buffer` (32 KiB) or `m_temp_cart_memory` (17,152 B):
every user of them is `fopen`-guarded (device `fopen` always fails) or `cstore`-guarded. The arena
drops from 271,872 B to **156,416 B**, so the Lua heap goes 22,016 → **137,472 B (6.25×)**. The
expansion also targets `m_lua_script` (the full 64 KiB), not upstream's 32 KiB
`m_decompression_buffer`: baba expands to **45,053 B** of Lua (bunny 49,966, terra 40,944,
fighter 39,786, flipside 8,268), so the upstream pairing is a latent overflow.

**The default cart.** `tools/pvcart.py` assembles the baked default as a cooked cart too — zero
cartridge ROM plus a **raw** code section (the pre-0.1.7 form) holding the button test that used
to live in `carts/btntest.p8`. So no build step parses a `.p8` either, and even the built-in cart
is the same bytes a `.p8.png` would carry. It is baked as a bare cooked cart (no wire magic) —
the store validates the magic, the engine re-checks the size.

**Verified, not assumed.** The JS extractor and the Python oracle agree **byte for byte on all
five real carts**; and running the *device's own* `pico8_code_section_decompress` over each
extracted blob yields clean Lua (kind=PXA, no garbage, the lengths above). `sim_test.mjs` now
uploads a **real `.p8.png`** over the SysEx protocol (the page's own module does the unwrapping),
commits it, reboots onto it and asserts the engine ran the PXA code section; a text `.p8` is
asserted **refused**; `run_page.mjs` does the same through the page's `.p8.png` control and
asserts the refusal there as well.

**All five real carts run (host, cooked path):** baba is you demake, bunnysurvivor, fighter
street ii, flipside, terra — `p8_load_cooked() == 0`, `p8_begin() == 0`, 30 frames, nonzero
pixels. Getting there exposed one **engine** gap worth recording, because it is not this port's
invention: femto8's vendored Lua accepts PICO-8's `?` (print shorthand) only as a *statement*,
while PICO-8's preprocessor expands `?` to `print(` and closes the parenthesis at the end of the
line — so `local w = ?text, 0, -1000` (how a cart measures a string, since `print` returns the x
after the text) is legal PICO-8 and was a **syntax error** here. `baba_demake-9` does exactly
that and failed to load at line 743. `femto8/src/lua/lparser.c` now takes `?` in
`primaryexp` too (patch #6 in `femto8/README.picovave.md`): `shortprint()` gained an
out-parameter, a statement discards the call and an expression keeps its one result. Additive,
and the statement path is unchanged; `verify_final.py` asserts the patch is still there, so a
future re-vendor cannot silently drop it and break real carts.

**Uploading cart after cart found a second engine bug — in picovave's own core use case.** The
wasm suite now uploads *every* cart in `carts/` one after another, and the third one died at
frame 0 with "attempt to call a nil value" (on the host too: any cart after a cart that defines
`_update`). Cause: `lua_init_script()` only ever *sets* the cached callbacks
(`m_lua_update`/`_update60`/`_draw`/`_init`), so a cart that defines no `_update` inherited the
previous cart's pointer, whose function object the new load had already collected — and because
an upload **is** a boot (`picovave_boot()` after the END), the device would have hit this on the
second cart a user ever uploaded. `lua_init_script()` now clears the four (and resets `m_fps`),
which is patch #7. Both bugs are exactly the kind a "one cart per process" test suite cannot see.

**Measured limit — and the reclaim that answered it (2026-10-17).** The format works end to end, but
the device's memory is the real gate, so it was measured rather than assumed. The pool is 294,912 B
(minus a 1,024 B red zone) and the engine used to hold **156,416 B** of it for the whole time a cart
ran: a 64 KiB PICO-8 address space, a **17,152 B copy of the cartridge ROM**, an 8 KiB overlay
screen, and a **64 KiB Lua source buffer** — plus the Lua state and its ~200 API closures (~44 KB).
That is 207,744 B before a cart draws a pixel, leaving ~86 KB, which makes the hardware a toy. Two
of those four buffers did not have to be RAM at all (patches #8/#9 in
`femto8/README.picovave.md`):

- **the cartridge ROM is read out of the cart itself.** `m_cart_memory` points into the caller's
  cooked cart — the device's SLOOP slot through XIP, the simulator's baked array — instead of a
  17,152 B copy. Nothing writes it: `cstore()` is refused before its write, `reload(name)` (which
  parses into the buffers patch #5 leaves NULL) now refuses the same way, and Lua's
  `reload(dest, src, len)` still reads the ROM exactly as before;
- **the Lua source buffer is transient.** `p8_begin()` expands the code section into it, hands it to
  `luaL_loadbuffer()`, and frees it the moment Lua owns the compiled chunk
  (`p8_materialize_script()` / `p8_release_script()`). A cart `reset()` re-materialises it, so a
  restart still restarts from the cart's own source.

For the whole time a cart runs the engine's share of the pool is now **73,728 B** (64 KiB + 8 KiB);
a *load* adds the 64 KiB source buffer while it lasts, and that is the whole engine footprint.

**The rest has to be measured, and with the device's numbers.** Lua costs 10–20× the source, so what
a cart needs cannot be computed — it has to be run. `sim/heap_probe.c` (compiled into the wasm by
`tools/build_sim.py`, on top of femto8's own `malloc`→`rh_malloc` mapping, exactly as the device has
it) renames the engine's and Lua's allocator to counters and charges every request **n + 16**: the
boundary-tag allocator in `femto8/stub/device/alloc.c` spends an 8-byte header + a 4-byte footer,
rounded to 8, on *every* block — and a cart is thousands of small Lua blocks, so this is not a
rounding detail. The wasm is 32-bit like the device; a 64-bit host overstates the same carts by ~2×,
because Lua is pointer-heavy. 300 frames (10 s of cart time), one cart per **fresh** instance
(an empty store, so the baseline is the engine's own footprint), against the device's 293,888 B:

| cart | measured peak | vs the 287 KB pool |
|---|---|---|
| baked built-in cart | 108 KB | fits, 179 KB spare |
| `flipside_-1` | 204 KB | fits, 83 KB spare |
| `fighter_street_ii-1` | 572 KB | 2.0× over |
| `baba_demake-9` | 622 KB | 2.2× over |
| `terra_1cart-44` | 626 KB | 2.2× over |
| `bunnysurvivor-9` | 2,117 KB | 7.4× over |

(A few KB move run to run — GC timing — so these are rounded. `sim_test.mjs` asserts the built-in
cart fits with room to spare and that `flipside` fits, and reports all five.) A cart over the pool
fails at load with an out-of-memory status (`picovave_status = -1`); the device stays alive and
takes another upload — not a crash. So picovave on the SMK-37 Pro runs **small** carts; the browser
and host previews have megabytes and run all five. Squeezing modestly more (a smaller overlay, a
smaller source buffer, freeing between uploads) can win tens of KB — not the ~1 MB baba needs.

**And the page says so before anyone uploads one.** Choosing a `.p8.png` now runs it in a
**throwaway wasm instance** — its own store, its own heap, its own panel — over the same SysEx
frames, for 300 frames, and prints the verdict in the cart line: `기기 OK: 실측 peak 204 KB / 287 KB
(여유 83 KB)` or `⚠ 기기 불가: 실측 peak 622 KB > 기기 풀 287 KB (시뮬레이터 전용)`, with the status
line amber rather than a claim of success. The running simulator — and a device on the other end of
Web MIDI — is never touched by that measurement, so a cart far too big for the hardware is still
perfectly good on the simulator. The device limit is a fact the page *reports*, not a rule it
enforces: the upload path is unchanged. `run_page.mjs` asserts the verdict appears and that an
oversized cart warns, and `verify_final.py` asserts the page's budget **is** the firmware's
(`POOL_LEN` minus `PV_POOL_GUARD`), not a second constant that can drift.




