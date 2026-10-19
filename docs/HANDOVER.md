# 핸드오버 — picovave (SMK-37 Pro PICO-8 펌웨어) 현재 상태

작성 2026-10-17, 갱신 2026-10-19. 이 문서는 **다음 사람(또는 다음 세션)이 이어받기 위한 것**이다.
소스·툴·시뮬레이터는 완성되어 있고, 실기에서 나온 결함 두 건(부팅 크래시 · 카트가 기기 풀보다 클 때의
멈춤)은 **원인을 확정해 고쳤다**(§7). 남은 것은 **실기 확인 한 번**이다(§11). 확정된 사실과 아직 아닌
것을 명확히 구분해 적었다 — 추측을 사실처럼 읽지 말 것.

---

## 0. 한눈에

| | 상태 |
|---|---|
| 앱 계층 (PICO-8/femto8 이식) | 완성. 카트 `.p8.png` 업로드(SLOOP 저장 + USB SysEx) |
| wasm 시뮬레이터 / 호스트 프리뷰 / 기기 | 같은 엔진, 화면 **픽셀 단위 일치** (`tools/verify_final.py` 통과, "all checks passed") |
| 펌웨어 부팅 | **원인 확정·수정 완료.** 원인은 "야생 SP"가 아니라 **newlib `setjmp`의 sp 사용 vs 하드웨어 EMU 스택 창**이었다(§7) |
| 원인 규명 | **100%** — 디스어셈블리로 증명(§7). 피해자는 `setjmp`, 가해자도 `setjmp` |
| 카트가 기기 풀보다 클 때 | **원인 확정·수정 완료(2026-10-18).** 실기에서 멈추던 것(부팅 화면 고정)은 하드웨어가 아니라 **래더가 후보 사이에 풀을 돌려주지 않은 것**이었다. 이제 거부된 카트는 **기본 카트로 폴백해 계속 실행**된다(§7 두 번째 결함) |
| 45 KB 카트(baba) | **기기에서 돈다.** 캔버스를 PICO-8 크기로 줄이고 예약 RAM을 회수해 아레나를 414,936 B(Lua 힙 413,912 B)로 넓혔다 — 실측 baba peak 402 KB·거부 5회/300프레임, 즉 가끔 GC 히치만 있고 실행된다(§13) |
| 화면 주사율 | **표시 경로 최적화 완료(§15).** 12 MHz SPI + 240개 행별 윈도우 → 30 MHz SPI + 프레임당 윈도우 1개. 115,200 B 전송은 ~77 ms에서 ~31 ms로 줄었다. 상단 FPS 숫자는 제거했다(엔진의 목표 프레임 기반 추정값이라 실측과 달랐다) |
| 입력 | **일곱 개 — D-pad·O·X·Pause.** PICO-8의 ESC(중단) 입력은 **없다**: ESC 중단은 콘솔로 돌아가는 동작이고 이 포팅에는 돌아갈 개발환경이 없다. `pv_gamepad()`에서 패드 12/건반 31의 select 매핑을, `seam`에서 그 비트를, `p8_input.c`에서 `BUTTON_MASK_ESCAPE` 매핑을 걷어냈다(패드 13·건반 32는 `BUTTON_START` → `BUTTON_MASK_PAUSE`, 카트가 `btn(6)`으로 읽는다) |
| 실기 사용 가능 여부 | **확인 대기** — 태그 `993b2146` 패키지가 부팅 수정 + 래더 폴백 + §13 메모리 회수 + §15 표시 경로 최적화 + ESC 입력 제거를 함께 담은 빌드다. 사진 한 장이면 확정된다(§11) |

build/`picovave.fwsc`(**태그 `993b2146`**)가 현재 패키지다. `784914b2`(부팅 수정 + 래더 폴백 +
§13 메모리 회수 + §15 표시 경로)와 `e5e5d778`(부팅 크래시 수정)이 그 앞선 라운드들이고,
`68c5e5f1`(`f65c25d`·`55a7de4`)은 그 앞선 라운드들이고, §13이 아레나를 넓혀 45 KB 카트를
기기에서 돌렸다. 태그는 빌드 입력 전체의 sha256 앞 8자리이므로 **문서만 고쳐도 바뀐다** —
`--ask`가 말하는 값과 이 문서가 말하는 값이 다르면 그 문서가 오래된 것이다.

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
  docs/                 이 문서 + RECOVERY / REVERSE_ENGINEERING
  build/                산출물(추적 안 함)
```

---

## 2. 계측 수단은 오직 "크래시 화면"이다

기기에는 콘솔도 JTAG도 없다. 관측 수단은 **패널에 그려지는 크래시 화면 한 장**과 USB `--ask` 뿐이다.
따라서 모든 진단은 사진 한 장에 최대한 많이 담는 방향으로 설계되어 있다.

크래시 화면 각 줄의 의미(사진을 읽을 때 이 표를 쓸 것). 화면은 PICO-8의 128×128 캔버스에
헤드라인 + 12줄로 그려지고, 패널 240×240으로는 표시 경로가 한 줄씩 확대한다(§13):

| 줄 | 내용 |
|---|---|
| `CRASH` / `LAST CRASH` | 헤드라인(2배 확대). `LAST CRASH`는 **다음 부팅**이 같은 리포트를 다시 그린 것 |
| `PC <hex>` | 크래시 프레임 f[16] = 인터럽트된 PC. **가해자가 아니라 피해자**다(항상 `setjmp` 프롤로그) |
| `V01 EMUb DBGb` | 가드가 잡은 원인 비트 (§4). 공백 없이 붙여 쓴다 — 캔버스가 128 px이라 19자가 상한이다(§13) |
| `SP <hex> <U\|S\|?> <OK\|BELOW\|ABOVE>` | f[24] = **예외 핸들러 자신의** SP, 그리고 그 주소가 어느 스택 창에 있는지 |
| `DEEP <hex> <U\|S\|?>` | 10 kHz 타이머 ISR이 매 틱 표본한 **활성 스택 최저 주소**(`pv_sp_min`) |
| `USP <a>` | f[22] **원값**. 이 실리콘에서는 스택 포인터가 아니다(§5) — 참고용 |
| `SSP <b>` | f[23] 원값. 위와 같은 이유로 참고용 |
| `RET <a>` | 인터럽트된 복귀 주소 |
| `ETM0 <a>` | 하드웨어 트레이스 0 |
| `ETM1 <a>` | 하드웨어 트레이스 1 |
| `N<n> EARLY<0\|1> UP<t>MS` | 연속 크래시 횟수 / 부팅 3초 내 크래시 연속 여부 / 업텀 |
| `WILD <sp>/<pc>` | **어느 스택에도 없는** sp를 표본한 틱 수(0이면 정상)와 그때의 pc(하위 24비트) |
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

# 검증 (전체 검사, 인자 없음)
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
.ram_text     0x01C00000 .. 0x01C00BE4  플래시가 바쁠 때 도는 코드(3,044 B; 리전은 0x01C01400까지)
.data/.bss    0x01C01400 .. _bss_end    앱의 정적 데이터(2026-10-19: 0x01C0EB28, 55,080 B)
_pool_start   = _bss_end                .pool은 .bss **바로 뒤**에 온다(한 RAM 리전)
_pool_top     0x01C74000                = _guard0. 아레나 = _pool_top − _pool_start (414,936 B)
_guard0       0x01C74000               쓰기 감시 밴드
_ustack_lo    0x01C74100  ┐
_ustack_top   0x01C7A000  ┘  사용자 스택 24,320 B   (crt0 sp = _ustack_init = 0x01C79C00)
_guard1       0x01C7A000               쓰기 감시 밴드
_sstack_lo    0x01C7A100  ┐
_sstack_top   0x01C7C000  ┘  슈퍼바이저 스택 7,936 B (crt0 ssp = _sstack_init = 0x01C7BF00)
NOINIT        0x01C7C000 + 0x3D50      리셋을 넘어 살아남는 기록(fm1_crash = 0x01C7C000 포함)
```

RAM 리전은 0x01C01400에서 시작한다(§13에서 내렸다): 그 위 5,120 B는 `.ram_text`가 쓰고, 그 아래
0x01C06000..0x01C08000의 "RAM 실행 디버그" 예약은 참조하는 코드가 없어 함께 회수했다. 정적의
대부분은 이제 **PICO-8 화면 그 자체**(`p8_screen_fb`, 128×128 RGB565 = 32,768 B)이고, 패널
240×240은 표시 경로가 한 줄씩 확대한다(정적 115,200 → 32,768 B = +82,432 B의 Lua).

`_pool_top`은 스택 가드라 **고정**이고, 풀의 시작은 `.bss`가 끝나는 곳이다 — 그래서 앱이 정적
메모리를 줄이면 **자동으로** 힙이 커진다(하드코딩된 POOL 리전이 아니다; `app.ld`의 `.bss` 뒤 `.pool`,
`alloc.c`는 `_pool_start`/`_pool_top`만 읽는다). 예전에는 두 리전 사이에 1,848 B 구멍(0x01C2C000)이
손으로 박혀 있었다.

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
   `STACK n/5F00`(`pv_stack_paint`/`pv_stack_depth`). 유일한 사각지대는 **밴드(256 B)를 건너뛰는
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

### 두 번째 결함 (2026-10-18, 확정): 기기가 못 돌리는 카트는 **멈추지 않고 폴백**해야 했다

**증상(실기)**: 기본 카트 부팅은 성공. 그 뒤 웹 페이지의 FLASH(USB)로 **baba**(45 KB 스크립트)를
업로드하면 **부팅 화면에서 그대로 멈춘다**(크래시 화면이 아니라 진행이 없는 화면). 사용자 확인.

**원인(시뮬레이터에서 재현·확정 — 하드웨어 아님)**: 카트의 Lua 비용은 소스의 10~20배(게다가 런타임
데이터)이고 당시 기기 풀은 총 287 KB(294,912 B − 1 KiB red zone, 그중 엔진 baseline 112,328 B; 이후
§10에서 303,800 B로 넓혔다)라 baba는 **정직하게 거부**된다(실측: `begin=4` = `LUA_ERRMEM`, 풀 피크
293,112 B, 할당 거부 6회). 문제는 그 다음이다:

1. 거부된 Lua 컴파일이 남긴 메모리는 **도달 불가지만 수거되지 않는다**(`luaM_realloc_`가
   `LUA_ERRMEM`을 던지고, 뒤이어 GC를 돌리는 사람이 없다). 그 상태로 풀이 293 KB까지 차 있다.
2. 래더의 다음 후보는 `p8_load_cooked()`에서 **연속 64 KiB**(Lua 소스 버퍼)를 요구한다 → 할당 실패
   → 후보는 "적재 실패"로 판정 → `hh_cart_next()`가 끝(`PV_RUN_END`)까지 걸어간다.
3. `picovave_status = -1`로 래더가 끝나고 `picovave_frame()`이 매 프레임 즉시 return → **패널이 부팅
   화면에 고정**. 크래시 화면이 아니라서 "크래시"로 보이지 않았다.

**수정(적용됨)**: 래더가 **매 후보 앞에서 풀을 돌려준다** — `src/picovave_p8.h`가
`p8_stop_cart()`(`femto8/src/p8_emu.c`)를 부른다. 이 함수는 `lua_close()`(= `lua_shutdown_api()`)로
**모든 객체를 해제**하고 `L`을 NULL로 남긴다(다음 `p8_begin()` → `p8_reset_cart()` →
`lua_load_api()`가 새 상태를 만든다). 실측: baba 시도 후 `used`가 223,760 → **112,328 B**(엔진
baseline)로 돌아오고, 폴백 후보가 `load=0 begin=0`으로 실행된다.

왜 `p8_reset_cart()`가 아니라 전용 함수인가: `p8_reset_cart()`에는 `assert(!cart_running)`이 있고
(이 빌드들은 `NDEBUG`를 정의하지 않는다 → assert가 살아 있다), **업로드로 다시 부팅할 때는 옛 카트가
살아 있다**. 그 상태로 부르면 abort(에뮬레이터에서는 `proc_exit` 스텁 때문에 무한 루프)로 **새로
멈춘다**. `p8_stop_cart()`는 `p8_begin()`의 첫 두 줄과 같은 판단(`cart_running`은 프레임 루프에 대한
주장이고, 여기서는 아무도 그 카트를 스텝하지 않는다)을 명시적으로 쓴다.

**함께 고친 것**: 후보가 성공하면 `picovave_status = 0`으로 되돌린다. 예전 코드는 실패한 후보에서
`-1`을 대입한 뒤 폴백이 성공해도 그대로 두었다 — 폴백이 처음으로 "성공"하게 되자 프레임 루프가 즉시
return하는 두 번째 버그가 드러났다.

**제품 규칙(변경 없음, 이제 지켜진다)**: 스크립트 크기로는 예측할 수 없다 — 20 KB 스크립트도
문자열/테이블 폭탄이면 못 돌고, 45 KB라도 데이터가 가벼우면 돌 수 있다. 그래서 유일한 판정은
**실측**이다: 페이지가 기기와 같은 할당자로 그 카트를 실제로 돌려 `peak`/`refused`를 재고, 기기
불가면 **업로드 전에 경고하고 확인을 받는다**(`confirm()` 게이트). 그리고 기기는 어떤 경우에도 멈추지
않는다: 최신 슬롯이 거부되면 → 그것이 대체한 슬롯 → 내장 기본 카트 순으로 내려간다.

**측정값**(기기 할당자, `sim_heap_*`; §13 이전의 303,800 B 아레나):

| 카트 | peak | 판정 |
|---|---|---|
| 내장 기본 카트 | 108 KB | 실행 (여유 179 KB) |
| flipside_-1 | 204~215 KB | 실행 (여유 72~83 KB) |
| baba_demake-9 · bunnysurvivor-9 · fighter_street_ii-1 · terra_1cart-44 | 285~293 KB (거부 5~12회) | **기기 불가 → 기본 카트로 폴백** |

(§13에서 아레나를 414,936 B로 넓히자 baba·fighter·terra는 **실행**으로 바뀌었다. 그 표가 지금의 값이다.)

같은 카트가 PicoPico(ESP32-WROVER-B, PSRAM 8 MB)에서 도는 것은 **인터프리터 차이가 아니다**: femto8도
z8lua를 vendor한다(`femto8/src/lua/luaconf.h`: `#define LUA_NUMBER fix32_t`, 16.16 고정소수점).
차이는 힙 크기(수 MB vs 여기 400 KB 남짓)이고, 그 힙은 **이 기기·이 로더 계약의 앱 창**(0x01C00000
부터 512 KiB)에서 나온다 — 그 창 안에서 쓸 수 있는 정적+힙 예산은 470,016 B(§13; RAMTEXT 축소와
예약 회수로 늘렸다)이고, 그 밖의 SRAM/PSRAM을
쓰려면 로더 자신의 메모리맵을 다시 쓰는 별개의 작업이 된다.

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

빌드/검증 결과(태그 `68c5e5f1`):

```
image 234196 B (slot 0x97400) · RAM .data+.bss 145608 B of 147456 · Lua heap 293888 B
package 651271 B, identity 'SMK-37 Pro_700', tag 68c5e5f1
tools/build_sim.py --engine docker   boots=31, draws=191, parity=ok
verify_final.py                      all checks passed
```

당시 게이트(태그 `e5e5d778`, §7 두 번째 결함 수정 포함 — 이 라운드의 게이트는 §13):

```
image 234228 B (slot 0x97400) · RAM .data+.bss 145608 B of 147456 · Lua heap 293888 B
package 651271 B, identity 'SMK-37 Pro_700', tag e5e5d778
tools/build_sim.py --engine docker                 boots=31, draws=191, parity=ok
tools/build_sim.py --device-alloc --stack 24320     all ok (거부→폴백 검사 포함)
verify_final.py                                    전체 검사 all checks passed
```

---

## 9. 그 다음 우선순위

1. **실기 확인(이번 라운드)** — `build/picovave.fwsc`(태그 `993b2146`)를 플래싱하고 **세 장면**을 본다:
   (a) 부팅 → 부팅 화면(`PICOVAVE BOOT`, `STACK n/5F00` 여유) → **기본 카트 화면**(버튼으로 픽셀을
   옮기는 화면) + `--ask`의 `build 993b2146`; 화면이 이전보다 부드러운지, 30 MHz에서 점 노이즈가
   없는지도 함께 본다(있으면 §15의 `LCD_BAUD 2` 폴백);
   (b) 페이지에서 **baba와 loop-4를 FLASH(USB)로 업로드** → 패널이 그 카트를 **실행**한다(§13:
   아레나가 414,936 B라 45 KB 스크립트가 들어간다; baba는 가끔 GC 히치). 페이지의 카트 줄은
   `기기 빠듯`(주황)으로 미리 알려준다 — **폴백하지 않는다**;
   (c) 페이지에서 **못 돌리는 카트(bunnysurvivor)를 업로드** → 패널이 **멈추지 않고 기본 카트로
   폴백**(§7 두 번째 결함)하고 USB/OTA가 계속 살아 있다.
   실패하면 §2의 표로 읽는다(`PC`/`EMU`/`DBG`/`SP`/`DEEP`/`WILD`).
2. **BLE 결론 문서화** — picovave에는 BLE MIDI가 **없다**(USB-MIDI만). 스톡 v016 `app.bin`에만
   BLE-MIDI GATT UUID(`03B80E5A-…`, `7772E5DB-…`)와 `BTSTACK`/`BTCTRLER`가 있었고, picovave·
   `hello`의 `app.bin`에는 0건이다. 복구 경로로 BLE를 기대하면 안 된다 — `README.md`/`RECOVERY.md`에 적을 것.
3. 여유가 되면 USB로 크래시 레코드를 내려받는 덤프를 추가 — 사진 판독을 없앨 수 있다.
4. 여유가 되면 §7의 "날카로운 모서리"(`_init`이 `reset()`/`run()`을 부르는 카트 → 래더 루프 밖의
   `longjmp`)를 처리: 래더 루프 자체를 `setjmp`로 감싸면 그런 카트도 폴백으로 내려보낼 수 있다.
   현재 기본 카트는 그러지 않아 무해하고, 업로드되는 카트는 baba류가 대부분이라 우선순위가 낮다.
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

### 래더 폴백 수정(2026-10-18)에서 건드린 파일

| 파일 | 내용 |
|---|---|
| `src/picovave_p8.h` | 래더 루프가 **매 후보 앞에서** `p8_stop_cart()` 호출(풀 반납, §7 두 번째 결함), 성공 후보에서 `picovave_status = 0` |
| `femto8/src/p8_emu.c` | `p8_stop_cart()` 추가 — `lua_close()`로 전부 해제, `L`은 NULL로(= `p8_reset_cart()`의 해제 절반, `assert` 없음) |
| `femto8/src/p8_emu.h` | `p8_stop_cart()` 선언 |
| `sim/sim.c` | `sim_cart_running()` export(= `pv_run_slot`; -1=내장 기본, 0/1=슬롯) |
| `sim/sim_test.mjs` | 실카트 루프가 **폴백까지** 검사: `status === 0` 그리고 (거부 0이면 그 슬롯 / 거부>0이면 내장 기본) |
| `web/sim/index.html` | `measureCart()`가 `refused`/`running`까지 반환, `fitText()`가 판정 표시, FLASH(USB) 업로드 전 `confirm()` 게이트 |
| `tools/verify_final.py` | 204검사(래더 4건 + 페이지 실측 1건 추가) |
| `docs/HANDOVER.md` · `README.md` · `docs/RECOVERY.md` | 이 원인과 폴백 동작 문서화 |

---

## 11. 다음 사람에게 필요한 것 한 가지

플래싱은 비싸다(사용자가 매번 손으로 한다). 새 빌드를 드릴 때는 **한 장의 사진으로 최대한 많이
읽히도록** 만들 것.

- **정상이면**: 기본 카트 화면 + 부팅 화면의 `BUILD`/`STACK n/5F00`만 확인하면 된다. 그 화면이
  뜨면 §7의 부팅 수정이 맞은 것이다. 이어서 페이지에서 **baba를 FLASH(USB)로 업로드**해 보고 패널이
  **baba를 실행하면**(§13: 45 KB 스크립트가 아레나 414,936 B에서 돈다 — 가끔 GC 히치, **폴백하지
  않는다**) 이번 라운드의 목표가 확인된다. 못 돌리는 카트(예: bunnysurvivor)를 올려 **기본 카트로
  폴백하고 멈추지 않는 것**은 §7 두 번째 결함의 확인이다. 화면이 30 MHz에서 깨끗한지도 §15의
  실기 확인 항목이다.
- **죽으면**: `BUILD`(무엇이 올라갔는지), `PC`, `EMU/DBG`, `SP`, `DEEP`, `WILD` — 이 여섯 줄.
  `EMU 0008 DBG 0000` + `PC`가 `setjmp`라면 **이번 수정이 안 들어간 패키지**인지부터 확인할 것
  (`--ask`로 태그 확인, 기대값은 §0의 `993b2146`).
- **멈추면**(크래시 화면 없이 화면이 안 움직이면): §7 두 번째 결함의 증상이다 — 이 패키지에서는
  `p8_stop_cart()`가 후보 사이에 풀을 돌려주므로 나오지 않아야 한다. 나온다면 `--ask`로 태그부터.
- 크래시 기록은 `.noinit`이라 리셋을 넘어 살아남는다. 부팅 후 10초 동안 같은 화면이 다시 뜬다.

---

## 12. 힙을 더 넓힌 작업 (2026-10-18, 실기 신고에서 출발)

**신고**: 실기에서 12 KB PNG(`flipside_-1.p8.png`)만 돌고 13 KB(`loop-4.p8.png`)는 실패한다.
"메모리를 더 확보할 여지가 있는가?"

**측정**(`tools/probe_carts.mjs`, 기기 비용 모델, 300프레임; `node tools/probe_carts.mjs build/sim/sim.wasm`):

| 카트 | PNG | 스크립트 | peak@300 | peak@1 | 피크 위치 | 구 아레나 여유 |
|---|---|---|---|---|---|---|
| flipside_-1 | 12,113 | 8,268 | 208,732 | 208,732 | 로드 | +85,156 |
| **loop-4** | **13,439** | **12,089** | **294,415** | 232,109 | **런타임** | **−527** |
| fighter_street_ii-1 | 45,334 | 39,786 | 585,287 | 358,131 | 런타임 | −291,399 |
| terra_1cart-44 | 61,037 | 40,944 | 641,130 | 640,960 | 로드 | −347,242 |
| baba_demake-9 | 41,343 | 45,053 | 638,312 | 344,526 | 런타임 | −344,424 |
| bunnysurvivor-9 | 44,432 | 49,966 | 2,176,132 | 743,188 | 런타임 | −1,882,244 |

loop-4는 **527 B** 차이였다 — 13 KB가 "안 되는" 이유가 그 정도다. peak@1 < peak@300이므로
막는 것은 로드(컴파일)가 아니라 **카트가 도는 동안 자라는 Lua**다.

**넓힌 것 (2건, 합계 +9,912 B):**

1. **`.bss`와 풀 사이의 1,848 B 구멍을 없앴다** — `app.ld`가 `.data/.bss`와 `.pool`을 **한 RAM
   리전**에 두고 `.pool`을 `.bss` 바로 뒤에 놓는다. 풀은 이제 `_pool_start`(=.bss 끝)에서
   `_pool_top`(=_guard0)까지이고, `tools/build_picovave.py`는 그 크기를 **링커 심볼에서 읽는다**
   (하드코딩한 `POOL_LEN`은 사라졌다). 앞으로 앱이 정적 메모리를 줄이면 힙이 자동으로 그만큼 커진다.
2. **PATH_MAX 버퍼 8,064 B** — `m_current_cart_dir`/`m_current_cart_file_name`은 툴체인의
   PATH_MAX(4,096) 크기였는데, 이 경로에 들어오는 값은 31 B 카트 이름과 `"."` 뿐이다. 64 B로
   줄였다(`p8_emu.h`의 `PV_CART_PATH_MAX`). 엔진의 파일/디렉터리 경로는 이 기기에서 도달 불가다.

**결과**: 아레나 294,912 → **304,824 B**, Lua 힙 293,888 → **303,800 B**. loop-4의 294,415 B는
이제 9,385 B 여유로 들어간다. `.data+.bss` 145,608 → 137,544 B.

**함께 고친 것: Lua 소스 버퍼는 카트 크기에 맞춘다.** `p8_materialize_script()`는 항상
`malloc(LUA_SCRIPT_SIZE)`(64 KiB)를 잡았다. 압축된 코드 섹션은 헤더 4~5바이트에 **원본 길이**를
가지고 있으므로(`:c:`/`pxa`는 big-endian, 구형 raw는 0x3d00) 그만큼만 잡는다. 로드 순간에 풀을
비우는 양: flipside **57,267 B**, loop-4 53,446, fighter 25,749, terra 24,591, baba 20,482,
bunny 15,569. loop-4처럼 런타임이 병목인 카트에는 영향이 없지만, **로드가 피크인 카트**(terra의
peak@1=peak@300)와 컴파일 천장을 올린다.

**남은 레버(우선순위순, 아직 안 함):**

| 레버 | 크기 | 비용/위험 |
|---|---|---|
| NOINIT 여유 (0x01C7C000..0x01C7FD50) | 15,600 | 중간 — 두 번째 아레나가 필요(alloc.c는 연속 리전 하나) |
| 엔진의 나머지 정적(m_clipboard 1,024 등 카트 가시 버퍼) | ~2,000 | 카트 동작이 바뀔 수 있음 |
| 할당자 red zone(PV_POOL_GUARD 1,024) | 1,024 | 위험 — 마지막 블록 넘침을 막는 안전 여유 |
| SDRAM/다른 SRAM 창 | MB 단위 | 로더 메모리맵 재작성 — 별개 프로젝트 |

이 표의 첫 두 줄(렌더 타깃 115,200 B, `.ram_text` 여유 + RAM-디버그 예약)은 **§13에서 회수했다**
(+110,080 B).

주의: 비용 모델 수치(n+16/블록, `sim/heap_probe.c`)는 **보수적**이고, 그것은 모델일 뿐이다. 실행
결과는 언제나 **기기 allocator로 기기 아레나에서 돌린 실측**이다 — `tools/build_sim.py`의
**기본**이 그 빌드이고(`--probe-alloc`이 비용 모델 빌드), 페이지의 `measureCart()`, `sim_test.mjs`,
`tools/probe_carts.mjs`가 모두 그것을 읽는다. 그래서 페이지는 실기에서 돌지 않는(또는 눈에 띄게
끊기는) 카트를 업로드하기 전에 경고하고 확인을 받는다.

---

## 13. 화면 캔버스를 PICO-8 크기로, 그리고 예약 RAM 회수 (2026-10-18, 같은 날 두 번째)

§12로 loop-4(13 KB 스크립트)까지 들어갔지만 **baba_demake-9(45 KB)는 여전히 기기 불가**였다.
실측 스윕(`tools/probe_carts.mjs --sweep`)에서 baba는 아레나 384 KB에서 죽고 400 KB에서
살아남았다 — 즉 아레나가 **약 400 KB**는 되어야 한다. 그런데 `.bss`의 83%가 렌더 타깃 하나였다.

| 대상 | 이전 | 지금 | 회수 |
|---|---|---|---|
| 렌더 캔버스 | `p8_panel_fb[240*240]` 115,200 B | `p8_screen_fb[128*128]` 32,768 B | **+82,432** |
| `.ram_text` 여유 + RAM-디버그 예약 (0x01C00BE4..0x01C08000) | 24,576 + 8,192 B | `.ram_text` 리전 5,120 B(실제 3,044 사용) | **+27,648** |
| 합계 | | | **+110,080 B** |

**왜 128×128인가.** PICO-8의 화면은 128×128이고, 엔진은 이제 카트를 **거기에 1:1로** 그린다
(`femto8/src/p8_emu.c`의 `p8_render()`). 128→240 최근접 확대는 표시 경로
(`femto8/seam/gdi.h`의 `gdi_expand_row()`)로 내려갔고, 세 소비자가 그 한 함수를 쓴다:

- **기기**(`src/picovave.c`): 프레임마다 주소 창을 한 번만 열고, 한 줄(240 px)씩
  `lcd_stream_row()`로 밀어넣는다 — 패널 한 프레임이 RAM에 **동시에 존재하지 않고**, 행마다 창을
  다시 잡던 240회의 명령 시퀀스도 사라졌다(§15).
- **wasm 시뮬레이터**(`femto8/seam/seam_sim.c`)와 **호스트 프리뷰**(`host/seam_host.c`): 각자의
  240×240 캡처로 편다.

카트 한 프레임과 폴트 화면이 **같은 캔버스, 같은 경로**로 패널에 간다: 앱 화면
(`src/picovave_gfx.h`의 `hg_fb`)을 `p8_screen_fb`에 앨리어스했으므로 부팅/크래시/업데이트/복구
화면은 RAM을 한 바이트도 더 쓰지 않는다(별도의 240×240 앱 캔버스는 사라졌다).

**부수 효과(의도한 것).** 구 기기 렌더는 SDL 경로와 달리 `MEMORY_SCREEN_TRANSFORM`(0x5f2c 화면
변형)과 하이컬러 모드를 **무시**했다. 이제 둘 다 적용되고, 두 헬퍼(`screen_transform_pixel`,
`high_color_resolve`)는 `#ifdef SDL` 밖으로 나왔다(`m_memory`만 읽는다) — 기기·wasm·프리뷰가 같은
픽셀을 그린다. 엔진의 I/O 아이콘 오버레이도 같은 128 격자에 합성된다(위치가 정확해졌다).

**RAM 리전 이동.** `app.ld`의 RAM이 `0x01C01400`에서 시작한다(RAMTEXT 리전은
0x01C00000..0x01C01400). 0x01C06000..0x01C08000의 "RAM 실행 디버그" 예약은 **참조하는 코드가 없어**
함께 회수했다(전 소스 확인: 하드코딩된 `0x01C08000`도 없다).

**결과**: `.data+.bss` 137,544 → **55,080 B**, 아레나 304,824 → **414,936 B**
(`0x01C0EB28..0x01C74000`), Lua 힙 303,800 → **413,912 B**.

**baba는 이제 기기에서 돈다.** 실측(기기 allocator, 기기 아레나, 300프레임):

| 카트 | 스크립트 | peak | 거부 | 판정 |
|---|---|---|---|---|
| 내장 기본 카트 | 266 | 110 KB | 0 | 여유 (294 KB 남음) |
| flipside_-1 | 8,268 | 167 KB | 0 | 여유 (236 KB 남음) |
| loop-4 | 12,089 | 300 KB | 0 | 여유 (§12의 그 카트) |
| **baba_demake-9** | **45,053** | **402 KB** | **5** | **실행** — 아레나 끝, 가끔 GC 히치 |
| fighter_street_ii-1 | 39,786 | 396 KB | 99 | 실행되지만 눈에 띄게 끊김 |
| terra_1cart-44 | 40,944 | 379 KB | 120,413 | 실행은 되지만 사실상 못 씀 |
| bunnysurvivor-9 | 49,966 | 402 KB | 3 | 로드 실패 → 래더가 내장 카트로 폴백 |

**60초(1800프레임) 장주행**(`tools/probe_carts.mjs --sweep`, 아레나를 바꿔 가며): baba는
**384 KB에서 죽고**(status 4 = `LUA_ERRMEM`, 거부 15회) **400 KB에서 산다**(거부 33회). 지금
아레나가 414,936 B = 404 KB이므로 **여유는 4 KB 남짓**이다 — 1800프레임에서도 살아남지만, 그
이하로 내려가면 다시 부팅 실패다. flipside와 loop-4는 어느 아레나에서도 거부 0회,
bunnysurvivor는 항상 로드 실패 → 내장 카트 폴백이다.

즉 8 KB에서 45 KB까지가 "기기에서 도는" 범위이고, 그 위는 "돌긴 한다"와 "쓸 만하다"가 갈린다.
그래서 페이지의 판정은 **기기 allocator로 기기 아레나에서 돌린 실측**(status + 거부 횟수)이다:
거부 0회 = `기기 OK`, 가끔 = `기기 빠듯`(알림), 10프레임에 1회 이상 = `기기 불가(끊김)` + 업로드 전
확인. `tools/build_sim.py`의 **기본** 빌드가 그 할당자이고(비용 모델 빌드는 `--probe-alloc`), 페이지의
*실행 중* 시뮬레이터는 아레나 배열 전체(1 MiB)를 받아 기기에 안 들어가는 카트도 그대로 돌린다 —
측정용 일회용 인스턴스만 기기 아레나를 쓴다.

**화면 레이아웃도 다시 잡았다.** 캔버스가 128폭이 되면서(글자 6 px = 한 줄에 20자가 **여백을 두고**
들어가는 상한; 21자는 126 px라 양끝이 베젤에 닿는다) 앱 화면의 좌표와 문자열을
고쳤고, 크래시 화면은 헤드라인 + 12줄로 **구 화면의 모든 필드를 유지**한다(`USP`/`SSP`와
`RET`/`ETM`은 이제 각각 한 줄, `WILD`의 sp+pc만 한 줄에 붙인다).
`tools/verify_final.py`가 캔버스 격자에서 줄 위치를 계산해 빈 줄·양끝 잘림·줄 충돌을 검사한다.

**이 라운드에서 건드린 파일**

| 파일 | 내용 |
|---|---|
| `femto8/seam/gdi.h` | 캔버스 계약: PICO-8의 128×128 RGB565(`GDI_W/H`), `gdi_expand_row()`(128→240 최근접 확대), `gdi_sx_init()` |
| `femto8/src/p8_emu.c` | `p8_render()`가 128×128에 1:1로 렌더; `screen_transform_pixel`/`high_color_resolve`를 `#ifdef SDL` 밖으로 |
| `femto8/seam/seam_device.c`·`seam_sim.c` · `host/seam_host.c` | 세 소비자가 `gdi_expand_row()`로 각자의 240×240 캡처를 채운다(기기는 한 줄씩 패널로) |
| `src/picovave.c` | `pv_push`/`pv_blit`(프레임당 윈도우 1개 + 한 줄 240 px씩 스트리밍), 앱 화면 좌표/문자열, sx 테이블 `pv_row_sx` |
| `src/picovave_gfx.h` | 앱 캔버스가 엔진 표면 앨리어스(`hg_fb` → `p8_screen_fb`); 크래시 화면 헤드라인 + 12줄(`V01 EMU0008 DBG0000` = 19자, 여백 확보) |
| `platform/firmware/app.ld` | RAMTEXT `0x1400`, RAM `0x01C01400` + `0x72C00`(예약 회수) |
| `tools/build_picovave.py` | `RAM_ORIGIN`/`RAM_LEN`, `.data+.bss`·아레나를 **링커 심볼**에서 읽는다 |
| `sim/device_alloc.c` · `host/alloc_test.c` · `sim/sim_test.mjs` · `sim/heap_probe.c` · `web/sim/index.html` | 아레나 414,936 B 일치(5중 검사) |
| `tools/build_sim.py` | **기본** 빌드가 기기 allocator(`sim/device_alloc.c`)로 기기 아레나에서 돈다(`--probe-alloc`이 비용 모델) |
| `tools/verify_final.py` | 캔버스 격자 기반 크래시 밴드 검사(줄 여백 포함), 5중 아레나 일치 검사, "페이지 wasm = 기기 allocator" 검사 |
| `tools/probe_carts.mjs` | 새 도구: 카트별 peak/거부/최종 슬롯 실측(§13 표·스윕의 근거). `node tools/probe_carts.mjs build/sim/sim.wasm` |
| `docs/` · `README.md` | 이 라운드의 수치·서술 갱신 |

**측정 자체가 느릴 수 있다 — 그래서 페이지가 끊어서 잰다.** 기기 allocator에서 거부 한 번은
Lua의 풀 GC + 재시도 한 번이므로, 경계에 있는 카트는 프레임당 비용이 커진다. 같은 wasm으로
300프레임을 재 본 실측(이 개발기): baba **214 ms** · loop-4 407 ms — 그런데 **terra는 71초**다
(프레임당 약 400회 거부, 그때마다 GC). 페이지가 예전처럼 300프레임을 한 번에 돌리면 브라우저가
1분 넘게 멈춘 것처럼 보인다. 그래서 `web/sim/index.html`의 측정 루프는 (a) 8프레임마다 이벤트
루프에 양보하고("측정 중…"이 실제로 그려진다), (b) **한 프레임에서 `HITCH_FRAMES`(30) 넘게
거부하는 일이 두 프레임 연속**이면 즉시 멈춘다 — 그런 카트는 이미 "돌긴 하지만 쓸 수 없다"라
판정이 바뀌지 않으므로, terra는 첫 2프레임(약 0.5초)에 끝난다. 판정 문구는 실제로 잰 프레임
수를 말한다(`첫 N프레임 측정에서 …`). `sim/run_page.mjs`가 두 극단을 모두 검사한다:
baba(거부 5회 → `기기 빠듯`, 주황)와 terra(→ `기기 불가(끊김)` + 업로드 전 확인).

---

## 14. 사전 컴파일 카트 (2026-10-19) — 업로드가 컴파일을 대신한다

페이지가 카트의 Lua를 **미리 컴파일해** 업로드하고, 기기는 그 바이트코드를 그대로 undump한다.
기기에서 사라지는 것은 두 가지다: 소스 압축해제(카트 크기만큼의 버퍼)와 Lua 컴파일(피크 152 KB,
baba 기준). 프레임 시간은 **변하지 않는다** — 같은 VM이 같은 opcode를 실행하기 때문이다(측정:
terra 172 ms/frame은 GC가 아니라 인터프리터 비용).

**포맷** (`src/picovave_cart.h`): `PVB1` + ROM 17,152 B + u32 bc_len + 바이트코드.

**저장소는 이제 한 카트다** (`PV_SLOT_COUNT 1`): 120 KiB 전체가 한 슬롯(122,880 B)이고, payload
122,816 B − 헤더 17,160 B = **bc_len ≤ 105,656 B**. 두 슬롯(61,440 B씩)으로는 어떤 실카트의
바이트코드도 들어가지 않았다(fighter 45,349 B부터 초과). 대가로 **롤백 슬롯을 잃었다**: 업로드가
재생 중인 카트를 지우므로 도중에 전원이 끊기면 저장소가 비고, 래더의 바닥인 **내장 카트**가 뜬다
(패널은 여전히 검게 남지 않는다). `pv_run_next()`는 슬롯 → 내장, 두 단이다.

**누가 컴파일하나**: `tools/luabc.c` + `tools/build_luabc.py`가 **기기와 같은 Lua 소스**를 wasm32로
빌드한 `luabc.wasm`. 덤프 헤더의 `sizeof(int/size_t/Instruction/lua_Number)` = 4/4/4/4와 리틀엔디언이
기기와 같아서, `luaU_undump`의 헤더 검사를 통과한다(`lundump.c:190-201`). 디버그 정보는 스트립한다
(`luaU_dump(..., strip=1)`) — 청크의 50~58%이고, 이 기기에는 줄 번호를 찍을 콘솔이 없다.

**기기 경로**: `p8_load_bytecode()` → `p8_begin()` → `lua_init_script_bc()` →
`luaL_loadbufferx(L, bc, n, name, "b")`. 슬롯은 XIP 포인터라 **RAM 복사가 없다**.

**실측** (기기 allocator, 아레나 414,936 B, 300프레임):

| 카트 | 바이트코드 | payload | 로드 피크 | peak@300 | 거부 | 픽셀 |
|---|---:|---:|---:|---:|---:|---|
| baba_demake-9 | 36,811 | 53,971 | 361 → **300 KB** | 404 → 404 KB | 5 → 3 | 동일 (9,006) |
| fighter_street_ii-1 | 45,349 | 62,509 | 376 → **279 KB** | 399 → 403 KB | 99 → 74 | 동일 (55,510) |
| loop-4 | 16,489 | 33,649 | 186 → **144 KB** | 300 → **242 KB** | 0 | 동일 (57,600) |
| flipside_-1 | 9,997 | 27,157 | 159 → **123 KB** | 167 → **133 KB** | 0 | 동일 (18,615) |
| terra_1cart-44 | 50,561 | 67,721 | 379 → 391 KB | 379 → 391 KB | 121,980 → 46,810 | 동일 (1,575) |
| bunnysurvivor-9 | 53,170 | 70,330 | — | — | — | **런타임이 아레나를 넘어 폴백** (로드는 성공) |

baba의 **런타임** 피크(403→404 KB)는 그대로다 — 컴파일은 로드 순간의 비용이었고, 카트의 데이터는
여전히 아레나를 채운다. loop-4/flipside의 peak@300이 내려간 것은 컴파일이 남기던 단편화가 사라져서다.

**남은 것**: 64 KB 카트의 바이트코드는 50 KB 안팎이라 한 슬롯(105,656 B)에 들어간다. 다만
bunnysurvivor처럼 **런타임**이 아레나를 넘는 카트는 바이트코드로도 살아나지 않는다(로드는 되고
첫 프레임에서 죽는다) — 그건 메모리 문제이고, 이 경로가 바꾸는 것은 로드 쪽이다.

---

## 15. 실기 FPS: 표시 경로가 병목이었다 (2026-10-19)

**신고**: 실기에서 PICO-8 카트가 느리고, 화면 왼쪽 위의 FPS 숫자가 실제 체감과 맞지 않는다.

**먼저 지운 것.** 왼쪽 위 숫자는 `p8_render()`가 PICO-8 화면(0,0)에 직접 그리던
`m_actual_fps`였다. 이 값은 `p8_flip()`에서 `1000 / (elapsed + sleep)`로 만든 **목표 프레임
기반 추정치**라, 패널 전송이 끝나기 전에 계산되고 실제 표시 주사율과 무관했다. SDL/기기 두
렌더 경로에서 그리기를 지우고, `m_actual_fps` 전역과 헤더 선언도 함께 없앴다.

**병목은 LCD 전송이었다.** 240×240 RGB565 한 프레임은 115,200 B다. 스톡 v016과 같은 SPI
분주비 `4`(이 보드의 lsb_clk 60 MHz 기준 ~12 MHz)에서는 순수 전송만
`115,200 × 8 / 12,000,000 ≈ 76.8 ms`다. 엔진이 아무리 빨라도 **이론상 13 fps**가 상한이고,
여기에 240개 행마다 주소 창을 다시 잡던 명령·동기화 오버헤드가 더해졌다.

**바꾼 것:**

| 항목 | 이전 | 지금 | 효과 |
|---|---|---|---|
| 주소 창 | 행마다 `lcd_blit()` → 240회 `0x2A/0x2B/0x2C` + 매번 동기화 | 프레임 시작에 한 번, 이후 CS를 low로 유지하고 행을 연속 전송 | 명령/동기화 240회 제거 |
| SPI 분주비 | `LCD_BAUD 4` (~12 MHz) | `LCD_BAUD 1` (~30 MHz) | 전송 76.8 ms → **30.7 ms** |
| 팔레트 변환 | 픽셀마다 `color_get()` + `color_index()` + `m_colors[]` | 프레임마다 16-entry `screen_lut[16]`/`overlay_lut[16]`, 일반 경로는 룩업 1회 | 16,384픽셀의 변환 비용 감소 |
| 오버레이 합성 | 항상 128×128 전체를 훑음 | `p8_show_io_icon(true)`일 때만 (`overlay_visible`) | cartdata를 안 쓰는 카트는 16,384픽셀 패스 생략 |

**실기에서 잡은 첫 버그(2026-10-19).** 첫 스트리밍 빌드는 `lcd_stream_row()`가 행 길이를
**픽셀 수(240)**로 넘겼는데 `fm1_lcd_send_data()`는 **바이트 수**를 받는다. 그래서 행마다 240 B
= 120픽셀만 전송됐고, 240열 창에 두 행이 겹쳐 들어가 **화면 왼쪽 절반이 상단 좌/우에 중복**되어
보였다. 지금은 `pixels * 2u`로 변환하며, 같은 실수가 다시 들어오면 `verify_final.py`가 실패한다.

**남는 상한과 주의:**

- 전송만 30.7 ms이므로 30 fps의 33.3 ms 예산에 거의 다 쓴다. 엔진/스케일/입력 비용이 더해지면
  30 fps에 못 미칠 수 있고, `_update60` 카트는 프레임마다 렌더하므로 표시 경로만으로 60 fps를
  보장할 수 없다. 이번 변경은 **표시 병목을 13 fps 상한에서 약 30 fps 상한으로 올린 것**이다.
- 30 MHz는 ST7789 계열 모듈에서 흔히 쓰이지만 데이터시트의 보수적인 write cycle보다 빠르다.
  실기에서 색 번짐·점 노이즈가 보이면 `picovave/platform/firmware/src/lcd.c`의 `LCD_BAUD`를
  `2`(~20 MHz, 전송 ~46 ms)로 내리는 것이 첫 폴백이다. 그 경우 30 fps 예산은 다시 빠듯해진다.
- **실기 측정은 아직 없다.** 시뮬레이터/호스트 픽셀 패리티와 패키지 검증은 통과했지만, 실제
  패널에서의 체감 FPS와 30 MHz 안정성은 태그 `993b2146`를 플래싱해 확인해야 한다.

**건드린 파일**: `femto8/src/p8_emu.c`(FPS 오버레이 제거, 팔레트 LUT, 오버레이 스킵),
`femto8/src/p8_emu.h`(`m_actual_fps` 제거), `platform/firmware/src/lcd.c`(`LCD_BAUD`,
`lcd_stream_begin/wait/row/end`), `src/picovave.c`(`pv_push()`가 프레임당 윈도우 하나를 열고
행을 스트리밍), `tools/verify_final.py`(행별 `lcd_blit` 대신 스트리밍 API를 검사).

## 16. 웹 UI 한국어 / English 전환 (2026-10-19)

시뮬레이터 페이지 상단에 **한국어 / EN** 버튼을 두었다. 기본은 한국어이고(실기의 언어),
선택은 `localStorage`에 기억되며 `?lang=ko` / `?lang=en`으로도 강제할 수 있다. 정적 문구는
`renderStatic()`이 ID별로 다시 쓰고, 동적 상태·로그·카트 판정은 `t(key, vars)` 사전을 거친다.
언어를 바꾸면 현재 상태·패키지 목록·카트 대상 버튼·DEVICE 화면도 즉시 다시 그린다.

**건드린 파일**: `web/sim/index.html`(사전 + `renderStatic()` + 상태/로그 호출),
`sim/run_page.mjs`(로딩 문자열의 잘못된 코드포인트 수정, 한국어/English 전환 검사),
`tools/verify_final.py`(warn 경로 검사가 새 구조를 따르게 수정).
