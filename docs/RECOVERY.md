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

- **Linux(루트) 또는 Windows(관리자)** 호스트 — `jl-uboot-tool`은 raw SCSI로 말한다.
  **macOS는 지원하지 않는다**(도구가 `darwin`에서 즉시 실패한다). 라즈베리파이도 된다.
  Docker Desktop의 macOS VM은 USB 패스스루가 안 되므로 대안이 못 된다.
- USB **데이터** 케이블(충전 전용 X), 허브 없이 직결.
- [kagaimiq/jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool) (`pip install -r requirements.txt`).
- 복구할 패키지: `build/picovave.fwsc` (또는 순정 v016).
- 기기 전체 flash **백업을 보관할 곳**(아래 4단계). `0xC0000-0xC1FFF`(BT/USRTRIM)와
  `0xFF000-0xFFFFF`(key_mac)는 **그 기기에만 있는 값**이라 다시 받을 수 없다.

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

### `PICOVAVE CRASH` 화면 읽기

앱이 죽으면(가드/CPU 예외) 폴트 핸들러가 화면을 그리고 3초 뒤 리셋한다. 리셋이 반복되면 부트
실패 2회로 RESCUE에 들어가므로, **크래시 화면은 지나가는 화면이 아니라 "왜 죽었는지"를 읽을 기회**다.
`fm1_fault()`가 남기는 기록(`fm1_crash`, `.noinit`)을 8줄로 그린다:

| 줄 | 뜻 |
|---|---|
| `PC xxxxxxxx` | 폴트를 일으킨 명령의 주소(XIP는 flash offset + `0x02000120`) |
| `Vxx EMU xxxx DBG xxxx` | 어느 벡터인가 / EMU 상태(`bit3` = **sp가 스택 창 밖** — 현재 빌드는 이 창을 쓰지 않으므로 보통 0) / 버스·쓰기 트랩 비트 |
| `SP xxxxxxxx OK\|BELOW\|ABOVE` | 폴트 시점의 **핸들러 자신의** 스택 포인터와 판정(앱의 sp는 프레임에서 읽히지 않는다 — `DEEP` 참고) |
| `STACK xxxx/5F00` | **이번 실행이 실제로 쓴 스택 최대치** / 전체(24,320 B). 부팅 때 스택을 패턴으로 칠하고 훑어서 얻는다 |
| `Nx EARLYx UPxxxxxxMS` | 누적 크래시 횟수 / 부팅 3초 내 연속 크래시 횟수 / 죽기까지 살아 있던 시간 |
| `RET xxxxxxxx` | 폴트 시점의 리턴 주소 |
| `ETM xxxxxxxx`, `ETM xxxxxxxx` | ETM 트레이스(하드웨어 백트레이스) 최근 2개 |

같은 값이 **정상 부팅의 빵가루 화면**(`PICOVAVE BOOT`)에도 한 줄로 나온다: `STACK 0xxx/5F00`은
**직전 실행**이 쓴 최대치다(스택을 칠해 두고 다음 부팅에서 먼저 훑기 때문에, 크래시 없이도 여유를
볼 수 있다). 즉 크래시가 나면 `SP ... BELOW` + 큰 `STACK`이면 "카트가 너무 깊이 재귀",
`DEEP`가 `U` 창 밖(`?`)이면 "sp가 창 밖으로 갔다"로 갈린다(ISR이 매 틱 표본한 최저 주소).

화면 자체는 `hg_crash_screen()`(`src/picovave_gfx.h`)이 그리고, ROM과 같은 함수를 호스트
프리뷰가 스크립트 값으로 렌더한다(`build/picovave/preview --crash out.ppm`) — 실기 없이 레이아웃을
볼 수 있고 `tools/verify_final.py`가 8줄이 패널 안에 들어가는지 검사한다.

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
| 부팅 화면 `STACK n/24320` | 직전 실행의 스택 최대 사용량(스택을 칠해 두고 다음 부팅에 훑는다) |

사각지대는 "밴드(256 B)를 한 번에 건너뛰는 단일 프레임" 하나뿐이다 — 그 경우만 밴드가 안 잡는다.
`LUAI_MAXCCALLS=48` 덕분에 엔진 경로에서는 그런 프레임이 나오지 않는다.

주의: `LUAI_MAXCCALLS=48`은 **크래시의 원인을 제거한 것이 아니라** 한도를 스택에 맞춘 것이다(그리고
이건 여전히 필요하다 — 위 수정과 별개로 유효하다). 순수 Lua 재귀는 C 스택을 쓰지 않으므로
(루프로 처리된다) 영향받지 않는다.

### 어느 빌드가 올라가 있는지 (패널을 믿지 말 것)

**LCD는 마지막으로 그려진 프레임을 그대로 들고 있다.** 새 앱이 패널을 켜기 전에 죽으면
화면은 **이전 빌드가 남긴 그림**이고, 그것을 보고 "플래싱이 안 됐다"고 판단하면 틀린다.
그래서 빌드는 **자기 이름을 USB로 말한다**: identity 응답(27바이트)의 NUL 패딩 뒤에
그 빌드의 태그가 붙는다(`ota.c`의 `PV_BUILD_TAG`, 빌드 입력 전체의 sha256 앞 8자리).
업데이터가 읽는 identity 문자열은 **한 바이트도 바뀌지 않는다**(태그는 종료 NUL 뒤에 있다).

```sh
python3 tools/ota_probe.py --ask
#   ...  <- identity: SMK-37 Pro_700  build e5e5d778
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
같은 8줄을 정지 화면으로 다시 그린다** — 헤드라인이 `LAST CRASH (BOOT)`이고 **10초** 유지된다
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

`jl-uboot-tool`은 **16바이트 벤더 CDB + RAM 로더 업로드**로 flash를 쓴다(표준 READ/WRITE(10)의 LBA
매핑이 아니다). 그래서 `dd`로 대신할 수 없고, 툴 자체도 `scsiio/__init__.py`에 `win32`/`linux`
백엔드만 있어 macOS에서는 `ImportError`로 즉시 죽는다. **즉 macOS에는 raw SCSI 경로가 없다.**

| 기기 상태 | macOS에서 |
|---|---|
| 앱이 돎 / 앱의 RESCUE(부팅 2회 실패) / 업데이트 로더(`ota-SMK-37 Pro_7xx`·`_016`) | **USB-MIDI로 완전 복구된다** — 3.2 |
| mask-ROM UBOOT(`WL82 UBOOT1.00`, MIDI 포트 없음) | **쓸 수 없다** → Linux/Windows, Linux VM(USB 패스스루), 또는 백업. `--uboot`은 macOS에서 사실상 편도행이다 |

MIDI가 살아 있는 한 macOS만으로 끝난다는 점이 중요하다: 업데이트 로더는 **중단된 설치를 이어서
끝내고**, 앱이 죽어도 RESCUE가 다시 MIDI를 열며, RESCUE에서 또 실패해야 UBOOT으로 떨어진다.

### 3.2 macOS에서 복구하기 (MIDI)

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

> 기기 풀(287 KB, 엔진이 ~108 KB 사용)을 넘는 카트를 올리면 기기는 **그것을 거부하고 기본 카트로
> 폴백**한다 — 패널이 멈추지 않는다(롤백 사다리: 최신 슬롯 → 그것이 대체한 슬롯 → 내장 기본 카트).
> 페이지는 업로드 전에 기기와 같은 할당자로 **실측**해(`peak`/`refused`) 경고하고,
> `기기 불가`면 한 번 더 확인을 받는다. `기기 불가`로 뜬 카트는 웹 시뮬레이터에서만 돌아간다
> (원인·측정값: `HANDOVER.md` §7 "두 번째 결함").

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
