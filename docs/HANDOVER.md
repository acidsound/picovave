# 핸드오버 — picovave (SMK-37 Pro PICO-8 펌웨어) 현재 상태

작성 2026-10-17. 이 문서는 **다음 사람(또는 다음 세션)이 이어받기 위한 것**이다. 소스·툴·시뮬레이터는
완성되어 있고, **실기에서만 나는 크래시 하나**가 남았다. 그 크래시에 대해 확정된 사실과 아직 아닌 것을
명확히 구분해 적었다 — 추측을 사실처럼 읽지 말 것.

---

## 0. 한눈에

| | 상태 |
|---|---|
| 앱 계층 (PICO-8/femto8 이식) | 완성. 카트 `.p8.png` 업로드(SLOOP 저장 + USB SysEx) |
| wasm 시뮬레이터 / 호스트 프리뷰 / 기기 | 같은 엔진, 화면 **픽셀 단위 일치** (`tools/verify_final.py` 197검사 통과) |
| 펌웨어 부팅 | **실패** — 부팅 후 0.5~11초 사이 `EMU 0008`(스택 창 밖 접근)으로 죽고 부트가드가 RESCUE로 떨어짐 |
| 원인 규명 | 약 90%. "앱의 SP가 야생값이 된다"까지 실측으로 확정. **누가 SP를 망가뜨리는지**가 남은 질문 |
| 실기 사용 가능 여부 | **불가**. 한 번도 정상 동작한 적 없음(카트를 넣어본 적조차 없음) |

마지막 커밋(`78119fd`)에는 **빌드되지 않은 반쯤 적용된 계측기 패치**가 들어 있다(§8). 그 패치를
빌드·검증·플래싱하는 것이 다음 첫 작업이다.

---

## 1. 대상과 트리 구조

- 대상: **M-Vave SMK-37 Pro** — JieLi **AC791N/WL82**, pi32v2, XIP 실행(코드는 flash 0x02000000 대역).
- 이 트리는 상위 `hello` 트리(같은 플랫폼 HAL·USB/OTA·복구·메모리맵)를 복제해 이름만 바꾼 뒤
  앱 계층을 PICO-8로 갈아끼운 것이다. 상위 `hello` 트리 문서: [`../../README.md`](../../README.md),
  [`../README.md`](../README.md)(이 트리), [`REVERSE_ENGINEERING.md`](REVERSE_ENGINEERING.md).
- 트리:

```
picovave/
  src/                  앱 계층 (picovave.c=앱 본체, picovave_gfx.h=패널/크래시 화면, picovave_p8.h=프레임 루프)
  femto8/               벤더링한 PICO-8 엔진 (p8_lua.c, p8_emu.c ... lodepng)
  platform/             AC79 SDK 쪽 — 링커 스크립트, crt0.S, HAL(가드/IRQ/패널/USB/OTA)
  sim/ web/ host/       wasm 시뮬레이터, 브라우저 페이지, 호스트 프리뷰
  tools/                빌드·검증·플래싱 스크립트 (§3)
  carts/                예제 카트(.p8.png)
  docs/                 이 문서 + PICO8-PORT-PLAN / RECOVERY / REVERSE_ENGINEERING
  build/                산출물(추적 안 함)
```

---

## 2. 계측 수단은 오직 "크래시 화면"이다

기기에는 콘솔도 JTAG도 없다. 관측 수단은 **패널에 그려지는 크래시 화면 한 장**과 USB `--ask` 뿐이다.
따라서 모든 진단은 사진 한 장에 최대한 많이 담는 방향으로 설계되어 있다.

크래시 화면 각 줄의 의미(사진을 읽을 때 이 표를 쓸 것):

| 줄 | 내용 |
|---|---|
| `PC <hex>` | 크래시 프레임 f[16] = 인터럽트된 PC. **가해자가 아니라 피해자**다(항상 `setjmp` 프롤로그) |
| `V01 EMU <b> DBG <b>` | 가드가 잡은 원인 비트 (§4) |
| `SP <hex> <U\|S\|?> <OK\|BELOW\|ABOVE>` | f[24] = **예외 핸들러 자신의** SP, 그리고 그 주소가 어느 스택 창에 있는지 |
| `USP <a> SSP <b>` | f[22]/f[23] **원값**. 이 실리콘에서는 스택 포인터가 아니다(§5) — 참고용 |
| `DEEP <hex> <U\|S\|?>` | 10 kHz 타이머 ISR이 매 틱 표본한 **활성 스택 최저 주소**(`pv_sp_min`) |
| `N<n> EARLY<0\|1> UP<t>MS` | 연속 크래시 횟수 / 부팅 3초 내 크래시 연속 여부 / 업텀 |
| `RET <a> ETM <b>` | 인터럽트된 복귀 주소 + 하드웨어 트레이스 0 |
| `ETM <c>` | 하드웨어 트레이스 1 |
| `BUILD <tag>` | **이 화면을 그린 빌드**. 기기에 무엇이 올라가 있는지 증명하는 유일한 근거 |

- 부팅 시 직전 크래시 기록을 10초간 같은 화면으로 다시 보여준다(`PV_REPORT_MS`, `pv_crash_report()`).
  기록은 `.noinit`이라 리셋을 넘어 살아남는다.
- `pv_early_mark()`는 "BOOT MARK: THIS BUILD RAN" 화면을 부팅 초반에 한 번 띄운다 — 검은 패널과
  "빌드가 실행조차 못 됨"을 구분하기 위한 것.
- 업텀 단위는 TIMER4/24000을 ms로 표기한 값이다(부팅 이후 절대값은 확신 없음, **비율은 유효**).

---

## 3. 빌드 · 검증 · 플래싱

```sh
# 빌드 (한 번에 하나만 — 동시 빌드는 ld가 멈춘다)
AC79_SDK=/Users/spectrum/fw-AC79_AIoT_SDK python3 tools/build_picovave.py
#   -> "tag <8hex>" 를 출력. 산출물 build/picovave.fwsc
#   -> 플래싱·증거용으로 build/picovave-<tag>.fwsc 복사본을 남겨둘 것

# 검증 (197검사, 인자 없음)
python3 tools/verify_final.py > /tmp/suite.log 2>&1      # 반드시 로그로 리다이렉트(SIGPIPE)
#   macOS에는 timeout 명령이 없다. 로그 마지막 줄 "all checks passed" + 종료코드 0 확인.
#   AC79_SDK가 없으면 읽기 전용 packer 교차검증 한 건이 "(skipped: ...)"로 표시된다(정상).

# 플래싱
python3 tools/ota_probe.py --flash build/picovave-<tag>.fwsc
python3 tools/ota_probe.py --ask        # 기기에 올라가 있는 태그 확인
# 그 밖의 플래그: --list(포트 목록) --port --upgrade --uboot --watch --yes

# 호스트 프리뷰(크래시 화면 렌더 확인 — 플래싱 없이 레이아웃을 검사할 수 있다)
python3 -c "import sys;sys.path.insert(0,'tools');import preview_picovave;preview_picovave.build()"
./build/picovave/preview --crash /tmp/crash.ppm
./build/picovave/preview --crash-boot /tmp/crash-boot.ppm
```

작업 규칙(위반하면 조용히 잘못된 결과가 나온다):

1. **동시 빌드 금지.** `build/picovave/obj`를 두 빌드가 동시에 쓰면 `ld`가 멈춘다.
   `job_kill` 후에는 `docker ps`로 고아 컨테이너를 확인해 정확한 컨테이너 id로 죽인다.
2. **빌드가 읽는 중인 소스를 편집하지 말 것.** 태그는 소스 해시다. `tools/sources.py`의
   `staleness()`로 "패키지보다 새로운 입력"을 확인할 수 있다(0이어야 정상).
3. 플래싱 금지 구역: flash `0x0–0x3FFF`, `0xC0000–0xC1FFF`, `0xFF000–0xFFFFF`.
   RESCUE는 USB에 응답하므로 **벽돌이 아니다**. `_9xx` 로더는 `--resume-loader` 없이 통과한다.
4. wasm↔호스트 픽셀 패리티 단계는 수 분 걸린다. node 프로세스가 **CPU 0%로 멈춰 있으면**
   그건 검사 자체가 아니라 남은 좀비 프로세스다(`ps -o %cpu`로 확인).

---

## 4. 기기 메모리맵과 스택 가드 (확정)

링커(`platform/firmware/app.ld:31-32`)와 가드(`platform/firmware/hal/fm1_guard.h:6-18`):

```
POOL          0x01C2C000 + 0x48000     Lua/엔진 힙(여기서 m_memory 64 KB를 malloc)
_guard0       0x01C74000               쓰기 감시 밴드
_ustack_lo    0x01C74100  ┐
_ustack_top   0x01C7A000  ┘  사용자 스택 24,320 B   (crt0 sp = _ustack_init = 0x01C79C00)
_guard1       0x01C7A000               쓰기 감시 밴드
_sstack_lo    0x01C7A100  ┐
_sstack_top   0x01C7C000  ┘  슈퍼바이저 스택 7,936 B (crt0 ssp = _sstack_init = 0x01C7BF00)
NOINIT        0x01C7C000 + 0x3D50      리셋을 넘어 살아남는 기록(fm1_crash = 0x01C7C000 포함)
```

가드 비트:

- **EMU bit 3 (0x8)** — SP(USP/SSP 양쪽)가 창 `[_ustack_lo, _sstack_top]` **밖**을 가리킴.
  두 스택에 하나의 창을 쓴다. → `EMU 0008`은 "스택 창 밖 접근"이다.
- **DBG bit 13** — 가드 **밴드**(`[_guard0,_ustack_lo-1]`, `[_guard1,_sstack_lo-1]`)에 **쓰기**.
- **DBG bit 12** — PC가 허용 범위 밖.
- 관측된 크래시는 항상 `EMU 0008 DBG 0000` = **스택 창 밖 접근, 밴드 쓰기 아님, PC는 정상**.

ABI 주의: pi32v2 코드는 **양의 sp 오프셋**에 저장한다(프레임 내부에서 `[sp+1056]`까지 관측).

---

## 5. 크래시 프레임 실측 (여기서 한 번 크게 틀렸다)

`platform/firmware/hal/fm1_irq.h`의 `fm1_crash_t` 주석은 프레임을 `f[16] reti, f[22] usp, f[23] ssp,
f[24] sp`로 적고 있지만, **f[22]/f[23]은 이 실리콘에서 스택 포인터가 아니다.** 실측:

| 슬롯 | 관측값 | 판정 |
|---|---|---|
| f[16] | `setjmp+0x12` 등 실제 코드 주소 | **인터럽트된 PC — 신뢰 가능** |
| f[24] | `0x01C7BEFC`(= `_sstack_init` − 4), 이전 빌드 `0x01C7BFFC`(= `_sstack_top` − 4) | **예외 핸들러 자신의 SP** (핸들러는 `ssp`로 진입) |
| f[23] | `0x01C7BF00` = crt0가 넣은 `_sstack_init` 그대로 | 슈퍼바이저 스택 **기저값**으로 보임 → 앱은 슈퍼바이저 스택을 쓰지 않는다 |
| f[22] | `jmpbuf_restart + 0x40`(현재 빌드) / `+0x30`(f82ab110) | **jmp_buf 안을 가리키는 데이터 포인터** — `setjmp`가 넘겨받은 인자/스필된 레지스터로 보인다. usp 아님 |

⇒ **앱의 실제 SP는 프레임에서 읽을 수 없다.** 그래서 ISR 표본 계측기(`DEEP`)를 따로 만들었다.

현재 빌드(36251743) 심볼: `jmpbuf_restart = 0x01C0EC2C`, `m_memory = 0x01C0C320`,
`m_cart_memory = 0x01C0C324`, `fm1_crash = 0x01C7C000`.

---

## 6. 고친 것 (검증됨)

**(a) crt0가 스택 창의 꼭대기에서 시작하던 버그 — 확실히 고쳐짐**

`7bc1d0e1`은 **1,196틱** 만에 죽었고 f[24]가 `_sstack_top − 4`였다: 첫 프레임의 push가 창 밖으로
나가는 구조였다. `app.ld:41-42`에서 `_ustack_init = _ustack_top − 0x400`,
`_sstack_init = _sstack_top − 0x100`로 여유를 두자 **11,210틱**까지(9.4배) 살았다.

**(b) 엔진 카트 메모리 범위 검사 — 실제 결함이지만 이번 크래시의 원인은 아님**

`femto8/src/p8_lua.c`의 `memset/memcpy/poke/poke2/poke4/peek/reload/cstore`가 `m_memory`
(= `malloc(MEMORY_SIZE)`, 64 KB) 범위를 검사하지 않아, 카트가 범위 밖 주소를 넘기면 `.bss`를 훑어
`jmpbuf_restart`까지 밀어낼 수 있었다. `mem_ok()` 가드와 클램프를 넣었다.

**단, 이번 크래시의 원인이 아니다.** 사용자가 카트를 넣은 적이 없고, 기본 카트는
`tools/pvcart.py`의 버튼/`pset` 테스트(메모리 함수 호출 없음)다. 이 수정을 크래시 원인으로 주장하면 안 된다.

---

## 7. 미해결 크래시 — 증거와 현재 가설

### 증상

부팅 후 **비결정적인 시점**(1,196 / 11,210 / 1,147틱)에 `EMU 0008 DBG 0000`으로 죽고,
부트가드가 RESCUE로 떨어진다. `PC`는 매번 `setjmp` 프롤로그의 `[sp+32]` 저장 지점 — 즉 **피해자**다.

### 기기에서 받은 사진(원본 증거)

| 태그 | PC | EMU/DBG | SP 관련 | 업텀 | 비고 |
|---|---|---|---|---|---|
| `7bc1d0e1` | — | — | f[24] = `_sstack_top − 4` | 1,196 | 창 꼭대기 시작 버그 → (a)로 수정 |
| `f82ab110` | `0201EDC8` | `0008 / 0000` | SP `01C0EC3C BELOW`, USTACK 1F10/5F00 | 11,210 | (a) 수정 후 9.4배 생존 |
| `36251743` | `0201EFF6` | `0008 / 0000` | SP `01C7BEFC S OK`, USP `01C0EC6C`, SSP `01C7BF00`, **DEEP `5731C1F4 ?`** | 1,147 | 계측기 투입 |

(`eae03ad8`도 빌드·전달했지만 그 태그의 사진은 없다.)

### 36251743이 알려준 결정적 사실

`DEEP 5731C1F4` — **`0x5731C1F4`는 RAM 주소가 아니다**(RAM은 `0x01C00000–0x01C7FFFF`).
`DEEP`은 ISR이 매 틱 `&지역변수`로 표본한 최저 주소이므로, **어느 순간 앱의 SP가 야생값이었다**는
뜻이다. 같은 사진에서:

- `SSP 01C7BF00` = crt0 값 그대로 → 앱은 슈퍼바이저 스택을 쓰지 않는다.
- `SP 01C7BEFC S OK` = 핸들러 자신의 스택(정상).
- 사용자 스택 페인트 스캔은 8 KB 남짓만 사용 → **SP가 야생값이 되기까지는 정상적으로 돌고 있었다.**

### 현재 가설

**SP를 소프트웨어가 쓰는 곳은 `setjmp`/`longjmp` 뿐이다.**

- `src/picovave_p8.h:102` — 앱 프레임 루프가 **매 프레임** `setjmp(jmpbuf_restart)`
- `femto8/src/p8_emu.c:401` — `p8_run()`의 `longjmp`
- `femto8/src/p8_emu.c:1107` — `p8_abort()`의 `longjmp`
- (`femto8/src/p8_emu.c:409` — 엔진 쪽 `setjmp`)

⇒ **`longjmp`가 손상된 `jmp_buf`를 복원해 SP가 야생값이 되었고, 그 다음 `setjmp`의 프롤로그 저장이
창 밖 주소로 나가 가드가 잡았다**는 것이 가장 그럴듯한 설명이다. 이는 사진 하나로 모든 값이
설명된다(피해자 PC가 setjmp, EMU bit 3, 야생 SP, 짧은 생존).

**아직 모르는 것**: `jmpbuf_restart`를 **누가 언제** 손상시키는가. 그리고 크래시 시점이
1,147 / 11,210틱으로 흔들리는 이유(경합 또는 힙 레이아웃 의존).

### 시도했지만 원인이 아니었던 것 (다시 파지 말 것)

- `LUAI_MAXCCALLS` 깊이 제한 — 아님.
- 카트 메모리 범위 초과(§6b) — 카트를 넣은 적이 없으므로 아님.
- 초기 `SP ... ABOVE`/`BELOW` 판정 — 당시 SP 계산이 핸들러 스택을 보고 있었다(§5에서 수정).

---

## 8. 소스에 들어가 있지만 **빌드되지 않은** 작업 (다음 첫 작업)

"첫 야생 표본의 인터럽트 PC"를 잡는 계측기까지 소스에 반영했으나 **빌드가 중단되었다.**
따라서 `build/picovave.fwsc`(태그 `36251743`)는 이 패치 **이전** 소스로 만든 것이고,
현재 트리는 stale 입력 3개(`fm1_isr.S`, `picovave.c`, `picovave_gfx.h`) 상태다.

반영된 내용:

1. `platform/firmware/hal/fm1_isr.S` — 스텁이 `call \body` 직전에 `r0 = sp`로 저장 프레임
   (`psr/rets/reti`)의 주소를 C 본문에 넘긴다.
2. `src/picovave.c` — `fm1_timer5_irq(uint32_t isr_sp)`가 **두 스택 어디에도 속하지 않는 첫 표본**을
   만나면 `pv_sp_bad`(그 SP)와 `pv_sp_bad_pc`(프레임에서 코드 주소로 보이는 워드 = 인터럽트된 PC)를
   `.noinit`에 남긴다(`pv_stack_ok()` / `pv_stack_of()` 사용).
3. `src/picovave_gfx.h` — 크래시 화면 11번째 밴드 `WILD <sp> <pc>` (y=222).
4. `tools/verify_final.py` — 밴드 기하 검사를 11줄로 갱신(밴드 y: 28/50/76/94/112/130/148/166/184/204/222).

**다음 단계**: 빌드 → `verify_final.py` 통과 확인 → `build/picovave-<새 태그>.fwsc` 복사 → 플래싱 →
새 사진의 `WILD` 줄을 읽는다. 그 PC가 **SP를 망가뜨린 코드 위치**를 지목한다.

---

## 9. 그 다음 우선순위

1. **`WILD` PC 확보** (§8) — 범인 지목. 이것 없이는 나머지가 추측이다.
2. 확보한 PC 주변을 `longjmp` 경로(`p8_emu.c`의 `p8_run`/`p8_abort` 호출자, `p8_lua.c`의 에러 경로)와
   대조. 카트 없는 기본 실행에서 프레임마다 `setjmp`/`longjmp`가 도는 경로가 있는지 확인.
3. 스택 창을 **넓히는 것은 원인 대책이 아니다**(증상 완화). 창은 POOL 아래/NOINIT 위로 이미 꽉 차 있다.
4. 여유가 되면 USB로 크래시 레코드를 내려받는 덤프를 추가 — 사진 판독을 없앨 수 있다.
5. 회귀 테스트: 기본 카트로 호스트 스택 한계를 낮춰(예: `ulimit -s`) 재현되는지 시도.

### 큰 함정 하나 (반드시 기억할 것)

**호스트에서는 이 버그가 재현되지 않는다.** 호스트 스택은 8 MB이고 스택 가드가 없어서, 같은
재귀/오염이 무해하다. 세션 중 호스트에서 돌린 카트 5종이 모두 깨끗했던 이유가 이것이고,
이 때문에 나는 한 번 잘못된 결론(카트 데이터 오염)으로 기울었다. **호스트 초록불은 기기 정상의
증거가 아니다.**

---

## 10. 이번 작업에서 건드린 파일

| 파일 | 내용 |
|---|---|
| `platform/firmware/app.ld` | `_ustack_init`/`_sstack_init` 여유(41-42), 스택/가드 상수(31-32) |
| `platform/firmware/crt0.S` | 위 초기값 사용 |
| `platform/firmware/hal/fm1_isr.S` | ISR 스텁이 sp를 C 본문에 전달(WIP) |
| `platform/firmware/hal/fm1_guard.h` | 가드 창/비트 정의와 설명 |
| `platform/firmware/hal/fm1_irq.h` | `fm1_crash_t`(usp/ssp 포함) + 프레임 주석(§5 주의) |
| `src/picovave.c` | 스택 페인트/스캔(`pv_stack_paint`/`pv_stack_depth`), `pv_app_sp`, `pv_stack_of`/`pv_stack_ok`, `pv_crash_report`, 부트 마크 `pv_early_mark`, ISR 계측기(`pv_sp_min`, WIP `pv_sp_bad`) |
| `src/picovave_gfx.h` | 크래시 화면 밴드(11줄, WIP `WILD` 포함) |
| `src/picovave_p8.h` | 프레임 루프의 프레임당 `setjmp`(102) |
| `femto8/src/p8_lua.c` | 카트 메모리 범위 가드(`mem_ok`) |
| `femto8/src/p8_emu.c` | `longjmp`/`setjmp` 지점(401/409/1107) — 조사 대상 |
| `tools/verify_final.py` | 197검사, 크래시 화면 밴드 기하 검사, 엔진 범위 검사 2건 |
| `tools/build_picovave.py` | 빌드 + 기본 카트 굽기 + 태그 출력 |
| `tools/ota_probe.py` | 플래싱/`--ask`/`--list` |
| `tools/preview_picovave.py` | 호스트 프리뷰 빌드(크래시 화면 렌더) |
| `tools/pvcart.py` | 기본 카트(`DEFAULT_SCRIPT`) |
| `README.md` | 검사 수 197 갱신 |

주의: `build/picovave/cart_default.h`와 `picovave/cdata/`는 빌드 시 생성되는 산출물이라 추적하지 않는다
(상위 `.gitignore`).

---

## 11. 다음 사람에게 필요한 것 한 가지

플래싱은 비싸다(사용자가 매번 손으로 한다). 새 빌드를 드릴 때는 **한 장의 사진으로 최대한 많이
읽히도록** 만들 것. 그리고 새 사진에서 필요한 것은 `BUILD`(무엇이 올라갔는지), `PC`, `EMU/DBG`,
`SP`, `DEEP`, `WILD` — 이 여섯 줄이다.
