# vavemod — SMK-37 Pro 커스텀 펌웨어 (picovave)

> **상태: PICO-8(femto8) 이식 — 기기·시뮬레이터·호스트가 같은 엔진을 돌린다, 2026-10-17.**
> 플랫폼(`platform/`: HAL · 부팅 · USB/OTA · 복구 · 메모리맵 · 로더 · 패커)은 Makkuro37에서
> vendor한 그대로이고, **앱 계층이 이제 PICO-8이다.** 공유 앱 [src/picovave_p8.h](src/picovave_p8.h)가
> vendored femto8 엔진을 프레임 단위(`p8_begin`/`p8_step`, 카트의 `reset()`/`run()`은 프레임마다
> `setjmp`로 되감는다)로 돌리고, 카트는 SLOOP 스토어(0x9D000..0xBB000)에 저장되며 호스트가 USB
> SysEx(`F0 22 24 35 7E ..`)로 **`.p8.png` 카트를 업로드**한다 — 카트 형식은 **`.p8.png` 하나뿐**이다
> (텍스트 `.p8`은 받지 않는다). 이미지는 **브라우저가 풀고**(`web/sim/p8png.mjs`) 기기는 그 안의
> 코드 섹션만 편다(femto8의 PXA/`:c:`/raw 해제기) — 기기에서 PNG를 해독하려면 RGBA 131,200 B가
> 필요했고, 그때 기기의 Lua 힙은 22,016 B였다. 지금 기기가 엔진·Lua에 주는 아레나는 **414,184 B**
> (Lua 힙 **413,160 B** = 아레나 − red zone 1,024)이고, **카트가 도는 동안 엔진이 붙잡는 몫은 73,728 B**
> (64K PICO-8 주소 공간 + 8K 오버레이)뿐이다: 카트 롬은 카트 자체(스토어)에서 읽고, Lua 소스 버퍼는
> 로딩 중에만 살아 있으며 **카트의 스크립트 크기만큼만** 잡는다(패치 8·9·10). 그래서 **풀의 거의 전부가
> Lua**인데, Lua가 얼마나 필요한지는 소스 크기로 알 수 없다(10~20배) — 시뮬레이터의 wasm이 **기기와
> 같은 할당기**(`femto8/stub/device/alloc.c`)를 링크해 **기기 아레나에서 그 카트를 실제로 돌리고**,
> 최고점과 거부 횟수를 센다. **페이지가 업로드 전에 그 실측으로 "이 카트가 기기에서 도는가"를 먼저
> 말해준다.** 실측(300프레임, 기기 아레나): 내장 카트 110 KB(거부 0) · flipside 167 KB(0) ·
> loop-4 300 KB(0) · **baba(45 KB) 402 KB — 거부 5회, 실행**(아레나 끝, 가끔 GC 히치) · fighter
> 396 KB(99회, 눈에 띄게 끊김) · terra 379 KB(12만회, 실행은 되지만 사실상 못 씀) · bunnysurvivor는
> 로드 실패 → 기기가 **기본 카트로 폴백**하고 멈추지 않는다. (시뮬레이터/호스트 프리뷰는 메모리가 커서
> 5개 전부 실행된다.) 기기 펌웨어 · wasm 시뮬레이터 · 호스트 프리뷰가 **같은 엔진과 같은 앱 계층**을
> 컴파일하고, 그 화면은 픽셀 단위로 일치한다. Game Boy 경로(peanut-gb 등)는 쓰지 않는다.

M-Vave SMK-37 Pro(AC791N)용 커스텀 펌웨어 패키지 형식을 역설계하고,
**PICO-8(femto8) 카트를 실행하는, 입력(18 버튼 · 8 엔코더 · 37 건반 · 16 패드 · 4 페이더 ·
피치/모드 휠)을 그대로 게임 패드로 매핑하는, USB-MIDI로 인식되고 호스트가 SysEx로 카트를
업로드할 수 있으며 실기에서 다시 플래싱할 수 있는(절대 벽돌이 되지 않는) picovave.fwsc** 를 빌드한다.

그리고 Makkuro37의 가장 값진 구조를 그대로 가져왔다: **브라우저 시뮬레이터가 펌웨어와 같은
소스를 컴파일한다.** 앱 계층과 카트 저장소/프로토콜이 공유 소스(`src/picovave_p8.h`,
`src/picovave_cart.h`, `src/picovave_input.h`, 그리고 화면용 `src/picovave_gfx.h`)에 있고,
그 아래 엔진은 vendored femto8(`femto8/`)이다. 시뮬레이터 화면은 흉내가 아니라 실제 롬이 그리는
픽셀이다. 두 가지를 픽셀 단위 일치 검사로 고정해 두었다: **같은 화면**(입력 없음)과
**같은 입력**(같은 버튼/건반/엔코더/패드/페이더/휠을 양쪽에 먹인 뒤) — 그 비교 상대는 호스트
프리뷰(`host/preview.c`)이며, 셋 다 같은 엔진과 같은 앱 계층을 컴파일한다.

기기 빌드의 플랫폼(HAL·부팅·USB/OTA·패커)은 **Makkuro37에서 vendor한 사본**을 `picovave/platform/`에
넣어 picovave가 **자기완결**로 빌드한다 — picovave는 빌드 시 `repo/`를 전혀 참조하지 않는다(그래서 USB
장비명도 `picovave`). 그리고 `picovave.fwsc`는 **시뮬레이터 레벨에서 확인**할 수 있고, 같은 원리로
**실기에 플래싱**할 수 있다. 시뮬레이터도 자기완결이다 — 페이지·워클릿·wasm 모두 picovave 것이고
`repo/`를 참조하지 않는다.

- 구조 분석 전문: [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md)
- 산출물: [build/picovave.fwsc](build/picovave.fwsc)
- 시뮬레이터: [build/sim/index.html](build/sim/index.html) — `python3 -m http.server -d build/sim 8738`

## 하나의 소스, 세 개의 소비자

    picovave/femto8/               vendored PICO-8 엔진(femto8) + seam              ← 하드웨어 없음
    picovave/src/picovave_p8.h     앱 계층 (부팅·프레임 루프·setjmp·입력→gamepad)   ← 하드웨어 없음
    picovave/src/picovave_cart.h   카트 저장소 + SysEx 업로드 프로토콜 (슬롯/CRC/7-bit) ← 하드웨어 없음
    picovave/src/picovave_input.h  입력 모델 (버튼/건반/엔코더 + 패드·페이더·휠)     ← 하드웨어 없음
    picovave/src/picovave_gfx.h    128x128 RGB565 캔버스 (OTA/복구/크래시 화면)      ← 하드웨어 없음
            │
            ├─ picovave/src/picovave.c     + platform/firmware/{hal,src}
            │                              + femto8/seam/seam_device.c + femto8/stub/device/  실제 기기
            ├─ picovave/sim/sim.c          + picovave_sim_hal.h + femto8/seam/seam_sim.c   브라우저 (wasm32)
            └─ picovave/host/preview.c     + host/seam_host.c                             호스트 렌더 / 입력 파리티

기기 빌드는 **Makkuro37의 HAL·부팅·USB/OTA 스택과 레시피를 `picovave/platform/`에 vendor한
사본**으로 쓴다(`fm1_time.h`, `fm1_sys.h`, `fm1_lcd_hw.h`, `lcd.c`, `smk_input.h`, `fm1_timer.h`,
`fm1_audio.h`, `usb.c`, `ota.c`, `fm1_flash.h`, `crt0.S`, `fm1_vec.S`, `fm1_isr.S`, `app.ld`,
그리고 패커 `picovave/platform/tools/fm1pkg_make.py`). picovave가 새로 쓰는 것은 앱 계층
(`src/picovave_p8.h` — 부팅·프레임 루프·입력→gamepad), 카트 저장소와 업로드 프로토콜
(`src/picovave_cart.h`), 화면용 글리(`src/picovave_gfx.h`), 그리고 기기 미니 libc·할당자
(`femto8/stub/device/`)뿐이라, 하드웨어에서 이미 검증된 부분은 다시 만들지 않는다. 다른 소비자
(시뮬레이터/호스트)는 작은 hh_* HAL과 seam(`femto8/seam/`)만 바꿔 끼운다.

## 카트(게임) 업로드와 실행

기기는 SLOOP 스토어(플래시 0x9D000..0xBB000, 120 KiB)를 **한 카트**에 쓴다(슬롯 1개, 122,880 B).
슬롯 헤더(64 B)에는 매직·이름·길이·`seq`·CRC16/CCITT가 있고, 부팅 시 그 슬롯을 실행한다 — 업로드가
곧 실행이다. 한 슬롯인 이유는 **사전 컴파일 카트**(`PVB1`: ROM + Lua 바이트코드, 최대 105,656 B)가
61,440 B 슬롯에는 들어가지 않기 때문이다. 대가로 롤백 슬롯은 없고, 업로드 도중 전원이 끊기면
래더의 바닥인 **내장 카트**가 뜬다(패널은 검게 남지 않는다).
업로드는 USB SysEx `F0 22 24 35 7E <cmd> .. F7`(`01 BEGIN` / `02 DATA` / `03 END` / `05 ERASE` /
`06 LIST`)이다.

**카트는 `.p8.png` 하나만 받는다.** 페이지가 160×205 이미지를 풀어(그 픽셀의 하위 2비트가 곧
카트다) **쿠킹된 카트**(ROM 17,152 B + 코드 섹션 15,648 B = 32,800 B)를 만들고, 여기에 4바이트
매직 `PVC1`을 붙인 **32,804 B**가 BEGIN이 받아들이는 유일한 길이다(다른 길이는 `ERR 7` = 형식 아님).
기기는 그 코드 섹션만 편다 — femto8이 이미 가진 해제기(PXA `\0pxa` · `:c:` mini · 구형 raw)를
`p8_load_cooked()`가 64 KiB Lua 스크립트 버퍼로 직접 푼다(업스트림은 32 KiB 버퍼를 대상으로 해
baba 45,053 B 등에서 넘쳤다). 텍스트 `.p8`은 받지 **않는다**: gfx/map/sfx/music이 16진수라 작성자가
저장한 압축 카트보다 2~3배 커서 슬롯에 들어가지 않고, 형식이 둘이면 헷갈린다(`.p8` → `.p8.png`는
PICO-8의 저장 그 자체다).

쿠킹된 카트는 **바이너리**이므로 7비트 SysEx에 싣기 위해 페이로드를 **8비트→7비트로 분할**한다
(패키지 프로토콜과 같은 방식; `off`·`n`·CRC는 원시 바이트 기준). **CRC16/CCITT가 원시
페이로드**를 덮으므로, 커밋되는 END 자체가 그 분할이 정확히 가역이라는 바이트 단위 증명이다.
같은 파서가 시뮬레이터에서도 돌기 때문에 프로토콜이 기기에서 어긋날 수 없다.

업로드 **대상**은 두 가지이고 페이지에서 구분해 고른다 — DEVICE 상자의 SIMULATOR/FLASH(USB)와
같은 구분이다:

- **SIMULATOR** — 웹 wasm 안의 저장소(`sim_store`)에 넣는다. 받는 파서가 펌웨어의 것
  (`src/picovave_cart.h`)이라 wasm 안에서 그대로 돌고, 브라우저가 곧 기기다.
- **FLASH(USB)** — 연결된 **실기**의 SLOOP 스토어에 Web MIDI SysEx로 쓴다. **같은 프레임**을 그대로
  보내므로(같은 슬롯 교대 규칙, 같은 CRC16) 시뮬레이터에서 통한 업로드가 실기에서도 그대로 통한다.

두 경우 모두 커밋되면 칩 리셋이 아니라 **그 카트로 다시 부팅**하고(`pv_cart.changed` →
`picovave_boot()`), 슬롯에 남아 POWER-ON 뒤에도 다시 뜬다. CLI(`tools/fm1_install.py`)는 아직
펌웨어 설치만 하고 카트 명령이 없다 — 실기 카트는 페이지의 FLASH(USB)로 넣는다.

**기기가 못 돌리는 카트는 거부되고, 기기는 멈추지 않는다.** 카트의 Lua 비용은 소스 크기의 10~20배에
런타임 데이터(테이블·문자열)가 더해지는데, 기기 아레나는 414,184 B(Lua 힙 413,160 B = 아레나 − 1 KiB
red zone)이고 그중 엔진이 ~74 KB를 쓴다. 그래서 **크기로는 예측할 수 없다** — 45 KB 스크립트(baba)가
겨우 돌고, 20 KB 스크립트도 문자열/테이블이 많으면 못 돈다. 유일한 판정은 **실측**이다:

- 페이지가 카트를 올리기 전에 **기기와 같은 할당자·같은 아레나**로 그 카트를 실제로 돌려
  `status`/`refused`를 재고(`measureCart()`), `기기 OK` / `기기 빠듯` / `기기 불가`를 표시한다.
  거부 한 번은 Lua의 풀 GC 한 번이라(terra는 300프레임에 71초) 측정은 프레임 사이에 양보하고
  **끊김이 확실해지면 일찍 멈춘다** — 그래서 판정 문구는 실제로 잰 프레임 수를 말한다.
  실기에서 아예 안 돌거나 눈에 띄게 끊기는 카트면 FLASH(USB) 업로드 전에 `confirm()`으로 한 번 더 묻는다.
- 기기 자체도 **롤백 사다리**로 방어한다: 부팅/업로드 시 최신 유효 슬롯이 거부되면(`p8_begin()`이
  `LUA_ERRMEM`) → 그 슬롯이 대체한 슬롯 → **내장 기본 카트** 순으로 내려간다. 따라서 실기 풀을 넘는
  카트를 올려도 패널이 멈추지 않고 기본 카트가 계속 돈다(측정: bunnysurvivor는 거부 3회 →
  기본 카트 폴백, `status=0`).

참고: PicoPico 같은 ESP32-WROVER-B(PSRAM 8 MB) 포트에서 같은 카트가 도는 것은 인터프리터 차이가
아니다 — femto8도 z8lua를 vendor한다(`LUA_NUMBER = fix32_t`). 차이는 힙 크기뿐이고, AC791N에는
PSRAM 인터페이스가 없어 이 상한은 하드웨어 제약이다.

## 입력

기기는 Makkuro37의 `smk_input.h`(SPI2 + 74HC595, 그리고 ADC mux)로 18 버튼 · 37 건반 · 8 엔코더에
더해 패드(타격 속도) · 페이더 4 · 피치/모드 휠까지 폴링으로 읽는다. `src/picovave_p8.h`의
`pv_gamepad()`가 **소유자 검증된 SMK-37 Pro 게임 배열**(건반 저음 + 패드 행 = D-pad, 두 건반/패드
= O/X)로 PICO-8의 6버튼을 만든다. 시뮬레이터 페이지는 `sim_button` / `sim_key` / `sim_enc` /
`sim_pad` / `sim_fader` / `sim_wheel`로 **같은 논리 입력**을 넣고, 두 경로 모두 같은 앱 계층이
처리한다. 호스트 프리뷰는 같은 `hh_input_read` 계약을 스크립트로 먹인다.

## 시뮬레이터에서 (picovave)

시뮬레이터 페이지는 picovave의 패널 — 휠(PITCH·MOD) · 화면과 그 아래 6버튼 · K1..K8 과 16 패드 ·
페이더와 2×6 버튼 · 건반 — 을 갖추고, **오디오는 picovave 자신의 AudioWorklet**
(`picovave/sim/worklet.js`)이 낸다. 실행되는 wasm은 picovave 펌웨어의 소스 그대로
(`picovave/sim/sim.c`가 `src/picovave_p8.h`·`src/picovave_cart.h`와 vendored femto8 엔진을 변경
없이 include)이므로, **엔진이 만든 소리 자체**(femto8 `p8_audio.c`의 `render_sounds`)가 워클릿으로
흘러 나온다 — 기기에서는 메인 루프가 링을 채우고 ALNK0 ISR이 하프를 꺼내며(엔진 오디오 상태의
단일 writer), 시뮬레이터에서는 같은 함수가 같은 하프를 채운다. 워클릿 계약은 `sim_render` /
`sim_out_l·r` / `sim_pads` / `sim_leds` / `sim_pad` / `sim_fader` / `sim_wheel`이고 100 ms 루프를
돈다. 페이지·워클릿·wasm 모두 picovave 것이고 `repo/`를 참조하지 않는다.

브라우저 자동재생 정책 때문에 오디오는 한 번의 사용자 제스처가 필요하다. 페이지는 기본적으로 오디오가
켜진 상태(SOUND on)이고, **첫 클릭·키 입력**이 그 제스처가 되어 설치된 펌웨어를 곧바로 워클릿으로
옮긴다. **SOUND** 는 음소거 토글이다. 그 전에는 페이지가
*조용한 메인스레드 러너*로 같은 wasm 을 같은 메시지 프로토콜로 돌리고, 헤드리스(Node) 검사가 실행하는
것도 이 경로다. 즉 브라우저에서는 워클릿, 헤드리스에서는 메인스레드 — **한 실행기, 두 백엔드**이며 구동
메시지는 동일하다.

설치/원복은 아래 DEVICE 상자에서 한다.


## 시뮬레이터 설치 · 실기 플래싱 · v016 원복

시뮬레이터 페이지의 **DEVICE** 상자는 두 갈래다.

- **SIMULATOR** — 웹에서 바로 적용한다. `picovave.fwsc`를 설치하면 그
  패키지와 같은 소스의 wasm이 곧바로 화면에 뜬다. `web/sim/fwsc.mjs`로 **검증**하고
  (헤더 / 파일목록 / `flash.bin` CRC), 설치가 어느 flash 바이트를 바꾸는지 보여준다. 1 MB flash
  모델에 `flash.bin`을 쓰고, 보호영역(0x9d000 VM … 0xff000 key_mac)은 건드리지 않는다. **USB로 쓰지 않는다.**
- **FLASH(USB)** — 진짜로 쓴다. `web/sim/fm1ota.mjs`(= `picovave/platform/tools/fm1_install.py`의 Web MIDI 이식)가
  USB-MIDI SysEx로 연결된 SMK-37 Pro에 패키지를 보내고, 기기가 스스로 설치·재부팅한다(핸드셰이크 →
  업그레이드 → 패키지 확인/로더 스테이징 → 로더가 app 영역 기록 → 재시작 후 identity 확인). Chrome
  (https/localhost)과 사용자의 MIDI 권한이 필요하다. flash 모델도 wasm도 거치지 않는다 — 하드웨어가
  자기 이미지를 실행한다. 개봉 직후의 실기 SMK-37 Pro 는 USB-MIDI **포트가 여러 개**(`-Private`,
  `-Master`, `-MCP` 등)라 어느 포트가 업데이트에 응답하는지 이름만으로는 알 수 없다. 그래서 기기를
  **자동으로 못 찾으면** `MIDI 포트 진단`이 **모든** 포트에 handshake 를 질의해 응답한 포트(와 그
  identity)를 보여주고, 그 포트를 선택 상자에 골라 준다. 포트를 열자마자 보내면 CoreMIDI 가 그
  메시지를 버릴 수 있어(참조 `fm1_install.py` 와 같이) 0.3 s 기다린 뒤 질의하고, 여러 케이블의 in/out
  이름이 1:1 이 아니어도 되도록 같은 입력에 다른 패밀리 출력으로도 질의한다.
- 페이지는 로드한 패키지를 **내용**(`flash.bin`)으로 picovave 것인지 판별한다(순정 v016은 identity가
  `SMK-37 Pro_016`). 그 밖의 ROM(예: 순정 v016)은 SIMULATOR로는 실행할 수 없어 "실기 전용"으로
  표시하지만, FLASH(USB)로는 쓸 수 있다. 브라우저는 공유 C 소스만 실행할 수 있고 pi32v2 기계어는 실행할
  수 없기 때문이다.
- 원복은 사용자가 **직접 가진** `SMK-37_Pro_016.fwsc`를 끌어다 놓거나 선택해 SIMULATOR(모델) 또는
  FLASH(USB)(실기)로 되돌린다. 순정 펌웨어는 저작권물이라 저장소/페이지에 넣지 않는다.
- `picovave.fwsc` 내려받기 링크도 페이지에 있다.

`web/sim/fwsc.mjs`는 `tools/fwsc.py`의 이식본이다. 이식본은 조용히 어긋나기 쉬우므로,
`tools/verify_final.py`가 두 리더를 실제 패키지에 돌려 **같은 답**을 요구한다. 같은 이유로
`web/sim/fm1ota.mjs`는 `picovave/platform/tools/fm1_install.py`와 wire 포맷을 **교차검증**하고,
`picovave/sim/fm1ota_check.mjs`가 가짜 기기로 **전체 업데이트를 끝까지** 돌려 매 바이트를 패키지와 대조한다.


## 빠른 시작

    # 1. 펌웨어 + 설치 패키지 (picovave; vendored 플랫폼으로 자기완결 빌드)
    JIELI_TOOLCHAIN=~/.jieli/toolchain AC79_SDK=~/fw-AC79_AIoT_SDK python3 tools/build_picovave.py  # → build/picovave.fwsc

    # 2. 브라우저 시뮬레이터 (picovave wasm + 페이지 + 워클릿)
    python3 tools/build_sim.py                                 # 기본: 힙 = 기기 allocator(femto8/stub/device/alloc.c) · 기기 아레나
    python3 tools/build_sim.py --stack 24320                   # 기기와 같은 C 스택(24,320 B)으로: 엔진이 그 안에 들어가는지
    python3 tools/build_sim.py --probe-alloc                   # 힙을 비용 모델(sim/heap_probe.c)로 — arena 없는 보수적 추정치
    python3 -m http.server -d build/sim 8738                   # → http://localhost:8738

    # 3. 검증 (앱/패키지 + 화면·입력 픽셀 일치(호스트 프리뷰 ↔ wasm) + 시뮬레이터 페이지 + 실기 플래셔 교차검증, 209개 검사)
    AC79_SDK=~/fw-AC79_AIoT_SDK python3 tools/verify_final.py

준비물: JieLi pi32v2 툴체인(`~/.jieli/toolchain`), JieLi AC79 SDK, Pillow, Node.
wasm 빌드는 emcc 또는 emscripten/emsdk Docker 이미지를 자동으로 사용한다.
macOS에서는 JieLi 툴체인 명령도 linux/amd64 Docker 컨테이너에서 실행된다.

## 구성

| 경로 | 역할 |
|---|---|
| [picovave/src/picovave_input.h](picovave/src/picovave_input.h) | 입력 모델 (hin_t: 버튼/건반/엔코더/패드/페이더/휠) — 세 소비자가 공유 |
| [picovave/src/picovave_p8.h](picovave/src/picovave_p8.h) | 앱 계층: femto8 부팅·프레임 루프(`p8_begin`/`p8_step`)·프레임별 setjmp·입력→gamepad — 세 소비자가 공유 |
| [picovave/src/picovave_cart.h](picovave/src/picovave_cart.h) | 카트 저장소(한 슬롯·CRC16·`seq`) + SysEx 업로드 프로토콜 — 기기/시뮬레이터가 공유 |
| [picovave/src/picovave_gfx.h](picovave/src/picovave_gfx.h) | **128x128**(PICO-8 자체 크기) RGB565 캔버스 — 엔진이 카트를 그리는 그 표면이고, 기기의 OTA/복구/크래시 화면이 `hg_fb` 별칭으로 재사용한다(패널 확대는 표시 경로가 한 줄씩). 엔진은 `-DLUAI_MAXCCALLS=48`로 컴파일된다(스톡 200은 보호 레벨당 316 B × 200 ≈ 63 KB로 24,320 B 스택을 넘겨, 깊은 카트에서 Lua의 자체 가드보다 하드웨어 폴트가 먼저 온다) | 빌드는 identity 응답의 NUL 패딩에 **자기 태그**(빌드 입력의 sha256 앞 8자리)를 실어 보내고, `ota_probe.py --ask`/`--flash`가 그것을 찍는다(패널은 마지막 프레임을 들고 있으므로 플래싱 여부의 근거가 못 된다) | `hg_crash_screen()`은 `PICOVAVE CRASH` 헤드라인 아래 12줄에 PC·벡터·EMU/DBG·SP 판정·USP/SSP·**스택 최대 사용량**·크래시 횟수·ETM을 그린다. 기록은 `.noinit`에 남아 **다음 부팅이 같은 화면을 `LAST CRASH` 헤드라인으로 10초** 다시 그린다(놓칠 일 없음) |
| [picovave/femto8/](picovave/femto8/) | vendored PICO-8(femto8) 엔진 — **10개의 문서화된 패치**만 적용([femto8/README.picovave.md](femto8/README.picovave.md)) |
| [picovave/src/picovave.c](picovave/src/picovave.c) | 기기 진입점 + hh_* HAL + 패널 푸시(RGB565 BE 스왑) + ALNK0/TIMER5 ISR + USB/OTA/카트 글루 |
| [picovave/platform/firmware/{hal,src}](picovave/platform/firmware/hal) | 실기 HAL · 패널 + USB/OTA + 로더 (Makkuro37에서 vendor한 사본 — picovave 자기완결) |
| [picovave/sim/sim.c](picovave/sim/sim.c) | wasm 시뮬레이터 (앱 계층·카트·엔진을 include, HAL만 교체) |
| [picovave/sim/picovave_sim_hal.h](picovave/sim/picovave_sim_hal.h) | 시뮬레이션 패널 + 입력 HAL |
| [picovave/sim/worklet.js](picovave/sim/worklet.js) | picovave 자신의 AudioWorklet (오디오 엔진; `repo/`를 읽지 않는다) |
| [picovave/sim/check_api.mjs](picovave/sim/check_api.mjs) | 페이지/워클릿이 부르는 export가 실제로 있는지 (wasm) |
| [picovave/sim/run_page.mjs](picovave/sim/run_page.mjs) | 페이지의 실제 스크립트를 wasm에 물려 실행 (SIMULATOR/FLASH(USB) 포함) |
| [picovave/sim/fwsc_check.mjs](picovave/sim/fwsc_check.mjs) | `fwsc.mjs` ↔ `fwsc.py` 교차검증 러너 |
| [picovave/sim/fm1ota_check.mjs](picovave/sim/fm1ota_check.mjs) | `fm1ota.mjs` ↔ `picovave/platform/tools/fm1_install.py` 교차검증 + 가짜 기기 전체 업데이트 |
| [picovave/host/preview.c](picovave/host/preview.c) + [seam_host.c](picovave/host/seam_host.c) | 호스트 프리뷰: 같은 엔진·앱 계층 + 캡처 HAL/스크립트 입력 (wasm과 픽셀 일치) |
| [picovave/host/alloc_test.c](picovave/host/alloc_test.c) | **기기 allocator 단위/스트레스 테스트** — `femto8/stub/device/alloc.c`를 기기와 같은 414,184 B 아레나로 돌린다(시뮬레이터·프리뷰는 다른 allocator를 쓰므로 이 파일만이 그 코드를 실행한다). `verify_final.py`가 매번 돌린다 |
| [web/sim/index.html](web/sim/index.html) | 시뮬레이터 페이지 (SMK-37 Pro 패널 + DEVICE: SIMULATOR(웹) / FLASH(USB)) · 카트 대상도 같은 구분 |
| [web/sim/fm1ota.mjs](web/sim/fm1ota.mjs) | Web MIDI SysEx 실기 플래셔 (`picovave/platform/tools/fm1_install.py`와 교차검증) |
| [web/sim/fwsc.mjs](web/sim/fwsc.mjs) | .fwsc 리더의 브라우저 이식본 (`tools/fwsc.py`와 교차검증) |
| [web/sim/p8png.mjs](web/sim/p8png.mjs) | `.p8.png` → 쿠킹된 카트(매직 + ROM + 코드 섹션) — 페이지와 Node 테스트가 **같은 모듈**을 쓴다 |
| [tools/fwsc.py](tools/fwsc.py) | .fwsc 컨테이너 독립 파서/코덱 |
| [tools/picovave_pack.py](tools/picovave_pack.py) | flash 이미지 + 패키지 생성 (독립 패커, 교차검증용) |
| [picovave/platform/tools/fm1pkg_make.py](picovave/platform/tools/fm1pkg_make.py) | 패커 (Makkuro37에서 vendor한 사본, 기기 패키징의 기준) |
| [tools/build_picovave.py](tools/build_picovave.py) | 펌웨어 빌드 드라이버 (picovave/platform의 vendored 플랫폼 + fm1pkg_make) |
| [tools/build_sim.py](tools/build_sim.py) | 시뮬레이터 빌드 드라이버 (picovave wasm + 페이지 + 워클릿). `--stack 24320`=기기 스택, 기본 힙=기기 allocator(`sim/device_alloc.c`, 기기 아레나), `--probe-alloc`=비용 모델(`sim/heap_probe.c`) |
| [tools/probe_carts.mjs](tools/probe_carts.mjs) | 카트별 실측 도구: 기기 allocator로 각 카트의 peak·거부 횟수·최종 슬롯(폴백 여부)을 찍는다. `--sweep`은 아레나 크기를 바꿔 가며(§13 표·롱런의 근거) |
| [tools/pvcart.py](tools/pvcart.py) | 내장 기본 카트 = **쿠킹된 카트**(ROM 0 + raw 코드 섹션) 생성기 — 세 빌드가 여기서 `cart_default[]`를 굽는다 |
| [tools/ota_probe.py](tools/ota_probe.py) | 실기 MIDI 프로브 + `--flash PKG`(복구: `fm1_install.py` 실행) |
| [tools/mido_boot.py](tools/mido_boot.py) | mido 없는 python3로 실행해도 되도록 재실행 (ota_probe·fm1_install 공용) |
| [tools/fake_device.py](tools/fake_device.py) | 실기 없이 업데이트 전체를 돌리는 가짜 기기(`--test`, 또는 CoreMIDI 포트로) |
| [tools/verify_final.py](tools/verify_final.py) | 전체 검증 (화면·입력 파리티 포함) |

## 안전성

- 앱은 **USB-MIDI로 인식되고 M-UPGRADE를 응답하며 재플래싱될 수 있다** — vendored `usb.c` +
  `ota.c`를 쓰고, TIMER5 ISR(10 kHz)이 입력 스캔(`fm1_input_tick`)과 USB 폴링을 함께 돈다.
  **USB 장비명은 `picovave`**(`picovave/platform/firmware/src/usb.c`의 `STR2`; Makkuro37이 아님).
- 앱이 실행 중에 플래시를 직접 쓰는 영역은 둘뿐이다: **업데이터 staging(0xBB000-0xBFFFF)**과
  **카트 스토어(SLOOP, 0x9D000-0xBAFFF — 호스트 업로드/`ERASE`가 4 KiB 섹터 단위로 지우고 쓰는
  곳)**. app area(0x04000-0x9CFFF) 교체는 업데이트 로더(SPL)가 하고,
  VM/BTIF/USRTRIM/USRFLASH/USR/key_mac은 보존된다.
- **절대 벽돌이 되지 않는다(탈출구):** (a) 실행 중 OCT- + OCT+ 5초 홀드 = UBOOT, (b) USB SysEx
  `F0 22 24 35 7D F7` = UBOOT, (c) **전원 투입 시 OCT- 홀드**(또는 부팅 실패 2회) = 앱 레벨
  **USB RESCUE**(**헤드리스**: usb.c + ota.c만, 화면 없음 — 인스톨러가 화면이다) — 기기는 여전히
  USB-MIDI로 잡히고 **웹 인스톨러/`fm1_install.py`로 그대로 재플래싱**된다(jl-uboot-tool 불필요).
  RESCUE는 USB를 **TIMER5 ISR(2 kHz)**로 서비스한다: 500 ms 루프 폴링으로는 512 B 업데이트
  프레임이 `ota_read()` 창 안에 못 들어와 세션이 시작조차 못 한다(그래서 RESCUE에서도 (a)/(b)가
  동작하고, `flash_ok`가 세션을 막지 않는다), (d) 그래도 앱이 뜨지 못하면 mask ROM UBOOT.
  복원은 UBOOT에서, 또는 순정 v016 패키지 재설치.
- **SysEx 안정성** — 느린 호스트가 SysEx 응답을 중간에 끊어도 다음 전송이 단독 `F7`로 닫아
  호스트 파서가 막히지 않는다(SLOOP/dinghy의 `sx_open` 수정을 vendored `usb.c`에 이식).
- **USB로 안 잡히는 기기(예: USB 스택 없는 옛 빌드) 복구** — mask-ROM UBOOT(`WL82 UBOOT1.00`)에
  `jl-uboot-tool`로 app 영역(`0x4000-0x9CFFF`)만 쓴다. 전체 절차는 [docs/RECOVERY.md](docs/RECOVERY.md).
- **BLE MIDI는 없다 — 복구 경로로 기대하지 말 것.** 스톡 v016 `app.bin`에는 BLE-MIDI GATT
  (`03B80E5A-EDE8-4B33-A751-6CE34EC4C700` / `7772E5DB-3868-4112-A1A9-F2669D106BF3`), 광고 이름
  `SMK-37 Pro_016`, `BTSTACK`/`BTCTRLER`가 들어 있지만, 그것은 **우리가 교체하는 app 영역 안**에 있다.
  picovave(`app.bin`)에는 셋 다 0건이다 — 즉 picovave에는 BLE 스택이 없고 MIDI는 **USB-MIDI만**
  이다. 탈출구는 (a)~(d)의 USB/mask-ROM 경로뿐이다.

비공식 펌웨어이며 설치 책임은 사용자에게 있다.
