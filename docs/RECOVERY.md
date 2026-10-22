# 복원: USB로 안 잡히는 기기를 mask-ROM UBOOT으로 되살리기

이 문서는 **기기가 USB-MIDI로 인식되지 않아 M-UPGRADE(FLASH(USB) / `fm1_install.py`)로
아무것도 쓸 수 없을 때**의 복구 절차다. 대표적인 경우가 **USB 스택이 없는 옛 picovave**가 올라가
있는 상태 — 앱이 실행돼도 USB로 enumerate하지 않아 MIDI 포트도, 업데이트 리스너도 없다.

빌드된 `picovave.fwsc`는 이 문제를 고친다(USB-MIDI + OTA). 하지만 그 패키지를
**기기에 넣는 것 자체**가 USB를 필요로 하므로, USB가 죽어 있으면 칩에 내장된 **mask-ROM UBOOT**을
통해서만 넣을 수 있다. mask-ROM UBOOT은 순정 flash의 어느 것도 지워지지 않고(un-erasable), 그곳에서
flash를 임의로 읽고/지우고/쓸 수 있다.

> 이 경로는 실기 SMK-37 Pro에서 검증됐다(같은 JieLi AC791N/WL82 SoC의 FM-1 커뮤니티 도구를
> 사용). 절차·주소·보호영역은 `picovave/platform/firmware/hal/fm1_flash_map.h`의 SMK-37 Pro 맵과
> genkigenki/fm1-to-smk37 `docs/02-update-and-recovery.md`의 검증된 공정을 따른다.

---

## 0. 무엇을 쓰는가

M-UPGRADE 설치(웹 FLASH(USB) / `fm1_install.py`)는 패키지의 **app 영역만** 쓴다.
SMK-37 Pro에서 그 영역은 **flash `0x04000-0x9CFFF`**, 즉 패키지 `flash.bin`의
`[0x4000:0x9D000]` = **`0x99000` 바이트**다. 패키지의 `flash.bin`은 **flash에 들어가는 그대로의
(암호화된) 이미지**이므로, 이 범위를 UBOOT에서 그대로 쓰는 것은 **정상 설치와 바이트 단위로 같다**.

그래서 head(`0x00000-0x03FFF`)는 건드리지 않는다. 기기의 head는 그대로 두고 app 슬롯만 교체한다.

---

## 1. 필요한 것

- **macOS / Linux / Windows 모두 지원**:
  - **macOS**: `tools/jl-uboot-tool`에 네이티브 PyUSB (Bulk-Only Transport) 드라이버(`scsiio/darwinport.py`)가 내장되어 있어, **터미널에서 `sudo` 명령 한 줄로 즉시 복구**할 수 있습니다.
  - **Linux(루트) / Windows(관리자)**: `tools/jl-uboot-tool`의 SCSI 인터페이스로 직접 복구 가능.
- USB **데이터** 케이블(충전 전용 X), 허브 없이 Mac/PC 직결.
- 복구 도구: 이 저장소에 내장된 `tools/recover_fm1.py` 또는 `tools/recover_smk37.py` (또는 `tools/recover_uboot.py`).
- 복구할 패키지: `build/picovave-fm1.fwsc` 또는 `build/picovave-smk37.fwsc` (`build/picovave.fwsc`).
- 기기 전체 flash **백업을 보관할 곳**(필요 시 4단계). `0xC0000-0xC1FFF`(BT/USRTRIM)와
  `0xFF000-0xFFFFF`(key_mac)는 **그 기기에만 있는 값**이므로 임의로 덮어쓰지 않습니다.

---

## 2. mask-ROM UBOOT으로 들어가기

기기는 이때 **`WL82 UBOOT1.00`** 라는 USB **대용량 저장장치**(읽을 수 없는 디스크)로 나타나며,
USB ID는 **`VID 4C4A / PID 8057`** 다.

들어가는 방법(위에서 아래로 시도):

1. **앱이 죽어 있으면 자동**: 부팅에 실패하면(flash에서 못 부팅) mask ROM이 스스로 UBOOT으로
   들어간다. "소리도 없고 USB도 안 잡힘"이면 앱이 실제로 죽어 **이미 이 상태일 수 있다** —
   먼저 3단계로 확인해 보라.
2. **전원 투입 시 OCT- 홀드**: 전원을 끄고 **OCTAVE-** 를 누른 채 전원을 켠다. 몇 초 유지.
   이 저장소의 **새 빌드에서는 앱이 이 키를 먼저 잡아 앱 레벨 USB RESCUE로 들어간다** — 그때는
   3단계의 mask-ROM이 아니라, 아래 문단처럼 **웹 인스톨러/`fm1_install.py`로 재플래싱**하면 된다.
   앱이 아예 못 뜰 때만 mask ROM이 이 키를 본다.
   (주의: 앱 레벨 RESCUE는 **헤드리스**다 — 화면에 아무 것도 띄우지 않는다. 그 모드에 들어갔는지는
   **USB-MIDI 포트가 보이는가**로 판단한다. 이유는 아래 "앱 레벨 RESCUE" 문단.)
3. 그래도 아무 것도 안 뜨면 **USB_KEY 동글**(RP2040, `0x16EF` 패턴): [FM-1 Transporter](https://github.com/kurogedelic/FM-1-transporter)
   / [MvaveFM1Unbricker](https://github.com/Quixotic7/MvaveFM1Unbricker). 하드 브릭용이며
   SMK-37 Pro에서는 아직 미검증이다(칩과 boot ROM은 동일).

이 저장소의 새 picovave는 (a) 실행 중 `OCT-`+`OCT+` 5초, (b) USB SysEx `F0 22 24 35 7D F7` 로 UBOOT에
들어가고, (c) 전원 투입 시 `OCT-` 홀드거나 부트 실패 2회면 **앱 레벨 USB RESCUE**(웹 인스톨러/
`fm1_install.py`로 바로 재플래싱; jl-uboot-tool 불필요)로 들어가며, RESCUE에서도 (a)/(b)가 그대로
동작하고, 그 RESCUE조차 리셋을 반복하면 mask ROM으로 떨어진다 — **한 번 새 빌드가 올라가면 다시는
이 문서가 필요 없다**.

### 앱 레벨 RESCUE (헤드리스)

RESCUE는 **usb.c + ota.c 뿐**이다: 패널도, 폰트도, gfx도, 엔진도 없다. 인스톨러(웹 페이지/CLI)가
화면이므로, 들어갔는지는 **USB-MIDI 포트가 enumerate되는가**로 본다(패널은 비어 있다).

여기서 중요한 사실이 하나 있다. `usb.c`의 USB SIE 처리는 **오직 TIMER5 ISR(2 kHz)에서만**
돌아간다(`usb_poll()`의 유일한 호출자다). 이전 RESCUE는 "no TIMER5"라서 루프에서 500 ms에 한 번
`usb_poll()`을 불렀고, 그 상태로는 512바이트 업데이트 프레임(128 USB 패킷)이 `ota_read()`의
5초 창 안에 들어올 수 없다 — 핸드셰이크는 되는데 업데이트 세션은 **시작조차 못 하고** "stopped
answering after 0 requests"로 끝난다. 그래서 RESCUE는 이제 TIMER5를 켜고, `flash_ok`(JEDEC)에
세션을 걸지 않는다(막는 대신 시도하고 실패를 프로토콜로 보고한다).

### RESCUE가 살아 있는지 / UBOOT으로 보내기 (수동 MIDI)

웹 인스톨러가 이상할 때 원시 바이트로 직접 확인한다:

```sh
python3 tools/ota_probe.py --list                 # 어떤 MIDI 포트가 보이는가
python3 tools/ota_probe.py --ask --secs 3         # 아이덴티티 질의 (핸드셰이크)
python3 tools/ota_probe.py --upgrade --secs 10    # M-UPGRADE 업그레이드 명령: 기기가 0x30 요청을 보내는가
python3 tools/ota_probe.py --uboot                # soft key: mask-ROM UBOOT으로 리셋
```

`ota_probe.py`는 `mido`/`python-rtmidi`가 필요하다. 없으면 스스로 venv를 찾아 재실행한다
(`tools/.venv`, `tools/.venv-ota`, `picovave/.venv*`, 저장소 루트 `.venv*`, `~/.venv-ota` 순;
`PICOVAVE_PY`로 인터프리터를 지정할 수 있다). 설치:

```sh
python3 -m venv tools/.venv-ota && tools/.venv-ota/bin/pip install mido python-rtmidi
```

수신은 **입력 포트**로만 된다(mido의 `BaseOutput`에는 `iter_pending`이 없다): 프로브는 송신
포트와 같은 이름의 입력 포트를 함께 열고, 기기→호스트 프레임은 거기서 읽는다. 창을 열어두려면
`--watch --secs 10`. 참고로 **mask-ROM UBOOT 상태의 기기는 MIDI 포트가 아니라 USB 디스크**로
enumerate되므로 `--list`에 보이지 않는다(그때는 3절의 확인 방법을 쓴다).

`--upgrade`에서 기기가 아무 것도 보내지 않으면 기기 쪽 문제(USB를 못 받거나 세션을 거부),
`0x30` 요청이 보이면 호스트 쪽 문제다.

### 크래시 화면 읽기 (`CRASH` / `LAST CRASH`)

앱이 죽으면(가드/CPU 예외) 폴트 핸들러가 화면을 그리고 3초 뒤 리셋한다. 리셋이 반복되면 부트
실패 2회로 RESCUE에 들어가므로, **크래시 화면은 지나가는 화면이 아니라 "왜 죽었는지"를 읽을 기회**다.
`fm1_fault()`가 남기는 기록(`fm1_crash`, `.noinit`)을 헤드라인 + 12줄로 그린다:

| 줄 | 뜻 |
|---|---|
| `LAST CRASH` / `CRASH` | 헤드라인. `CRASH`는 지금 죽어서 그리는 화면(3초), `LAST CRASH`는 **다음 부팅**이 그리는 같은 리포트(10초) |
| `PC xxxxxxxx` | 폴트를 일으킨 명령의 주소(XIP는 flash offset + `0x02000120`) |
| `Vxx EMUxxxx DBGxxxx` | 어느 벡터인가 / EMU 상태(`bit3` = **sp가 스택 창 밖** — 현재 빌드는 이 창을 쓰지 않으므로 보통 0) / 버스·쓰기 트랩 비트. 공백 없이 붙여 쓴다 — 캔버스가 128 px이라 19자가 상한이다 |
| `SP xxxxxxxx U BELOW\|ABOVE\|OK` | 폴트 시점의 **핸들러 자신의** 스택 포인터와 판정(앱의 sp는 프레임에서 읽히지 않는다 — `DEEP` 참고). `U`/`S`는 그 sp가 어느 스택인가 |
| `DEEP xxxxxxxx U\|S\|?` | 10 kHz ISR이 표본한 **가장 깊은 스택 주소**와 그 스택(`?` = 두 창 밖). 즉 주 루프가 어느 스택에서 얼마나 깊이 갔는가 |
| `USP xxxxxxxx` / `SSP xxxxxxxx` | 프레임의 나머지 두 sp 슬롯, 원시값 — 이 실리콘에서는 스택이 아니라 데이터처럼 보이는 값이 들어 있고, 그래서 그대로 찍는다 |
| `RET xxxxxxxx` | 폴트 시점의 리턴 주소 |
| `ETM0 xxxxxxxx` / `ETM1 xxxxxxxx` | ETM 트레이스(하드웨어 백트레이스) 최근 2개 |
| `Nx EARLYx UPxxxxxxMS` | 누적 크래시 횟수 / 부팅 3초 내 연속 크래시 횟수 / 죽기까지 살아 있던 시간 |
| `WILD xxxxxxxx /xxxxxx` | **어느 스택에도 없는 sp**를 표본한 틱(0이면 정상)과 그때의 pc(하위 24비트). `longjmp`로 죽은 프레임에 뛰어든 경우나 `setjmp` 프롤로그가 잡힌다 |
| `BUILD xxxxxxxx` | 이 화면을 그린 빌드의 태그 |

같은 값이 **정상 부팅의 빵가루 화면**(`PICOVAVE BOOT`)에도 한 줄로 나온다: `STACK 0xxx/5F00`은
**직전 실행**이 쓴 최대치다(스택을 칠해 두고 다음 부팅에서 먼저 훑기 때문에, 크래시 없이도 여유를
볼 수 있다). 즉 크래시가 나면 `SP ... BELOW` + 큰 `STACK`이면 "카트가 너무 깊이 재귀",
`DEEP`가 `U` 창 밖(`?`)이면 "sp가 창 밖으로 갔다"로 갈린다(ISR이 매 틱 표본한 최저 주소).

화면 자체는 `hg_crash_screen()`(`src/picovave_gfx.h`)이 그리고, ROM과 같은 함수를 호스트
프리뷰가 스크립트 값으로 렌더한다(`build/picovave/preview --crash out.ppm`; 부팅 리포트는
`--crash-boot`) — 실기 없이 레이아웃을 볼 수 있고 `tools/verify_final.py`가 13줄이 패널 안에
들어가는지(빈 줄·양끝 잘림·줄 충돌)를 캔버스 격자에서 계산해 검사한다. 그리는 면은 카트와 같은
**128×128 RGB565 캔버스**(`p8_screen_fb`, `femto8/seam/gdi.h`)이고, 패널 240×240으로는 표시
경로가 한 줄씩 ×1.875 최근접 확대한다 — 그래서 모든 글자가 정수배 픽셀이고 실기 사진에서도 읽힌다.
구 240×240 화면은 폭이 넓어 `USP`/`SSP`와 `RET`/`ETM`을 한 줄에 붙였지만, 128폭에서는 글자 6 px로
한 줄에 **20자가 여백을 두고** 들어가는 상한이라(21자는 양끝이 베젤에 닿는다; `WILD`의 sp+pc만
예외로 붙인다) 각각 자기 줄을 쓴다.

### 크래시의 원인(2026-10-17에 확정): newlib `setjmp`의 sp 사용 vs EMU 스택 창

초기 빌드가 부팅 직후 `EMU 0008 DBG 0000`으로 죽었고, `fm1_crash`의 PC는 `setjmp` 내부였다.
원인은 **엔진/카트가 아니라 툴체인의 `setjmp` 구현**이었다:

- 이 툴체인의 `setjmp`는 **`jmp_buf` 자체를 스크래치 스택으로 쓴다** — 레지스터를 `jmp_buf[0..63]`에
  저장한 뒤 `sp = jmp_buf + 64`로 놓고 그 `sp`로 `psr/rets/reti` 블록을 push한다(`longjmp`도 같은
  경로로 복원). 버퍼 크기(100 B = `_JBLEN 25`)와 쓰는 범위는 정확히 일치한다 — 오버런이 아니다.
- `FM1_GUARD_STACK`은 EMU의 sp 허용 창을 `[_ustack_lo, _sstack_top]`로 잡는다. 앱의 `jmp_buf`는
  `.bss`에 있으므로 **첫 프레임의 `setjmp`에서 곧바로 "sp 창 밖" 트랩**이 났다.
- Lua의 `jmp_buf`는 스택 지역변수라 창 안이다 → 그래서 시뮬레이터(emscripten `setjmp`)와 Lua
  자체는 멀쩡했고, 매 프레임 도는 앱의 `setjmp(jmpbuf_restart)`만 걸렸다.

**수정**: EMU ス택 창을 쓰지 않는다(`fm1_guard_enable(FM1_GUARD_WRITE | FM1_GUARD_BUS |
FM1_GUARD_PC)` + `FM1_EMU_CON &= ~(1u<<3)`). 즉 **`EMU bit3`(sp 창 밖) 크래시는 이제 나오지 않는
것이 정상**이고, 나온다면 그 창을 다시 켠 빌드다. 남는 스택 보호는 아래와 같다.

| 보호 | 잡는 것 |
|---|---|
| `FM1_GUARD_WRITE` 쓰기 밴드(`_guard0`, `_guard1`) | 스택이 밴드까지 자란 오버플로(실제 오버플로의 일반적 모양) |
| `-DLUAI_MAXCCALLS=48`(실기·시뮬레이터 동일) | Lua C 중첩 한도. 스톡 200은 보호 레벨당 **316 B** × 200 ≈ **63,200 B**로 사용자 스택 **24,320 B**를 넘겨, Lua의 "C stack overflow"보다 **하드웨어 크래시가 먼저** 왔다. 48(에러 경로 여유 포함 ≈ 17 KB)로 한도를 스택에 맞췄다 |
| 부팅 화면 `STACK n/5F00` | 직전 실행의 스택 최대 사용량(스택을 칠해 두고 다음 부팅에 훑는다). `5F00` = 24,320 B |

사각지대는 "밴드(256 B)를 한 번에 건너뛰는 단일 프레임" 하나뿐이다 — 그 경우만 밴드가 안 잡는다.
`LUAI_MAXCCALLS=48` 덕분에 엔진 경로에서는 그런 프레임이 나오지 않는다.

주의: `LUAI_MAXCCALLS=48`은 **크래시의 원인을 제거한 것이 아니라** 한도를 스택에 맞춘 것이다(그리고
이건 여전히 필요하다 — 위 수정과 별개로 유효하다). 순수 Lua 재귀는 C 스택을 쓰지 않으므로
(루프로 처리된다) 영향받지 않는다.

### 크래시의 원인(2026-10-23 실기 검증): pv_early_mark()의 m_memory NULL 포인터 역참조

FM-1 최초 플래싱 후 재부팅 시 발생했던 즉각적인 크래시 및 UBOOT 전락 원인:

- **발생 기전**: SPI DMA 대기 시간을 활용하기 위해 `pv_push_dirty()` 내 각 행 전송마다 `pv_audio_step_fill()`을 호출하도록 최적화하였음.
- **문제점**: 부팅 극초기인 `fm1_cstart()`에서 화면에 빌드 태그를 그리는 `pv_early_mark()`가 실행될 때, 아직 PICO-8 엔진 초기화(`p8_init()`)가 수행되지 않아 `m_memory` 포인터가 `NULL` 상태였음.
- `render_sounds()`가 `m_memory[MEMORY_AUDIO_PAUSE]`(주소 `0x00005F2F`)를 역참조하면서 하드웨어 버스 가드(`FM1_GUARD_BUS`)가 트랩되어 Vector 1 CPU Exception 발생.
- 예외 핸들러 `fm1_fault()` 내의 크래시 화면 출력(`hg_crash_screen()`)에서도 `pv_push_dirty()`를 거치며 재귀적으로 크래시가 발생, 2회 부팅 실패 후 부트가드에 의해 `WL80UBOOT1.00` 다운로드 모드로 자동 진입함.
- **해결책**:
  1. `pv_audio_step_fill()` 및 `pv_audio_fill()`에 `pv_audio_up && m_memory` 가드를 추가하여 엔진 및 오디오 초기화 완료 전에는 절대 믹싱을 시도하지 않도록 방어.
  2. `pv_audio_up` 플래그는 `pv_audio_init()`에서만 1로 세팅되고, 폴트 핸들러 `fm1_fault()` 진입 시 즉시 0으로 소거.
  3. 변수 추가에 따른 `.bss` 정렬 변화를 `pv_ring_bss_pad[4076]`로 상쇄하여 SMK-37 Pro(`415,696 B`) 및 FM-1(`416,848 B`)의 Lua 힙 아레나 메모리 주소를 1바이트 오차 없이 보존.
  4. 본 저장소의 `tools/recover_fm1.py` 및 `tools/recover_smk37.py`를 통해 macOS에서 즉시 복구 완료.

### 어느 빌드가 올라가 있는지 (패널을 믿지 말 것)

**LCD는 마지막으로 그려진 프레임을 그대로 들고 있다.** 새 앱이 패널을 켜기 전에 죽으면
화면은 **이전 빌드가 남긴 그림**이고, 그것을 보고 "플래싱이 안 됐다"고 판단하면 틀린다.
그래서 빌드는 **자기 이름을 USB로 말한다**: identity 응답(27바이트)의 NUL 패딩 뒤에
그 빌드의 태그가 붙는다(`ota.c`의 `PV_BUILD_TAG`, 빌드 입력 전체의 sha256 앞 8자리).
업데이터가 읽는 identity 문자열은 **한 바이트도 바뀌지 않는다**(태그는 종료 NUL 뒤에 있다).

```sh
python3 tools/ota_probe.py --ask
#   ...  <- identity: SMK-37 Pro_700  build 993b2146
```

- `build xxxxxxxx`가 **이번 패키지의 태그와 같으면** 플래싱이 들어간 것이다(`build_picovave.py`가
  빌드 끝에 `tag  xxxxxxxx`를 찍는다).
- `(no build tag: a pre-2026-10-17 build)`면 **그 태그 기능 이전 빌드가 아직 돌고 있다** =
  이번 설치가 들어가지 않았다.
- `--flash`는 설치 **전후의 태그를 함께 찍고**, 같은 태그면 `note:`로 그 사실을 알린다.

설치 자체의 신뢰도: 스테이징된 로더는 **4KB 섹터마다 flash를 다시 읽어 바이트 비교**하고
(다르면 erase+program을 두 번까지 재시도, 그래도 다르면 `-11`로 실패) 전체 `flash.bin`의
CRC16까지 확인한다. 그래서 **`done: ...`이 나왔다면 app 영역은 실제로 그 이미지다** — 그런데도
화면이 그대로라면 위의 stale frame을 의심해야 한다(패널이 아니라 USB를 보라).

**크래시 화면을 놓칠 일은 없다.** 기록은 `.noinit`에 남아 리셋을 넘어가므로, **죽은 다음 부팅이
같은 화면을 정지 화면으로 다시 그린다** — 헤드라인이 `LAST CRASH`이고 **10초** 유지된다
(그 사이 전원 투입 때부터 눌린 버튼이 있으면 즉시 넘어간다). 한 번 보여준 뒤 기록의 magic을
지우므로 그 뒤 부팅은 조용하다. 즉:

- 크래시 → 3초 화면 → 리셋 → **다음 부팅에 같은 내용이 10초** → 그다음 정상 진행.
- 크래시가 간헐적이라 다음 부팅이 멀쩡히 떠도 **리포트는 그대로 뜬다**(직전 실행의 스택 최대치 포함).
- 10초는 부트가드가 "정상 부팅"으로 세는 30초 안쪽이라, 리포트 때문에 부팅 실패로 세어지지 않는다.

그래서 플래싱 한 번이면 "뜨는가"와 "왜 죽었는가(스택 깊이 포함)"를 **동시에** 얻는다. 다시
플래싱해서 실패해도 잃는 것은 없다: 중단된 설치든 부트 실패 2회로 들어간 RESCUE든, 같은 MIDI
경로로 다시 쓰면 된다(`--resume-loader`가 필요한 경우는 그 화면/에러가 문자열을 알려준다).

---

## 3. 기기가 UBOOT으로 떴는지 확인

- **Linux**: `lsusb | grep 4c4a`, `lsscsi -g` (또는 `dmesg`)에 `WL82 UBOOT1.00`.
- **Windows**: 관리자 PowerShell에서
  `Get-CimInstance Win32_DiskDrive | Format-Table Index, Model, InterfaceType`
  → `WL82 UBOOT1.00 USB Device`.
- **macOS**: `system_profiler SPUSBDataType | grep -A3 -i uboot` (또는 시스템 정보 → USB).
  USB 디스크로 잡히면 `diskutil list`에도 1 MB짜리 디스크로 보인다. **여기서 쓰기는 안 된다** — 아래 3.1.

장치 경로는 Linux `/dev/sgN`, Windows `\\.\PhysicalDriveN`(이름이 `WL82 UBOOT`인 디스크).

### 3.1 macOS에서 어디까지 되는가 (결정 트리)

이전에는 `jl-uboot-tool`이 raw SCSI(Linux `/dev/sg*`, Windows `DeviceIoControl`)만 지원하여 macOS에서 UBOOT 복구가 불가능했으나, **이 저장소에 macOS용 PyUSB Bulk-Only Transport 백엔드(`tools/jl-uboot-tool/scsiio/darwinport.py`)를 자체 구현/탑재하여 macOS에서도 mask-ROM UBOOT 복구가 100% 지원**됩니다.

| 기기 상태 | macOS 복구 수단 | 복구 명령 |
|---|---|---|
| 앱 정상 실행 / 앱의 RESCUE(부팅 2회 실패) / 업데이트 로더 | **USB-MIDI로 간편 복구** (권한 불필요) | `python3 platform/tools/fm1_install.py build/picovave[-fm1].fwsc --yes` |
| mask-ROM UBOOT (`WL80UBOOT1.00` / `WL82 UBOOT1.00`, MIDI 포트 없음) | **원클릭 UBOOT 플래시 복구** (macOS `sudo` 필요) | FM-1: `sudo .venv-ota/bin/python tools/recover_fm1.py`<br>SMK-37 Pro: `sudo .venv-ota/bin/python tools/recover_smk37.py` |

> **macOS에서 `sudo`가 필요한 이유:**  
> macOS 커널(IOKit)의 보안 정책상, 일반 사용자 프로세스는 대용량 저장장치(Mass Storage) 클래스 기기의 원시 USB Bulk 엔드포인트(`0x01 OUT`, `0x81 IN`)를 독점 클레임하거나 커널 드라이버를 분리(detach)할 수 없습니다. `sudo` 권한으로 실행하면 PyUSB/libusb가 커널 드라이버를 우회하여 JieLi UBOOT과 직접 통신하고 안전하게 펌웨어를 복구합니다.

---

### 3.2 원클릭 UBOOT 복구 도구 사용법 (`tools/recover_fm1.py`, `tools/recover_smk37.py`)

기기가 부트 루프나 치명적 오류로 인해 mask-ROM UBOOT 상태(`WL80UBOOT1.00` 또는 `WL82 UBOOT1.00`)로 떨어졌을 때, 터미널에서 기기 모델에 맞는 명령을 한 줄만 실행합니다:

```sh
# FM-1 복구:
sudo .venv-ota/bin/python tools/recover_fm1.py

# SMK-37 Pro 복구:
sudo .venv-ota/bin/python tools/recover_smk37.py

# 또는 범용 도구 사용:
sudo .venv-ota/bin/python tools/recover_uboot.py --board fm1
sudo .venv-ota/bin/python tools/recover_uboot.py --board smk37
```

#### 이 스크립트가 자동으로 수행하는 작업:
1. **타깃별 패키지 자동 추출:** `tools/make_app_area.py`를 실행하여 `.fwsc` 패키지에서 정확한 앱 슬롯 바이너리를 추출합니다:
   - **FM-1:** `0x04000` ~ `0x093000` (585,728 B, `0x8F000`)
   - **SMK-37 Pro:** `0x04000` ~ `0x09D000` (626,688 B, `0x99000`)
   - *SPL/헤드(`0x00000..0x03FFF`) 및 캘리브레이션/키 영역(`0xC0000..`)은 절대 건드리지 않습니다.*
2. **JieLi UBOOT 핸드셰이크 & RAM 로더 주입:** USB로 연결된 `0x4C4A:0x8057` 장치를 감지하고, 공식 WL82 RAM 로더(`wl82loader.bin`)를 기기의 volatile RAM(`0x1C02000`)에 업로드한 뒤 실행합니다.
3. **플래시 메모리 검증 & 앱 슬롯 재작성:** 온칩 플래시 ID(FM-1: `0x856014`, SMK-37 Pro: `0xEB6014` 또는 `0x856014`)를 확인하고, 앱 영역만 섹터 단위로 소거한 뒤 새 바이너리를 프로그래밍합니다.
4. **100% 바이트 대조 검증:** 기록된 전체 앱 영역을 플래시로부터 다시 읽어와 빌드 결과물의 SHA-256 해시와 1:1 바이트 단위로 대조 검증합니다.
5. **기기 자동 리셋:** 칩에 `RUN_APP` 리셋 명령을 보내 정상적인 picovave 펌웨어로 즉시 부팅시킵니다. (부팅 완료 후 기존처럼 일반 USB-MIDI로 사용 및 업데이트 가능)

---

### 3.3 macOS에서 복구하기 (USB-MIDI가 살아있는 경우)

```sh
python3 tools/ota_probe.py --list                        # 1. 무슨 상태인가 (포트 이름 = 아이덴티티)
python3 tools/ota_probe.py --ask --secs 3                # 2. 아이덴티티 질의 (SMK-37 Pro_700 / ota-SMK-37 Pro_016)
python3 tools/ota_probe.py --flash build/picovave.fwsc   # 3. 패키지 설치 = 복구 (필요하면 --yes)
```

`--flash`는 `platform/tools/fm1_install.py`를 그대로 실행한다(직접 써도 같다): 패키지를 실어 주고 →
로더가 flash를 쓰고 → 재부팅을 기다려 → 아이덴티티를 확인한다. 그래서 **중단된 설치도 같은 명령으로
끝난다**(로더가 남아 있으면 쓰기 단계만 한다). 순정 복귀는 순정 v016 패키지를 같은 방법으로 설치한다.

**USB 이름은 도중에 바뀐다** — 앱과 업데이트 로더는 서로 다른 USB 디스크립터를 쓴다:

| 상태 | USB/MIDI 이름 | 아이덴티티 | macOS 복구 |
|---|---|---|---|
| picovave 앱 | `picovave` | `SMK-37 Pro_700` | `--flash` ✓ |
| picovave 업데이트 로더 | `picovave Update` | `ota-SMK-37 Pro_700` | `--flash` ✓ (쓰기만) |
| **Felucca 계열 로더**(참조 펌웨어/SLOOP) | **`Felucca Update`** | `ota-SMK-37 Pro_9xx` | `--flash` ✓ (쓰기만) |
| 순정 M-Vave 로더 | `Felucca Update` | `ota-SMK-37 Pro_016` | `--flash --resume-loader "ota-SMK-37 Pro_016"` |

- 호스트는 세 가지 이름을 모두 받는다(`PORT_RE`/`ota_probe.py`의 `MATCH`에 `felucca` 포함) ✓, 그래서
  **`--port` 없이** 실행하는 편이 안전하다: 앱 이름으로 시작해 로더 이름으로 다시 찾아간다. `--port`로
  한쪽 이름만 지정하면 나머지 절반을 못 찾을 수 있다(그 경우 두 이름을 알려주는 힌트를 출력한다).
- `Felucca Update`로 보인다는 것은 **다른 펌웨어 계열의 로더**에 있다는 뜻이다(Felucca/SLOOP 참조
  펌웨어, 또는 순정). 같은 계열 로더 `_9xx`는 그대로 이어받고, 순정 `_016`은 레이아웃이 다를 수 있어
  기본적으로 거부한다(exit 6) → 그 로더를 스테이징한 패키지를 그대로 쓴다는 전제로
  `--resume-loader "ota-SMK-37 Pro_016"` (로더가 바이트마다 검증하므로 실패하면 쓰이지 않고 로더에 남는다).
- mido 없는 python3로 실행해도 된다: 두 스크립트 모두 `tools/mido_boot.py`로 mido가 있는
  인터프리터(`.venv`/`.venv-ota`/`$PICOVAVE_PY`)를 찾아 스스로 재실행한다. 없으면:
  `python3 -m venv .venv-ota && .venv-ota/bin/pip install mido python-rtmidi`
- **웹 FLASH(USB)** 도 같은 일을 한다(브라우저 Web MIDI). 의존성이 없어 가장 간단하다:
  `python3 -m http.server -d build/sim 8738` → http://localhost:8738 (로컬은 secure context다).

### 3.3 실기 없이 이 경로를 확인하기

`tools/fake_device.py`가 ota.c/ldr_core.c의 기기 쪽을 대신한다(패키지와 바이트 단위로 대조). 앱↔로더
**재열거까지 흉내낸다**(포트 이름이 `picovave` → `picovave Update` → `picovave`로 바뀐다):

```sh
python3 tools/fake_device.py --test build/picovave.fwsc                    # 한 프로세스, MIDI 없이
python3 tools/fake_device.py build/picovave.fwsc --secs 300                # 가짜 기기를 CoreMIDI 포트로
python3 platform/tools/fm1_install.py build/picovave.fwsc --yes            # 다른 터미널 (--port 없이)

# Felucca 계열 로더로 보이는 상태(참조 _900 / 순정 _016):
python3 tools/fake_device.py --test build/picovave.fwsc --loader-name "Felucca Update" --loader-id "ota-SMK-37 Pro_900"
python3 tools/fake_device.py --test build/picovave.fwsc --phase loader --loader-name "Felucca Update" \
        --loader-id "ota-SMK-37 Pro_016" --resume-loader "ota-SMK-37 Pro_016"
```

`tools/verify_final.py`가 이 중 셋(자기 펌웨어 전체 업데이트, `Felucca Update` 로더, 다른 로더 거부→
`--resume-loader`로 완료)을 매번 돌린다. (가짜 기기는 한 번에 하나만: 같은 이름의 포트가 이미 있으면
거부한다.)

---

## 4. 먼저 전체 flash 백업 (필수)

```sh
python3 jluboottool.py --device <dev> --chip wl82 "read 0 0x100000 smk37-dump-1.bin"
python3 jluboottool.py --device <dev> --chip wl82 "read 0 0x100000 smk37-dump-2.bin"
```

접속 시 flash id가 출력된다(정상 예: `0xeb6014`). 검사:

- 두 덤프 모두 정확히 **1048576 바이트**,
- 두 덤프의 **SHA-256이 같음**,
- 오프셋 **`0xC1224`에 7글자 `trimdat`**.

하나라도 어긋나면 덤프를 신뢰하지 말고 다시 읽는다. 덤프는 **최소 두 곳**에 보관한다.

---

## 5. app 영역 파일 만들기

```sh
python3 tools/make_app_area.py build/picovave.fwsc
# -> build/picovave.app-area.bin  (or -o build/picovave/picovave.app.bin)
```

출력된 `sha256`을 적어 둔다(현재 `picovave.fwsc` 기준 `040374e5…`). 이 파일이 `0x4000`에 들어간다.

---

## 6. 쓰고 검증

```sh
python3 jluboottool.py --device <dev> --chip wl82 "erase 0x4000 0x99000"
python3 jluboottool.py --device <dev> --chip wl82 "write 0x4000 build/picovave.app-area.bin"
python3 jluboottool.py --device <dev> --chip wl82 "read 0x4000 0x99000 check.bin"
```

`check.bin`이 `build/picovave.app-area.bin`과 **바이트 단위로 같아야** 한다:

```sh
shasum -a 256 build/picovave.app-area.bin check.bin   # 같은 해시
```

> 쓰기 전에 `<dev>`가 `WL82 UBOOT`인지, 백업이 끝났는지 다시 확인하라.

전원을 껐다 켠다. 이제 새 picovave가 USB-MIDI(포트 `picovave`)로 enumerate하고, SLOOP 스토어에
있는 카트(PICO-8 게임)를 실행하며, 이후로는 **웹 FLASH(USB)로 자유롭게 다시 쓸 수 있다**
(페이지의 카트 컨트롤도 같은 두 갈래다 — **SIMULATOR**는 웹 wasm 저장소, **FLASH(USB)**는 USB-MIDI
SysEx로 실기 SLOOP 스토어에 `.p8.png` 카트를 쓴다. 텍스트 `.p8`은 받지 않는다. CLI
`tools/fm1_install.py`에는 아직 카트 명령이 없다 — 실기 카트는 페이지의 FLASH(USB)로 넣는다).

> 기기 풀(Lua 힙 414,672 B, 엔진이 카트가 도는 동안 ~74 KB 사용)을 넘겨 **카트가 로드조차 안 되면**
> 기기는 그것을 거부하고 **기본 카트로 폴백**한다 — 패널이 멈추지 않는다(롤백 사다리: 최신 슬롯 →
> 그것이 대체한 슬롯 → 내장 기본 카트). 아레나 끝에서 겨우 도는 카트는 폴백하지 않고 대신 가끔
> GC 히치가 생긴다. 페이지는 업로드 전에 **기기와 같은 할당자·같은 아레나**로 그 카트를 실제로 돌려
> 실측하고(`status`/`refused`) 경고하며, 아예 안 돌거나 눈에 띄게 끊기면 한 번 더 확인을 받는다.
> 못 도는 카트는 웹 시뮬레이터에서는 그대로 잘 돌아간다(원인·당시 측정: `HANDOVER.md` §7;
> 지금의 아레나와 실측: §13).

---

## 7. 절대 규칙

- **`0x00000-0x03FFF`, `0xC0000-0xC1FFF`, `0xFF000-0xFFFFF`를 쓰지 않는다.** 오직 자기 덤프에서,
  그것도 손상됐을 때만 복원한다.
- **4 KiB 섹터 단위**로 작업한다. 지우고 → 쓰고 → 다시 읽어 비교한다.
- 순정으로 되돌리려면: 순정 v016 패키지를 정상 설치(M-UPGRADE)하거나, Method A 덤프가 있으면
  UBOOT에서 `0x4000-0x9CFFF`를 그 덤프에서 쓴다. 백업이 없으면 UBOOT에서 순정 app을 만들 수
  없으므로 **설치가 가능한 상태에서** 순정 패키지를 설치하는 편이 낫다.
- VM/BTIF/USRTRIM/USRFLASH/USR/key_mac은 어떤 경우에도 건드리지 않는다(정상 포트는 절대 쓰지 않음).

---

## 참고

- `picovave/platform/firmware/hal/fm1_flash_map.h` — SMK-37 Pro flash 맵과 컴파일타임 보호 검사.
- `tools/make_app_area.py` — app 영역 추출(이 문서 5단계).
- `tools/ota_probe.py` — 수동 MIDI 프로브(핸드셰이크 / M-UPGRADE / soft key / 수신 감시).
- genkigenki/fm1-to-smk37 `docs/02-update-and-recovery.md` — 업데이트 프로토콜, `.fwsc`,
  백업, 복구, 순정 복귀(위 절차의 1차 출처).
- kagaimiq/jl-uboot-tool — `read`/`write`/`erase` 셸과 WL82 로더.
