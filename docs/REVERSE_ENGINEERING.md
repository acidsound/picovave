# SMK-37 Pro 커스텀 펌웨어: 구조 역설계와 picovave.fwsc

M-Vave SMK-37 Pro(AC791N)용 커스텀 펌웨어의 **패키지/앱 구조를 역설계**하고,
**240x240 액정에 PICOVAVE WORLD를 출력하는 복원 가능한 picovave.fwsc**를 빌드한 기록.

- 분석 대상 저장소: [genkigenki/Makkuro37](https://github.com/genkigenki/Makkuro37) (GPL-3.0)
- 산출물: [build/picovave.fwsc](build/picovave.fwsc), [build/picovave/picovave.bin](build/picovave/picovave.bin)
- 도구: [tools/fwsc.py](tools/fwsc.py) (독립 파서), [tools/picovave_pack.py](tools/picovave_pack.py),
  [tools/build_picovave.py](tools/build_picovave.py), [tools/verify_final.py](tools/verify_final.py)

> **갱신 (2026-10-17):** 이 문서의 **패키지/부팅 구조 역설계(1~2장과 형식 검증)는 그대로 유효**하다.
> 다만 앱 계층은 이후 PICO-8(femto8) 이식으로 교체되었다 — hello의 입력 디버그 화면과 정사각파
> 보이스(`picovave_app.h` / `picovave_voice.h`)는 더 이상 트리에 없고, 지금의 앱은
> [picovave/src/picovave_p8.h](picovave/src/picovave_p8.h)(femto8 부팅·프레임 루프·입력→gamepad) +
> [picovave/src/picovave_cart.h](picovave/src/picovave_cart.h)(SLOOP 카트 저장소·SysEx 업로드) +
> vendored femto8 엔진(`picovave/femto8/`)이다. 기기 빌드는 `.data+.bss` 55,080 B / 아레나
> 414,936 B(`0x01C0EB28..0x01C74000`, 전부 Lua 힙 — Lua 힙 413,912 B)이고, 렌더 타깃은 패널
> 240×240이 아니라 **PICO-8 자체의 128×128 RGB565**(표시 경로가 한 줄씩 ×1.875로 확대)라
> wasm 시뮬레이터·호스트 프리뷰와 **픽셀 단위로
> 일치**한다. 현재 상태와 검증 항목은 [README.kr.md](README.kr.md)와
> [HANDOVER.md](HANDOVER.md)를 보라. 아래의 앱/보이스/시뮬레이터 서술은 hello 시점의
> 기록으로 남겨 둔다.

---

## 1. 근거와 방법

소스 저장소의 코드를 그대로 옮기지 않고, **실제 배포된 패키지의 바이트에서 구조를 읽어** 독립적으로
재구현했다. 사용한 1차 증거:

| 파일 | 정체 | 크기 |
|---|---|---|
| SMK-37 Pro_015.fwsc | M-Vave 순정 v015 (20 마커) | 701,140 B |
| SMK-37_Pro_016.fwsc | M-Vave 순정 v016 (36 마커) | 705,252 B |
| x0x37-0.2.fwsc | 제3자 커스텀 펌웨어 | 650,816 B |

[tools/fwsc.py](tools/fwsc.py)는 이 3개 패키지 **전부에서 모든 CRC가 맞아떨어질 때까지** 반복
수정했고, 그 결과가 아래 형식이다. (초기 교차검증에는 Makkuro37 컨트롤 빌드도 병행해 읽었지만,
지금 빌드·검증은 그것에 의존하지 않는다.)

---

## 2. 부팅 체인 (mask ROM → 앱)

SMK-37 Pro의 AC791N은 애플리케이션을 **암호화된 플래시에서 직접 실행(XIP)** 한다.

    전원 ON
      └─ mask ROM
          └─ flash 0x00000 "uboot.boot" (SPL, 4 KiB, SDK 제공)
              ├─ [정상] app.bin(=flash 0x4000~)을 XIP 0x02000120에 매핑하고
              │        첫 바이트로 점프. r0 = 부트 파라미터 블록
              └─ [업데이트] 0x01C7FD88의 112바이트 UPDATA_PARM 레코드가 있으면
                          그 로더를 RAM 0x01C0A800에 올려 실행

앱 이미지의 **첫 4바이트는 반드시 04 81 80 00** 이어야 한다: pi32v2의 `goto +4; rts`
스텁으로, SPL이 이 값을 확인하고 점프한다. picovave의 [picovave/src/crt0.S](picovave/src/crt0.S)가 그대로
이 형태이며, 검증기가 이 바이트를 강제한다.

---

## 3. .fwsc 컨테이너 형식

논리 이미지 = **0x40 헤더 + 0x50씩 이어지는 파일 엔트리 + 파일 데이터**.

    헤더 (0x40 B)
      +0x00 u16  헤더 CRC16 (대상: [2:0x40])
      +0x02 u16  파일 목록 CRC16 (암호화된 엔트리 전체)
      +0x04 u32  논리 전체 크기
      +0x08 u16  파일 개수
      +0x0A 6 B  팩커 상수 04 00 02 00 00 00
      +0x10 ...  칩 이름 "AC791N"

    엔트리 (0x50 B, 0x40부터 연속)
      +0x00 u16  type   (0 = flash.bin, 100 = ota.bin)
      +0x02 u16  index
      +0x04 u16  데이터 CRC16
      +0x08 u32  데이터 오프셋
      +0x0C u32  길이
      +0x10 u32  32바이트 올림 길이
      +0x40 ...  파일 이름 (NUL 패딩)

    암호화: 헤더는 32바이트 블록마다, 엔트리는 0x50 전체가 JieLi "ENC" 스트림 암호
            (초기 키 0xFFFF) → 암호화 자체가 자기역원이라 복호는 같은 연산

    인터리브: 논리 이미지 0x2F 바이트마다 마커 1바이트 삽입.
              마커 = (식별자 i번째 문자 + i + 1) & 0xFF, 남으면 0x7D.

**마커 개수 추론** — v015는 20개인데 v016은 36개다. 헤더가 논리 크기를 선언하므로
`마커 수 = len(raw) - 헤더.total` 로 **정확히** 구해진다(추정이 아니다). 순정 v016이
36 * 0x30 = 27,216, 705,252 - 27,216 = 678,036으로 맞아떨어진다.

---

## 4. flash.bin (설치되는 플래시 이미지)

    [0x00000, 0x04000)  head
        +0x0000 32 B  플래시 헤더 (JieLi SDK 기본값: burner_size 544, VID "0.01",
                      flash_size 0xFF000, PID "AC791N_STORY")
        0x20부터 32 B JLFS 엔트리 연속:
          uboot.boot / isd_config.ini / app_dir_head / key_mac
        전 구간 ENC 0xFFFF (32바이트 블록 단위)
    [0x04000, ...)      app area — **칩 키 0x980F로 SFC 암호화**
        +0x0000 32 B  app_area_head (crc, off=0x02000120, size=전체)
        +0x0020       파일 엔트리들:
                        app.bin            off 0x120, size = 앱 슬롯
                        cfg_tool.bin       (SDK, 2048 B)
                        VM / PRCT / BTIF / USRTRIM / USRFLASH / USR  (영역 기술자 6개)
        +0x120        **app.bin = 앱 이미지 본체** (XIP 0x02000120에 매핑)
        ...           cfg_tool.bin
        ...           중첩 "cfg" 디렉터리 (eq_cfg_hw.bin, 1024 B)

### JLFS 엔트리 (32 B)

    +0x00 u16  crc16([2:32] 전체)   ← 엔트리 자체의 무결성
    +0x02 u16  데이터 CRC16         ← 기술하는 데이터의 무결성
    +0x04 u32  offset
    +0x08 u32  size
    +0x0C u8   flags      +0x0D u8 resvd      +0x0E u16 index
    +0x10 16 B name

PRCT는 **플래시 크기(0x9D000)** 를 담고, 부트로더가 실제로 덮어쓰는 범위의 끝을 정의한다.
따라서 flash.bin은 정확히 0x04000 + (0x120 + 앱슬롯 + cfg + 영역표 + cfg디렉터리) 까지만
쓰고, 그 뒤는 건드리지 않는다.

### 칩 키 유도

isd_config.ini 앞 32바이트가 키를 담는다:

    s = sum(core[0:16]) & 0xFF
    s = 0xAA if s >= 0xE0 else (0x55 if s <= 0x10 else s)
    key = sum(1 << i for i in range(16) if (core[16+i] ^ core[15-i]) < s)

순정·Makkuro37·picovave 패키지 모두 **0x980F**가 나온다. SFC 암호는 32바이트 블록마다
ENC(블록, key ^ (블록주소 >> 2)) 로, 주소가 섞이기 때문에 블록 오프셋이 어긋나면
전부 깨진다(초기 구현에서 실제로 이 지점을 디버깅했다).

---

## 5. 영역 맵 (순정 v016 = picovave가 그대로 사용)

| 플래시 범위 | 영역 | picovave 설치 시 |
|---|---|---|
| 0x00000-0x03FFF | head (SPL, isd_config) | 기록됨 (SDK SPL 재사용) |
| 0x04000-0x9CFFF | app area | **기록됨 — 펌웨어 슬롯** |
| 0x9D000-0xBAFFF | VM (프로젝트/설정/SLOOP 저장소) | 보존 |
| 0xBB000-0xBFFFF | OTA 스테이징 + 업데이트 레코드 | 보존 |
| 0xC0000-0xC0FFF | BTIF (블루투스 페어링) | 보존 |
| 0xC1000-0xC1FFF | USRTRIM (키/패드 캘리브레이션) | 보존 |
| 0xC2000-0xE9FFF | USRFLASH (사용자 샘플 슬롯) | 보존 |
| 0xF4000-0xFDFFF | USR (순정 리소스) | 보존 |
| 0xFF000-0xFFFFF | key_mac | 보존 |

검증기가 위 6개 보호 영역에 대해 **flash.bin이 닿지 않음**을 매 빌드마다 확인한다.

---

## 6. picovave 펌웨어

[picovave/src/picovave.c](picovave/src/picovave.c) — 앱 슬롯 0x97400 안의 **약 19 KB**. Makkuro37의 HAL·
부팅·USB/OTA 스택을 그대로 포함하므로, picovave가 새로 쓰는 것은 화면·음·글루뿐이다.

    fm1_cstart()                                (crt0.S / app.ld / fm1_vec.S / fm1_isr.S — picovave/platform에 vendor)
      ├─ .data / .ram_text 복사 (XIP → RAM)
      ├─ TIMER4 free-running (24 MHz) 시간 기준
      ├─ P33 워치독 8초 리셋 모드로 인수
      ├─ bootguard: 실패 2회 또는 전원 투입 시 OCT- 홀드 → 앱 레벨 USB RESCUE,
      │             그 리셋이 반복되면 mask ROM UBOOT (벽돌 방지)
      ├─ fm1_irq_init(): 예외/IRQ 벡터 + 가드(스택/쓰기/버스/PC)
      ├─ picovave_boot()  → hh_lcd_init() : SPI1 group A (PB6 CLK / PB7 DO / PC8 CS / PB5 D/C)
      │                                 ST7789V 순정 v016 초기화 (12 MHz)
      │                → hh_input_init(): SPI2 group C (PA3 CLK / PA4 DO), PA0 /OE · PA1 latch
      ├─ audio_init(): ALNK0 I2S → 코덱, 하프 버퍼 2개 (fm1_audio.h)
      ├─ usb_start(): USB-MIDI 디바이스 + M-UPGRADE (usb.c)
      ├─ fm1_timer5_start(isr_timer5, 4): 10 kHz 틱 = 입력 스캔(fm1_input_tick) + ms 카운트
      │                                 + 5틱마다 USB 폴링(usb_poll)
      └─ 무한 루프: 5 ms마다 입력 엣지 + hv_set(현재 음) + ota_service(),
                   100 ms마다 디버그 화면 렌더 → 240x240 전체 DMA 전송

    fm1_alnk0_irq(): 하프 버퍼마다 hv_step()으로 정사각파 렌더 (picovave_voice.h)

직접 다시 만들지 않고 **vendor한 플랫폼**(`picovave/platform/`, Makkuro37에서 복사):

- **USB-MIDI + M-UPGRADE** — `usb.c` + `ota.c` 그대로. 그래서 실기에서 USB로 인식되고
  (FLASH(USB)로) 다시 플래싱되며, OCT- + OCT+ 5초 홀드나 USB SysEx `F0 22 24 35 7D F7`로
  UBOOT에 들어갈 수 있다. `usb.c`에는 SLOOP/dinghy의 **`sx_open` 수정**(끊긴 SysEx 응답을
  다음 전송에서 단독 `F7`로 닫아 호스트 파서가 막히지 않게)도 이식했다
- **앱 레벨 USB RESCUE** — Makkuro37 `recovery.c`와 같은 방식: 전원 투입 시 OCT- 홀드거나
  부팅 실패 2회면 오디오/TIMER5 없이 USB 스택만 올려, **웹 인스톨러로 그대로 재플래싱**되는
  화면으로 들어간다(jl-uboot-tool 불필요)
- **입력 스캔** — `smk_input.h`의 `fm1_input_tick`을 TIMER5 ISR에서 (이전에는 폴링)
- **플래시** — `fm1_flash.h`; 실행 중에 쓰는 곳은 **업데이터 staging 영역(0xBB000-0xBFFFF)뿐**.
  app 영역 교체는 업데이트 로더(SPL)가 한다

건반·버튼·엔코더에 더해 **패드(타격 속도)·페이더·피치/모드 휠**을 읽어 화면에 관측하고,
이제 **건반이 눌리면 그 음높이로 정사각파를 실제로 발음**한다 — 코어는 `picovave_voice.h`에
한 번만 있고 기기와 시뮬레이터가 같은 수를 계산한다.

입력 처리와 렌더링([picovave/src/picovave_app.h](picovave/src/picovave_app.h),
[picovave/src/picovave_gfx.h](picovave/src/picovave_gfx.h))은 펌웨어와 호스트 미리보기, 시뮬레이터가
**같은 헤더**를 쓰므로, 그 화면이 곧 패널에 들어가는 픽셀이다.

---

## 7. 복원 가능성 (recoverability)

1. **head가 순정과 동일한 모양** — SPL은 순정과 같은 JieLi AC79 SDK uboot.boot(4 KiB),
   isd_config는 같은 키 0x980F를 담고, app_dir_head/key_mac 엔트리도 같은 형태다.
   Makkuro37 패키지도 같은 head를 쓰고 실기에서 동작한다.
2. **앱을 지워도 SPL은 살아 있다** — app area만 덮어쓰므로 mask ROM + SPL 진입 자체는
   항상 가능하다.
3. **탈출구 다중** — (a) 실행 중 OCT- + OCT+ 5초 홀드 = UBOOT, (b) USB SysEx
   F0 22 24 35 7D F7 = UBOOT, (c) 전원 투입 시 OCT- 홀드(또는 부팅 실패 2회) = **앱 레벨
   USB RESCUE** — 웹 인스톨러로 그대로 재플래싱(jl-uboot-tool 불필요), (d) 그 RESCUE조차
   리셋을 반복하면 bootguard가 mask ROM UBOOT으로 떨어뜨림. 순정 패키지 재설치도 가능.
4. **보호 영역 무손상** — VM/BTIF/USRTRIM/USRFLASH/USR/key_mac을 건드리지 않으므로
   순정 복귀 시에도 사용자 데이터와 캘리브레이션이 남는다.
5. **업데이트 로더 포함** — 패키지의 ota.bin에 Makkuro37(GPL-3.0)의 검증된 로더를 그대로
   넣는다. 실행 중인 펌웨어가 FELUCCA-LOADER-1 마커로 자체 로더를 식별하고 스테이징한다.
   picovave 앱 안에 로더를 새로 심는 것보다 안전하고 검증된 경로다.

즉, **앱이 죽어도 키보드는 살아 있고**, 순정으로 되돌리는 길이 막히지 않는다.

---

## 8. 빌드와 검증

준비물:

- JIELI_TOOLCHAIN = ~/.jieli/toolchain (tools/get_toolchain.sh)
- AC79_SDK = ~/fw-AC79_AIoT_SDK (uboot.boot, cfg_tool.bin, cfg/eq_cfg_hw.bin)
- macOS에서는 각 툴체인 명령이 linux/amd64 Docker 컨테이너에서 실행된다

명령:

    JIELI_TOOLCHAIN=~/.jieli/toolchain AC79_SDK=~/fw-AC79_AIoT_SDK python3 tools/build_picovave.py  # 펌웨어 + 패키지 (vendored 플랫폼, 자기완결)
    python3 tools/build_sim.py           # 브라우저 시뮬레이터 (picovave wasm + 페이지 + 워클릿)
    python3 tools/preview_picovave.py       # 호스트 240x240 렌더
    AC79_SDK=~/fw-AC79_AIoT_SDK python3 tools/verify_final.py  # 전체 검증 (아래)
    python3 -m http.server -d build/sim 8738   # → http://localhost:8738

빌드 파이프라인:

    tools/mkfont.py       → picovave/src/picovave_font.h  (글리프)
    picovave/platform/firmware/crt0.S + picovave/src/picovave.c (vendored CFLAGS/링커맵)
    pi32v2 clang + ld     → build/picovave/picovave.elf / picovave.bin (0x02000120 진입)
    picovave/platform loader → build/picovave/ota.bin     (7,150 B, FELUCCA-LOADER-1)
    picovave/platform/tools/fm1pkg_make.py → build/picovave.fwsc  (기기 패키징의 기준; tools/picovave_pack.py가 독립 교차검증)
    tools/fwsc.py         → 산출 패키지 자체 검증 (헤더/목록/파일 CRC)
    emcc (Docker)         → build/sim/sim.wasm      (picovave; 같은 picovave_gfx.h / picovave_app.h)

[verify_final.py](tools/verify_final.py)가 매번 확인하는 것 (총 119개):

- _start == 0x02000120, 진입 스텁 04 81 80 00, 인터럽트 핸들러 없음(영상의 sti 1개는 재사용 HAL)
- 레지스터 접근이 picovave/src 밖에 없음 (아키텍처 회귀 방지)
- 컨테이너 헤더/파일목록/flash.bin/ota.bin CRC 전부 일치
- Makkuro37의 패커 == 우리 독립 패커(`picovave_pack.py`), **바이트 단위 일치**
- app_area_head 데이터 CRC, offset 0x02000120, app.bin 내용 = 빌드 산출물
- 보호 영역 6곳 미접촉, 영역 기술자 6개가 순정 v016과 **바이트 단위로 동일**
- 시뮬레이터가 picovave 소스(picovave_gfx.h / picovave_app.h를 include, HAL만 교체)를 쓰는지, 앱 로직에 레지스터가 없는지
- 실기 HAL이 Makkuro37의 `smk_input.h`(패드 속도·페이더·휠 포함)를 쓰는지, 정사각파 보이스가
  **기기와 시뮬레이터가 공유**하는지(`picovave_voice.h` 코어 + 시뮬레이터 어댑터)
- 기기가 vendored USB-MIDI + M-UPGRADE 스택을 쓰고(`usb.c`/`ota.c`), 10 kHz TIMER5
  틱을 설치하며, mask ROM UBOOT으로 떨어질 수 있는지(bootguard 포함)
- **wasm 화면 == 호스트 미리보기, 픽셀 단위 일치**
- 페이지가 picovave를 SIMULATOR/원복하고 입력(버튼·패드·페이더·휠)에 반응하는지, FLASH(USB)가
  Web MIDI 없이도 조용히 실패하는지, 실패 시 MIDI 포트를 진단해 응답한 포트를 고르게 하는지 (페이지를 실제로 실행)
- `fm1ota.mjs`의 wire 포맷 == `picovave/platform/tools/fm1_install.py` (pack7/unpack7/crc16/ENC/response/parseRequest/
  parseIdentity/pairOutput 벡터), 그리고 가짜 기기로 전체 업데이트가 끝까지 도는지

---

## 9. 산출물

| 파일 | 내용 |
|---|---|
| [build/picovave.fwsc](build/picovave.fwsc) | 설치용 패키지, 651,266 B, 식별자 SMK-37 Pro_700 |
| [build/picovave/picovave.bin](build/picovave/picovave.bin) | 앱 이미지 19,396 B (슬롯 0x97400) |
| [build/picovave/picovave.dis](build/picovave/picovave.dis) | 디스어셈블리 |
| [build/picovave/preview.png](build/picovave/preview.png) | 240x240 렌더 결과 |
| [build/picovave/ota.bin](build/picovave/ota.bin) | 업데이트 로더 7,150 B |
| [build/sim/sim.wasm](build/sim/sim.wasm) | picovave 브라우저 시뮬레이터 14,058 B (펌웨어와 같은 소스) |
| [build/sim/index.html](build/sim/index.html) | 시뮬레이터 페이지 (picovave + DEVICE: SIMULATOR(웹) / FLASH(USB)) |
| [build/sim/worklet.js](build/sim/worklet.js) | 페이지의 오디오 엔진 (picovave 자신의 AudioWorklet) |
| [build/sim/fm1ota.mjs](build/sim/fm1ota.mjs) | 페이지가 쓰는 Web MIDI SysEx 실기 플래셔 |
| [build/sim/fwsc.mjs](build/sim/fwsc.mjs) | 페이지가 쓰는 .fwsc 리더 |
| [build/sim/picovave.fwsc](build/sim/picovave.fwsc) | 페이지의 SIMULATOR/FLASH(USB) 가 쓰는 패키지 (우리 빌드) |
| [build/sim/screen.png](build/sim/screen.png) | wasm이 실제로 그린 화면 |

설치(실기):

    python picovave/platform/tools/fm1_install.py --yes build/picovave.fwsc     # mido + python-rtmidi
    # 또는 시뮬레이터 페이지의 FLASH(USB) 버튼 (같은 프로토콜의 Web MIDI 이식), 또는 저장소의 웹 설치 프로그램

되돌리기:

    python picovave/platform/tools/fm1_install.py --yes "SMK-37 Pro_016.fwsc"
    # 또는 페이지에서 순정 .fwsc 를 끌어다 놓고 FLASH(USB) 복원

---

## 10. 라이선스

- 새로 작성한 코드(picovave, tools/*.py, 문서): GPL-3.0-only
- uboot.boot, cfg_tool.bin, eq_cfg_hw.bin: JieLi AC79 SDK (Apache-2.0) — 패키지에 그대로 포함
- 업데이트 로더: [Makkuro37](https://github.com/genkigenki/Makkuro37) firmware/loader
  (GPL-3.0, Leo Kuroshita / Hügelton Instruments)를 빌드해 그대로 포함

M-Vave와 무관한 비공식 펌웨어다. 설치 책임은 사용자에게 있다.


---

---

## 11. 시뮬레이터: 화면이 ROM과 같도록 만드는 구조

Makkuro37의 가장 값진 구조는 **시뮬레이터가 펌웨어와 같은 소스를 컴파일한다**는 점이다.
그래서 시뮬레이터 화면은 "비슷한 흉내"가 아니라 **실제 롬이 그리는 픽셀**이다. picovave도 같은
구조를 갖도록 리팩터링했다.

### 하나의 소스, 두 개의 HAL

    picovave/src/picovave_input.h  입력 모델 (hin_t: 버튼/건반/엔코더 + 패드·페이더·휠)  ← 하드웨어 없음
    picovave/src/picovave_gfx.h    240x240 RGB565 캔버스 + 입력 디버그 화면            ← 하드웨어 없음
    picovave/src/picovave_app.h     기기 동작 (부팅, 워치독, 루프, 입력 엣지, 현재 음)   ← 하드웨어 없음
    picovave/src/picovave_voice.h  정사각파 보이스 (정수 Q15 코어)                     ← 하드웨어 없음
             │
             ├─ picovave/src/picovave.c     + picovave/platform/firmware/{hal,src}  vendored Makkuro37 HAL (SPI1 패널 · SPI2 입력 · ADC mux · P33 · TIMER4/TIMER5 · ALNK0 · USB-MIDI/OTA)
             ├─ picovave/sim/sim.c       + picovave/sim/picovave_sim_hal.h  240x240 RGB565 배열 + 페이지 입력 + 정사각파 어댑터
             └─ picovave/host/preview.c  + 스크립트 HAL               240x240 RGB565 배열 + 스크립트 입력 (그리기만)

세 소비자가 **같은 네 헤더를 수정 없이** 컴파일하고, 다른 것은 작은 hh_* HAL 표면뿐이다:

    hh_time_init()  hh_delay_ms()  hh_wdt_arm()  hh_wdt_feed()  hh_lcd_init()
    hh_blit(x, y, w, h, px)
    hh_input_init() hh_input_read(&in)

picovave_app.h는 레지스터를 전혀 모른다. 입력을 **읽는** 부분만 HAL이고, 그 입력을 엣지로 만들고
화면에 그리는 정수 연산이 양쪽에서 동일하므로 같은 입력에 대해 같은 픽셀이 나온다.

실기 입력은 vendor한 [picovave/platform/firmware/hal/smk_input.h](picovave/platform/firmware/hal/smk_input.h)를
쓴다: SPI2 group C로 74HC595 4개를 돌려 16열을 차례로 선택하고 8개 행(PA2·PA5·PA6·PA7·PA9·PH6·
PH8·PH9)을 읽는다. 행 0..4 = 37건반, 행 5..6 = 18버튼, 행 7 = 8엔코더(4분주 구적 디코드).
패드·페이더·피치/모드 휠은 같은 HAL의 ADC mux(채널 1/8은 패드, 채널 2는 페이더·휠)에서
`fm1_in.pad_press[]` / `fm1_in.fader[]` / `fm1_in.wheel[]`로 들어온다 — picovave.c의 hh_* 어댑터가
`fm1_input_init` / `fm1_input_tick`(TIMER5 ISR) / `fm1_enc_take`로 넘길 뿐, 새로 만든 HAL이 아니다.
`hh_input_read`는 ISR이 이미 갱신한 `fm1_in` 상태를 읽기만 한다.

이는 Makkuro37의 방식 그대로다(그쪽 web/sim/sim.c가 firmware/src을 #include 하고 패널만
시뮬레이션한다): picovave도 [picovave/sim/sim.c](picovave/sim/sim.c)가 펌웨어 헤더를 include 하고 패널만
시뮬레이션한다.

### 시뮬레이터 페이지

시뮬레이터 페이지는 **picovave**를 실행한다. picovave wasm은 [picovave/sim/sim.c](picovave/sim/sim.c)
(= picovave_gfx.h / picovave_app.h를 변경 없이 include)를 컴파일한 것이고, 오디오 엔진은 picovave 자신의
[picovave/sim/worklet.js](picovave/sim/worklet.js)이다. 페이지의 패널은
물리 입력(18 버튼 · 8 엔코더 · 37 건반 · 16 패드 · 4 페이더 · 2 휠)을 picovave의
`sim_button` / `sim_key` / `sim_enc` / `sim_pad` / `sim_fader` / `sim_wheel`로 넣는다. 즉
**시뮬레이터 레벨에서** 동작을 확인할 수 있고 — "같은 소스 → fwsc + wasm" 원리로 — 실기에도
같은 방식으로 플래싱할 수 있다.


### 증명 (추정이 아니라 검사)

[tools/verify_final.py](tools/verify_final.py)가 매번 다음을 실행한다:

    ok   simulator includes the firmware's drawing core     ( #include "../src/picovave_gfx.h" )
    ok   simulator includes the firmware's app logic        ( #include "../src/picovave_app.h" )
    ok   simulator swaps only the HAL
    ok   input model is shared (picovave_input.h)              ( #include "picovave_input.h" )
    ok   app logic names no register or SPI symbol          (주석 제거 후 검사)
    ok   firmware/simulator/preview implements the input HAL
    ok   firmware reads pads/faders/wheels from the vendored HAL
    ok   the square-wave voice is shared (picovave/src/picovave_voice.h)
    ok   the device plays the keybed's note in the ALNK0 ISR
    ok   wasm screen == host preview, pixel for pixel       (240x240 전 픽셀 비교)
    ok   scripted input: wasm screen == host preview, byte for byte

앞의 픽셀 검사가 "같은 화면"을, 마지막 검사가 "같은 입력"을 증명한다. 같은 입력 열(버튼·건반·
엔코더·패드·페이더·휠, 22개 엣지)을 호스트 미리보기에도 wasm에도 순서대로 먹인 뒤 두 화면이
**바이트 단위로 일치**해야 통과한다. 즉 "브라우저에서 누른 것 = 롬이 그리는 것"이 주장이 아니라
테스트로 고정된다. 실기에서는 같은 공유 코드를 74HC595 매트릭스와 ADC mux가 구동한다.

### 정사각파 보이스 (기기와 시뮬레이터가 공유)

건반이 눌리면 **기기와 시뮬레이터 모두** 그 건반의 음높이로 정사각파를 재생한다. 음은
[picovave/src/picovave_input.h](picovave/src/picovave_input.h)의 공유 사실(`HIN_KEY_MIDI0`: 건반 0 = MIDI 48 =
C3)과 [picovave/src/picovave_app.h](picovave/src/picovave_app.h)의 `hi_note()`(눌린 건반 중 가장 낮은 것)로
정해지고, 파형 생성 코어는 [picovave/src/picovave_voice.h](picovave/src/picovave_voice.h)에 **한 번만**
있다 — 정수 위상 누산기(12평균율 비율 표 → 주파수 → 위상 증분)와 클릭 없는 40 ms 엔벨로프,
그리고 Q15 샘플 `hv_step()`뿐이라 부동소수점이 전혀 없다.

기기(picovave.c)는 루프에서 매 5 ms `hv_set(hi_note())`로 음을 따라가고, ALNK0 ISR에서 하프 버퍼마다
`hv_step()`을 24비트 I2S 버퍼로 shift해 코덱으로 보낸다. 시뮬레이터에서는
[picovave/sim/picovave_voice.h](picovave/sim/picovave_voice.h)가 같은 코어를 include하는 얇은 어댑터로,
`hv_render()`가 `hv_step()`을 워클릿 float 버퍼에 더한다 — 즉 **같은 정수를 계산**한다. 위
`the square-wave voice is shared` / `the device plays the keybed's note in the ALNK0 ISR` 검사가
그것을 고정한다. 같은 wasm을 노드에서 부팅하는
[picovave/sim/sim_test.mjs](picovave/sim/sim_test.mjs)가 무음 → 건반 누름 → 파형 발생 → 1초 동안
영교차 ~1046회(523 Hz의 2배) → 손 뗌 시 무음까지 확인하고, 실제 Chrome(CDP)에서 워클릿 출력을
AnalyserNode로 재어 같은 사실을 다시 확인한다.

### 시뮬레이터 화면

wasm은 **14,058 바이트**다 (Makkuro37의 sim.wasm이 152 KB인 것과 대비된다 — picovave에는 신스와
UI가 없고, 정사각파 보이스 하나만 더한다). [picovave/sim/sim_test.mjs](picovave/sim/sim_test.mjs)가
노드에서 브라우저와 동일하게 부팅해 검사한다:

    power-on draws the first screen / runs one loop iteration / waits its 100 ms frame
    screen is 240x240 RGB565 · mostly black field · white/green/dim text · no stray colour
    each iteration waits 100 ms · watchdog fed 20x per iteration · idle screen is static
    button down is counted and redraws · key/encoder input redraws · held key stays held
    pad hit · fader move · wheel move each counted and redrawn · pad release is one more edge
    silent while no key is held · a held key produces the square wave · its pitch is the key's note

### 빌드와 실행

    python3 tools/build_sim.py                 # → build/sim/{sim.wasm,index.html,worklet.js,picovave_render.js,fwsc.mjs,fm1ota.mjs,picovave.fwsc}
    python3 -m http.server -d build/sim 8738   # → http://localhost:8738

빌드 엔진은 자동 선택된다: emcc(PATH 또는 ~/emsdk)가 있으면 네이티브, 없으면
emscripten/emsdk Docker 이미지(이 환경이 그렇다). --engine 으로 강제할 수 있다.

페이지에는 SMK-37 Pro 패널(화면 + 6 네비 버튼, K1..K8 노브, 패드 16, 페이더 4, 피치/모드 휠,
2x6 버튼, 37건반)과 실시간 계기판이 있다. 버튼/건반/노브/패드/페이더/휠을 조작하면 페이지가
설치된 펌웨어에 맞는 `sim_button` / `sim_key` / `sim_enc` / `sim_pad` / `sim_fader` / `sim_wheel`로
wasm에 넣고 한 프레임을 돌려 LCD에 즉시 반영한다(picovave는 100 ms 루프). 오디오는 기본 켜짐
(SOUND on)이고 첫 사용자 제스처(클릭·키 입력)에서 시작한다 — 곧바로 picovave 자신의 워클릿
(`picovave/sim/worklet.js` = 오디오 엔진)에서 돌고, picovave는 건반을 누를 때 그 음의 정사각파를 낸다.
**SOUND** 는 음소거 토글이다. POWER-ON은 전원을 다시 넣고,
REFRESH는 루프를 한 바퀴 더 돌린다. 페이지의 실제 스크립트는
[picovave/sim/run_page.mjs](picovave/sim/run_page.mjs)가 스텁 DOM과 wasm에 물려 **실행**함으로써
검증된다(재구현하지 않는다).

### DEVICE: SIMULATOR(웹) 와 FLASH(USB)

페이지의 DEVICE 상자는 두 갈래다.

- **SIMULATOR** — 웹에서 바로 적용한다. `web/sim/fwsc.mjs`(= `tools/fwsc.py`의 이식본)로 패키지를
  검증하고(헤더 / 파일목록 / `flash.bin` CRC) 1 MB flash 모델에 `flash.bin`을 쓴 뒤, 바뀐 바이트 범위와
  보호영역(0x9d000 VM … 0xff000 key_mac) 침범 여부를 보여주고, 같은 소스의 wasm을 실행한다. USB로 쓰지
  않는다. 두 빌드는 identity가 같아서(`SMK-37 Pro_700`) 페이지는 로드한 패키지를 **내용**(`flash.bin`)으로
  구분해 맞는 wasm을 실행하고, 그 밖의 ROM(`SMK-37 Pro_016` 등)이면 "실기 전용"을 그린다 —
  **브라우저는 공유 C 소스만 실행하고 pi32v2 기계어는 실행할 수 없다**는 경계를 흐리지 않는다.
- **FLASH(USB)** — 진짜로 쓴다. `web/sim/fm1ota.mjs`는 `picovave/platform/tools/fm1_install.py`의 Web MIDI 이식으로,
  SysEx `F0 22 24 35 7F F7` 업그레이드를 시작해 기기의 read 요청(cmd `0x30`)에 로컬 이미지로 응답하고,
  `0xE0000000` / `0xF0000000` "success" 로 로더 스테이징과 app 영역 기록을 마친 뒤 재시작한 기기의
  identity를 확인한다. flash 모델도 wasm도 거치지 않는다 — 하드웨어가 자기 이미지를 실행한다. 순정 v016
  같은 "실기 전용" 패키지도 이 경로로는 쓸 수 있다(공식 로더를 싣고 있으므로 `force`). 개봉 직후의 실기는
  USB-MIDI **포트가 여러 개**(`-Private`/`-Master`/`-MCP`)라 업데이트에 응답하는 포트가 이름으로 드러나지
  않는다. 그래서 `find` 가 패밀리 포트를 모두 질의하고, 실패하면 `probeAll`(MIDI 포트 진단)이 **모든** 포트에
  순서대로 handshake 를 보내 응답한 포트와 identity 를 보고한 뒤 그 포트를 골라 준다. 포트를 열자마자 보내면
  CoreMIDI 가 버릴 수 있어(참조와 같이) `open` 뒤 0.3 s 기다린다. 여러 케이블 기기는 in/out 이름이 1:1 이
  아닐 수 있으므로, 짝 출력이 응답하지 않으면 같은 입력에 대해 다른 패밀리 출력으로도 질의한다(정답은 항상
  그 입력으로 온다). `pairOutput`(이름 → 짝 출력, Windows 의 `이름 2` 번호 매김까지)과 이 순서를
  `fm1_install.py` 와 교차검증한다.
- 원복은 사용자가 **직접 가진** 순정 v016을 끌어다 놓거나 선택해 SIMULATOR 또는 FLASH(USB)로 되돌린다.
  순정 펌웨어는 저작권물이라 저장소에 넣지 않는다. `picovave.fwsc` 내려받기 링크도 제공한다.
- 이식본은 조용히 어긋나므로, [picovave/sim/fwsc_check.mjs](picovave/sim/fwsc_check.mjs)가 두 리더를 실제
  패키지에 돌려 같은 답을 요구하고, [picovave/sim/fm1ota_check.mjs](picovave/sim/fm1ota_check.mjs)가
  `fm1ota.mjs`의 wire 포맷을 `picovave/platform/tools/fm1_install.py`와 **교차검증**하며 가짜 기기로 전체 업데이트를 끝까지 돌려
  매 바이트를 대조한다. `run_page.mjs`는 picovave를 SIMULATOR / 드롭-원복 / 입력까지 페이지에서 실제로
  눌러 검증하고, FLASH(USB)가 Web MIDI 없이도 조용히 실패하는지까지 본다.

### 두 계층의 검증이 서로를 지킨다

| 검증 | 대상 | 무엇을 보장하나 |
|---|---|---|
| wasm ↔ 호스트 미리보기 픽셀 일치 | 소스 동일성 | 시뮬레이터가 롬의 코드를 실제로 실행한다 |
| 패키지 구조 검사 (CRC, 진입점, 영역) | 롬 이미지 | 그 코드가 플래시에서 제대로 부팅한다 |

둘을 함께 보면 "화면에 보이는 것"과 "기기에 올라가는 것"이 같은 코드임이 연결된다.

---

### 세 번째 실패 유형: 페이지와 wasm의 API 불일치

시뮬레이터를 만들면서 실제로 났던 버그와, 그것을 막는 장치를 기록해 둔다.

**증상**

    (index):100 Uncaught TypeError: ex.sim_ms is not a function
        at stats ((index):100:29)
        at boot ((index):139:3)

**원인** — HAL 내부 변수와 export 함수 이름이 충돌했다(`sim_ms` 변수와 `sim_ms()` 함수는
공존할 수 없다). 그래서 export를 sim_clock_ms / sim_push_bytes 로 임시 개명했는데,
**부팅 테스트와 wasm만 고치고 페이지는 고치지 않았다.** 게다가 검증은 wasm 쪽만 보고
페이지가 어떤 API를 부르는지는 보지 않았다. 페이지는 서빙되기만 하면 통과였다.

**수정** — 공개 이름을 자연스러운 형태로 되돌렸다: 내부 상태를 sim_dev_* 로 바꿔
sim_ms() / sim_bytes() 를 다시 export하고, 페이지가 원래 쓰던 이름과 맞췄다.

**재발 방지 (세 겹)**

| 검사 | 무엇을 보나 | 언제 |
|---|---|---|
| [check_api.mjs](picovave/sim/check_api.mjs) | wasm export 목록 vs 페이지가 부르는/읽는 이름 | tools/build_sim.py 빌드 시 |
| [check_api.mjs](picovave/sim/check_api.mjs) | 페이지 인라인 모듈 스크립트 파싱 | 같은 시점 |
| [run_page.mjs](picovave/sim/run_page.mjs) | 페이지의 실제 모듈을 스텁 DOM + wasm에 물려 실행 | 같은 시점 |

이 시점부터 export를 개명하면 페이지가 낡은 이름을 부르는 순간 **빌드가 실패한다.**
픽셀 일치 검사가 "같은 코드"를 지키고, 이 검사가 "같은 API"를 지킨다.

### 검증 항목 (119개)

- 앱: 진입 스텁, _start, 인터럽트 핸들러 없음(HAL 임계구역 1개만), 워치독, 레지스터 격리
- 패키지: 컨테이너/파일목록/flash.bin/ota.bin CRC, 식별자, 마커
- 패커 교차검증: Makkuro37의 `fm1pkg_make` 산출물 == 우리 독립 `picovave_pack.py`, 바이트 일치
- 리더 이식: `fwsc.mjs`의 파싱 결과가 `fwsc.py`와 동일 (picovave.fwsc, 순정 v016)
- 플래셔 이식: `fm1ota.mjs`의 wire 포맷(pack7/unpack7/crc16/ENC/response/parseRequest/
  parseIdentity/pairOutput)이 `fm1_install.py`와 동일, 가짜 기기로 전체 업데이트 완주
- 앱 영역: app_area_head CRC, offset, app.bin 내용, 슬롯 패딩
- 시뮬레이터: wasm export ↔ 페이지/워클릿 호출 일치, 인라인 스크립트 파싱, 페이지 실행,
  picovave SIMULATOR · 드롭-원복 · 입력(버튼/패드/페이더/휠) 반응, FLASH(USB) 무해 실패
- DEVICE 구조: 페이지에 SIMULATOR 와 FLASH(USB) 가 분리되어 있는지, 실패 시 MIDI 포트를
  진단(모든 포트에 handshake)해 응답한 포트를 고르게 하는지
- 소스 동일성: 세 소비자가 펌웨어 헤더를 include, 앱 로직에 레지스터 없음,
  입력 모델 공유, 세 HAL이 입력 HAL 구현, 실기 HAL이 Makkuro37 `smk_input.h`를 씀
  (패드 속도·페이더·휠 포함), 정사각파 보이스가 기기와 시뮬레이터에 **공유**됨
- 기기 스택: vendored `usb.c`/`ota.c`, 10 kHz TIMER5 설치, 디바이스가 mask ROM UBOOT으로 탈출 가능
  (bootguard 포함)
- 픽셀 일치: wasm 화면 == 호스트 미리보기 (입력 없음), 같은 스크립트 입력 후 바이트 일치
- 파급 범위: 보호 영역 6곳 미접촉, 영역 기술자가 순정 v016과 바이트 동일

---

## 12. 빌드가 실패했는데 검증이 통과했다

이 프로젝트에서 가장 위험했던 순간은 버그가 아니라 **거짓 초록불**이었다. 두 사건이 같은
모양을 하고 있었다.

### 사건 1: 프리스탠딩 ROM과 memcpy

앱이 입력 모델(picovave_input.h)을 갖추면서 hi_push()가 생겼다. 이 함수는 이벤트 로그를 한
칸씩 밀어내는데, 작성자는 libc를 피하려고 필드별로 복사했다:

    for (; i > 1u; i--) {
        hi_hist[i-1].kind = hi_hist[i-2].kind;
        hi_hist[i-1].id   = hi_hist[i-2].id;
        hi_hist[i-1].val  = hi_hist[i-2].val;
    }

그런데 -O2의 LLVM은 이 루프를 **memcpy 호출로 되돌렸고**, ROM은 libc를 링크하지 않으므로:

    undefined reference to 'memcpy'
    relocation truncated to fit: R_PI32V2_LONG_JUMP_23M2 against undefined symbol 'memcpy'

고칠 것은 두 가지였다.

1. **의미를 최적화에 맡기지 않는다.** hi_push를 지역 변수를 경유한 교환으로 다시 써서
   모든 복사의 원본이 겹치지 않게 했다. 이제 어떤 최적화가 일어나도 뜻이 변하지 않는다.
2. **없는 런타임을 제공한다.** picovave.c에 memcpy/memmove/memset/memcmp를 넣었다.
   optnone + noinline이 핵심이다. 없으면 최적화기가 이 루프마저 memcpy로 바꿔
   **자기 자신을 호출하는 함수**를 만든다.

결과는 디스어셈블리로 확인했다. memcpy는 이미지에 있지만(0x2000138) 스스로를 부르지
않는다 — 본문의 분기는 전부 goto이고, 유일한 call은 호출자(0x2000406)에서 온다.

### 사건 2: 검증이 낡은 산출물을 칭찬했다

링크가 실패했는데 [tools/verify_final.py](tools/verify_final.py)는 **all checks passed**를
출력했다. 이유는 단순하다. 빌드가 실패하면 build/ 안의 **이전 산출물이 그대로 남고**,
검증은 그걸 검사했다.

이건 이 프로젝트가 낼 수 있는 최악의 결과다. 초록불은 "이 트리는 정상"이라는 주장인데,
그 트리가 컴파일조차 안 될 수 있다면 초록불은 거짓말이다.

그래서 빌드 신선도를 검증의 **첫 항목**으로 넣었다:

- [tools/build_picovave.py](tools/build_picovave.py)가 성공할 때마다 입력 13개의 sha256을
  build/picovave/.build_stamp.json 에 기록
- [tools/verify_final.py](tools/verify_final.py)가 그 해시를 다시 계산해 다르면 즉시 실패

    == build freshness ==
      ok   the package was built from the sources now on disk - stamp matches 13 inputs

소스를 한 글자라도 고치고 재빌드하지 않으면:

      FAIL the package was built from the sources now on disk
           - rebuild needed, changed: picovave/src/picovave_app.h

### 교훈

| 증상 | 진짜 원인 | 막는 장치 |
|---|---|---|
| undefined reference to memcpy | 최적화가 루프를 libc 호출로 바꿈 | 겹치지 않는 복사 + 자체 런타임(optnone) |
| all checks passed (빌드 실패) | 낡은 산출물을 검사 | 빌드 스탬프 + 신선도 검사 (검증 1번 항목) |

실패가 거듭 같은 모양이었다: **검사가 대상과 다른 것을 보고 있었다.** 픽셀 비교는 소스
동일성을, 페이지 실행은 API 일치를, 스탬프는 "지금 소스로 빌드했는가"를 본다.
무엇을 검사하는지 스스로 말할 수 없는 검사는 통과해도 의미가 없다.

---

## 14. 알려진 미해결 지점

정직하게 남겨 둔다. 설치에 필요한 항목이 아니어서 진행을 막지는 않지만, 완전히 설명되지는 않았다.

1. **순정 패키지의 보조 엔트리 검사값** — 순정 v015/v016에는 flash.bin, ota.bin 외에
   info.log, USR, isd_config.ini, script.ver, blimit.bin, tail.bin 엔트리가 더 있다.
   그중 tail.bin(64 B)은 평범한 CRC16(다항식 0x1021, 초기 0)과 일치하지만,
   USR / isd_config.ini / script.ver / blimit.bin은 CRC16 · CRC-CCITT(init 0xFFFF) ·
   CRC-32 하위/상위 · 단순 합 · 워드 XOR 어느 것으로도 재현되지 않는다. 별도 스트림/압축
   규약일 가능성이 있다. **picovave 패키지는 이 항목들을 싣지 않으며**, 순정 업데이터가
   실제로 요구하는 flash.bin/ota.bin 두 항목의 CRC는 정확히 재현된다.
2. **해시/서명** — 현재 확인한 범위에서 펌웨어 서명이나 공개키 검증은 없다. 무결성은
   CRC16과, 설치 중 실행 중인 펌웨어가 수행하는 자체 검사에 의존한다. (앱 내부에 추가
   검증이 있다면 이번 분석 범위 밖이다.)
3. **실기 미검증** — 이 환경에는 SMK-37 Pro가 없다. 따라서 검증은 정적·구조적 수준이다:
   패키지 전체 CRC, 앱 진입점/스텁, 링커 배치, 영역 기술자 일치, 호스트 렌더.
   실기 설치와 화면 표시는 사용자가 확인해야 한다.

