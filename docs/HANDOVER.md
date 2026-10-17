# 핸드오버 — picovave (SMK-37 Pro PICO-8 펌웨어) 현재 상태

작성 2026-10-17. 이 문서는 **다음 사람(또는 다음 세션)이 이어받기 위한 것**이다. 소스·툴·시뮬레이터는
완성되어 있고, **실기에서만 나는 크래시 하나**가 남았다. 그 크래시에 대해 확정된 사실과 아직 아닌 것을
명확히 구분해 적었다 — 추측을 사실처럼 읽지 말 것.

---

## 0. 한눈에

| | 상태 |
|---|---|
| 앱 계층 (PICO-8/femto8 이식) | 완성. 카트 `.p8.png` 업로드(SLOOP 저장 + USB SysEx) |
| wasm 시뮬레이터 / 호스트 프리뷰 / 기기 | 같은 엔진, 화면 **픽셀 단위 일치** (`tools/verify_final.py` 통과, "all checks passed") |
| 펌웨어 부팅 | **원인 확정·수정 완료.** 원인은 "야생 SP"가 아니라 **newlib `setjmp`의 sp 사용 vs 하드웨어 EMU 스택 창**이었다(§7) |
| 원인 규명 | **100%** — 디스어셈블리로 증명(§7). 피해자는 `setjmp`, 가해자도 `setjmp` |
| 실기 사용 가능 여부 | **확인 대기** — 태그 `cc27cc77` 패키지가 첫 "부팅 기대" 빌드다. 사진 한 장이면 확정된다(§11) |

build/`picovave.fwsc`(태그 `cc27cc77`)가 이 수정을 담은 패키지다. 이전 커밋(`78119fd`)의 반쯤 적용된
계측기 패치는 **빌드·검증 게이트 두 곳을 빨갛게 만든 상태로 커밋**되어 있었다 — 이번에 함께 해소했다(§8).

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

# 검증 (199검사, 인자 없음)
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
| f[22] | `jmpbuf_restart + 0x40`(현재 빌드) / `+0x30`(f82ab110) | **`setjmp`가 잠깐 `sp`에 넣는 값 그 자체.** `setjmp` 프롤로그가 `[r0+] = {r15-r0}` 뒤 `sp = r0`(= jmp_buf+64)를 하는데 `+0x40`이 정확히 그 중간값이다(§7). 야생값이 아니라 **`setjmp` 자신의 스크래치 sp**다 |

⇒ 이 표의 `+0x40`은 "누군가 SP를 망가뜨렸다"는 증거가 아니라 **원인 자체의 지문**이다(§7).

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

## 7. 크래시 원인 (확정) — newlib `setjmp`가 sp로 `jmp_buf`를 훑는다 vs EMU 스택 창

**"야생 SP" 가설은 틀렸다. 크래시는 SP 손상이 아니라 SP 창(sp window) 위반이라는 *가드의 정상 동작*이다.**
가해자는 `setjmp` 자신이고, 피해자도 `setjmp`이다.

### 기기에서 받은 사진(원본 증거)

| 태그 | PC | EMU/DBG | SP 관련 | 업텀 | 비고 |
|---|---|---|---|---|---|
| `7bc1d0e1` | — | — | f[24] = `_sstack_top − 4` | 1,196 | 창 꼭대기 시작 버그 → §6a로 수정 |
| `f82ab110` | `0201EDC8` | `0008 / 0000` | SP `01C0EC3C BELOW`, USTACK 1F10/5F00 | 11,210 | §6a 수정 후 9.4배 생존 |
| `36251743` | `0201EFF6` | `0008 / 0000` | SP `01C7BEFC S OK`, USP `01C0EC6C`, SSP `01C7BF00`, **DEEP `5731C1F4 ?`** | 1,147 | 계측기 투입 |

(`eae03ad8`도 빌드·전달했지만 그 태그의 사진은 없다. 위 세 장 모두 `EMU 0008 DBG 0000`,
PC는 `setjmp` 내부 — 즉 전부 같은 원인이다.)

### 증거: 툴체인의 `setjmp`/`longjmp` 디스어셈블리 (`build/picovave/picovave.dis`)

```
setjmp:                                    ; newlib pi32v2
 201efe4:  [r0+] = {r15-r0}                ; 레지스터 16개를 jmp_buf[0..63]에 저장
 201efe8:  r1 = sp                         ; 호출자의 sp를 r1에
 201efec:  sp = r0                         ; *** sp = jmp_buf + 64 — 즉 .bss 주소! ***
 201eff0:  sp += 100
 201eff2:  [--sp] = {sp, ssp, usp, icfg, psr, rets, retx, rete, reti}
 201eff6:  [sp+32] = r1                    ; ★ 관측된 크래시 PC (0201EFF6)
 201eff8:  sp = r1
 201effc:  r0 = 0
 201effe:  rts

longjmp:
 201f000:  [r0+0] = r1                     ; 반환값 1을 jmp_buf[0]에 (= r0로 돌려줄 값)
 201f002:  r0 = r0 + 4
 201f004:  {r15-r1} = [r0+]                ; r1..r15 복원 (60 B)
 201f008:  sp = r0                         ; *** 여기서도 sp를 jmp_buf로 ***
 201f00c:  r0 = [r0+-4]
 201f00e:  sp += 60
 201f010:  {sp, ssp, usp, icfg, psr, rets, retx, rete, reti} = [sp++]
 201f014:  rts
```

`sizeof(jmp_buf)`는 정확히 **100 B**(25워드 = 16 GPR + 9워드: `machine/setjmp.h`의 `_JBLEN (16+8+1)`,
ELF 심볼 `jmpbuf_restart` size=100)이고, 위 코드가 쓰는 범위도 정확히 그 100 B다(64 + 36).
**버퍼 오버런은 없다.** 문제는 그 100 B가 `.bss`에 있다는 것뿐이다.

`FM1_GUARD_STACK`은 EMU의 sp 창을 `[_ustack_lo, _sstack_top]`로 잡는다(`fm1_guard.h:59-66`:
`FM1_EMU_SSP_L/H`, `FM1_EMU_USP_L/H`, `FM1_EMU_CON |= 1<<3`). 그래서 위 `sp = jmp_buf + 64` /
`[--sp] = {...}` 접근은 **전부 sp-창 밖 위반**이고, 첫 `setjmp`에서 `EMU_MSG bit 3 (0008)`이 뜬다.
`DBG 0000`인 이유도 자명하다 — 쓰는 곳은 밴드가 아니라 `.bss` 안이다.

모든 관측값이 이 하나로 설명된다:

| 관측 | 설명 |
|---|---|
| `EMU 0008` | sp 창 밖 접근 — `setjmp`가 `sp`를 `.bss`로 옮긴다 |
| `DBG 0000` | 밴드(`_guard0`/`_guard1`)에 쓴 게 아니다 |
| `PC 0201EFF6` = `setjmp+0x12` | `[sp+32] = r1`, 즉 스크래치 sp로 쓰는 그 명령 |
| `f[22] = jmpbuf_restart + 0x40` | `sp = r0`(jmp_buf+64) 중간값 그대로(§5) |
| 1,147 ms에 죽음 | 부팅(패널·빵부스러기·`p8_init`·첫 카트 로드 ≈ 1초) 뒤 **첫 프레임의 `setjmp`** |
| 한 번도 못 뜸 / wasm은 정상 | 앱의 `setjmp`는 매 프레임 도는 필수 경로다. emscripten `setjmp`는 sp를 안 건드린다 |
| `36251743`의 `DEEP 5731C1F4` | **오해였다.** `pv_sp_min`은 `.noinit`인데 정상 부팅 경로에서 리셋되지 않아, 콜드 부팅 시 이전 내용(쓰레기)이 그대로 남았다. 아래 수정 참고 |

### 왜 Lua의 `setjmp`는 안 걸렸나

`luaD_rawrunprotected`의 `struct lua_longjmp lj`는 **스택 지역변수**다. 스크래치 sp = `&lj + 64`는
사용자 스택 창 **안**이므로 창을 위반하지 않는다. 걸리는 것은 `.bss`에 사는 앱의 `jmpbuf_restart`
하나뿐이고, 그게 바로 `src/picovave_p8.h`의 프레임 루프 `setjmp(jmpbuf_restart)`다.

### 수정 (적용됨, `src/picovave.c` `fm1_cstart`)

1. **`FM1_GUARD_STACK` 창을 쓰지 않는다.** `fm1_guard_enable(FM1_GUARD_WRITE | FM1_GUARD_BUS |
   FM1_GUARD_PC)` + 방어적으로 `FM1_EMU_CON &= ~(1u<<3)`. ("창을 넓히면 된다"가 아니라 **끄는 것**이
   정답이다: 창을 RAM 전체로 넓히는 것은 사실상 끄는 것과 같고, 그 좁은 창은 newlib `setjmp`와
   양립할 수 없다.)
2. 남는 스택 보호: **쓰기 밴드**(`FM1_GUARD_WRITE`)가 스택 오버플로를 잡는다(실제 오버플로가 나는
   모양이 이것이다) + `LUAI_MAXCCALLS=48`(24,320 B 스택에 맞춰 계산됨) + 부팅 화면의
   `STACK n/24320`(`pv_stack_paint`/`pv_stack_depth`). 유일한 사각지대는 **밴드(256 B)를 건너뛰는
   단일 프레임**이다.
3. `pv_sp_min`/`pv_sp_bad`를 정상 부팅 경로에서도 리셋(TIMER5 시작 직전). 이제 `DEEP`이 의미를 가진다.
4. 계측기(`pv_sp_bad`)는 소프트웨어 sp 감시로 남겨둔다 — EMU 창이 꺼졌으므로 이 ISR 검사가 유일한
   sp 창 감시자다.

### 남은 날카로운 모서리 (버그는 아니지만 기억할 것)

`picovave_boot()`은 `p8_begin()`을 `setjmp` **바깥**에서 부른다(래더 루프). 카트의 `_init`이
`reset()`/`run()`을 부르면 `p8_abort()` → `longjmp(jmpbuf_restart)`가 **죽은 프레임의 jmp_buf**로
점프한다(첫 부팅에서는 `.bss` 0 = sp 0). 기본 카트는 그러지 않으므로 지금은 무해하다. 대책이
필요해지면 **래더 루프 자체를 `setjmp`로 감싸고**(프레임 루프와 같은 패턴) 실패 카트로 내려보내면 된다.

### 이번 크래시의 원인이 **아니었던** 것 (다시 파지 말 것)

- "누군가 `jmpbuf_restart`를 손상시켜 야생 SP가 되었다" — 틀렸다. `jmp_buf`는 손상되지 않았다.
- 카트 메모리 범위 초과(§6b) — 실제 결함이지만 크래시 원인 아님(카트를 넣은 적도 없다).
- `LUAI_MAXCCALLS=48` — 크래시 원인은 아니지만 **여전히 필요하다**(엔진 Lua C 깊이를 24,320 B
  스택에 맞춘다). `tools/build_picovave.py`의 주석이 "이게 크래시 원인"처럼 읽히면 그것이 오해다.
- 초기 `SP ... ABOVE`/`BELOW` 판정, `_ustack_init` 여유(§6a) — §6a는 실제로 고친 버그지만
  이 크래시와는 별개다.

---

## 8. 게이트 상태 — WIP 패치가 남긴 빨간 트리를 해소함

이전 커밋(`78119fd`)에는 **빌드·검증 게이트 두 곳을 실패시키는 상태로 커밋된 계측기 패치**가 있었다
(§7 수정 전에 그 원인을 찾으려고 넣은 것):
`tools/build_picovave.py`의 MMIO 린트(`0x02000120u`, `0x02100000u` 리터럴)와
`tools/verify_final.py`의 `fm1_timer5_irq(void)` 슬라이스(시그니처가 `(uint32_t isr_sp)`로 바뀜).
이번에 함께 해소했다:

1. `src/picovave.c` — `pv_sp_bad_pc`의 "코드 주소인가" 판정을 리터럴 창 대신 **링커/HAL 이름**으로:
   `FM1_XIP(FL_APP_LO)` .. `_etext`, 그리고 `.ram_text`의 `_rt_start` .. `_rt_end`(둘 다 `fm1_guard.h`).
   이제 이 계측기는 "우리 코드가 레지스터/창을 만지지 않는다" 검사를 통과한다.
2. `tools/verify_final.py` — ISR 슬라이스를 `fm1_timer5_irq(` ... `extern void isr_timer5(void);`로
   바꿔 시그니처 변화에 견고하게 했다. (이전 슬라이스는 `panel_up`이 HAL 쪽으로 옮겨가면서
   **빈 문자열**이 되어 검사가 조용히 무의미해지는 상태였다.)

빌드/검증 결과(태그 `cc27cc77`):

```
image 234196 B (slot 0x97400) · RAM .data+.bss 145608 B of 147456 · Lua heap 293888 B
package 651271 B, identity 'SMK-37 Pro_700', tag cc27cc77
tools/build_sim.py --engine docker   boots=31, draws=191, parity=ok
verify_final.py                      all checks passed
```

---

## 9. 그 다음 우선순위

1. **실기 확인** — `build/picovave-cc27cc77.fwsc`를 플래싱하고 패널 사진 한 장. 기대: 기본 카트
   (버튼으로 픽셀을 옮기는 화면)가 뜨고, 부팅 화면의 `STACK n/24320`이 여유를 보여준다.
   실패하면 여전히 §2의 표로 읽는다(`PC`/`EMU`/`DBG`/`SP`/`DEEP`/`WILD`).
2. **래더 테스트 마무리** — 롤백 사다리(최신 유효 슬롯 → 그것이 대체한 슬롯 → 내장 기본 카트 → 없음)를
   `sim/sim_test.mjs`에 추가(깨진 카트 폴백 + 사다리 순회). §7의 "날카로운 모서리"(`_init`의
   `reset()`)도 여기서 함께 다룰 수 있다.
3. **BLE 결론 문서화** — picovave에는 BLE MIDI가 **없다**(USB-MIDI만). 스톡 v016 `app.bin`에만
   BLE-MIDI GATT UUID(`03B80E5A-…`, `7772E5DB-…`)와 `BTSTACK`/`BTCTRLER`가 있었고, picovave·
   `hello`의 `app.bin`에는 0건이다. 복구 경로로 BLE를 기대하면 안 된다 — `README.md`/`RECOVERY.md`에 적을 것.
4. 여유가 되면 USB로 크래시 레코드를 내려받는 덤프를 추가 — 사진 판독을 없앨 수 있다.
5. `tools/ota_probe.py`는 `mido` 의존이라 이 개발기에서 실행할 수 없다. 의존성 없는 경로(또는 설치)
   중 하나를 정할 것.

### 큰 함정 두 개 (반드시 기억할 것)

1. **호스트 초록불은 기기 정상의 증거가 아니다.** 호스트/emscripten의 `setjmp`는 sp를 건드리지 않고
   스택도 8 MB다. 이번 크래시가 정확히 그 예였다(기기에서만 100% 재현, 시뮬레이터는 항상 초록).
2. **`.bss` 레이아웃에 의존하는 버그는 호스트에서 원리적으로 안 보인다.** 기기 레이아웃을 재현하는
   검사(호스트 + 기기 allocator + ASan)가 필요하면 `host/`·`sim/device_alloc.c`를 쓸 것.

---

## 10. 이번 작업에서 건드린 파일

| 파일 | 내용 |
|---|---|
| `platform/firmware/app.ld` | `_ustack_init`/`_sstack_init` 여유(41-42), 스택/가드 상수(31-32) |
| `platform/firmware/crt0.S` | 위 초기값 사용 |
| `platform/firmware/hal/fm1_isr.S` | ISR 스텁이 sp를 C 본문에 전달(현재 커밋에 포함) |
| `platform/firmware/hal/fm1_guard.h` | 가드 창/비트 정의와 설명 |
| `platform/firmware/hal/fm1_irq.h` | `fm1_crash_t`(usp/ssp 포함) + 프레임 주석(§5 주의) |
| `src/picovave.c` | 스택 페인트/스캔(`pv_stack_paint`/`pv_stack_depth`), `pv_app_sp`, `pv_stack_of`/`pv_stack_ok`, `pv_crash_report`, 부트 마크 `pv_early_mark`, ISR 계측기(`pv_sp_min`, `pv_sp_bad`), **EMU 스택 창 비활성(§7 수정)**, sp 계측기 리셋(§7) |
| `src/picovave_gfx.h` | 크래시 화면 밴드(11줄, WIP `WILD` 포함) |
| `src/picovave_p8.h` | 프레임 루프의 프레임당 `setjmp`(102) |
| `femto8/src/p8_lua.c` | 카트 메모리 범위 가드(`mem_ok`) |
| `femto8/src/p8_emu.c` | `jmpbuf_restart`/`p8_abort`/`p8_restart` — §7의 "날카로운 모서리" 참고 |
| `tools/verify_final.py` | 199검사, 크래시 화면 밴드 기하 검사, 엔진 범위 검사 2건, ISR 슬라이스 수정(§8) |
| `tools/build_picovave.py` | 빌드 + 기본 카트 굽기 + 태그 출력 |
| `tools/ota_probe.py` | 플래싱/`--ask`/`--list` |
| `tools/preview_picovave.py` | 호스트 프리뷰 빌드(크래시 화면 렌더) |
| `tools/pvcart.py` | 기본 카트(`DEFAULT_SCRIPT`) |
| `README.md` | 검사 수 199 갱신 |

주의: `build/picovave/cart_default.h`와 `picovave/cdata/`는 빌드 시 생성되는 산출물이라 추적하지 않는다
(상위 `.gitignore`).

---

## 11. 다음 사람에게 필요한 것 한 가지

플래싱은 비싸다(사용자가 매번 손으로 한다). 새 빌드를 드릴 때는 **한 장의 사진으로 최대한 많이
읽히도록** 만들 것.

- **정상이면**: 기본 카트 화면 + 부팅 화면의 `BUILD`/`STACK n/24320`만 확인하면 된다. 그 화면이
  뜨면 §7 수정이 맞은 것이고, 그 다음은 카트 업로드(페이지)와 래더 테스트다.
- **죽으면**: `BUILD`(무엇이 올라갔는지), `PC`, `EMU/DBG`, `SP`, `DEEP`, `WILD` — 이 여섯 줄.
  `EMU 0008 DBG 0000` + `PC`가 `setjmp`라면 **이번 수정이 안 들어간 패키지**인지부터 확인할 것
  (`--ask`로 태그 확인, 기대값 `cc27cc77`).
- 크래시 기록은 `.noinit`이라 리셋을 넘어 살아남는다. 부팅 후 10초 동안 같은 화면이 다시 뜬다.
