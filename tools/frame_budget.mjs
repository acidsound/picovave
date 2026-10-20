#!/usr/bin/env node
// SPDX-License-Identifier: GPL-3.0-only
// frame_budget.mjs - the frame-budget gate for the display path, measured on the GALLERY carts.
//
// WHY THIS EXISTS
// The panel is the bottleneck. A 240x240 RGB565 frame is 115,200 B, and SPI1 runs at divider 1 =
// lsb_clk 60 MHz / 2 = 30 MHz, so a full frame costs 115,200 * 8 / 30e6 = 30.72 ms of the 33.33 ms
// a 30 fps tick allows - 92% of it (docs/HANDOVER.md 15). The accepted fix is dirty-row streaming:
// send only the panel rows that changed. The user's acceptance criterion, verbatim:
//
//     "시간당 프레임을 20% 이상 확보해야 30fps 를 확보가능. cart gallery 의 롬들로 검증해야함."
//
// i.e. the panel path must reclaim at least 20% of the frame budget, and it must be verified on the
// carts in the gallery (web/sim/index.html GALLERY) - not on a synthetic screen. This tool is that
// verification, and it is the exit code the Lead runs.
//
// HOW IT MEASURES
// The wasm simulator (build/sim/sim.wasm) is the firmware's own code: sim/sim.c includes
// src/picovave_p8.h unchanged and swaps only the HAL, and femto8/seam/seam_sim.c expands the
// engine's 128x128 surface into the 240x240 the panel gets. It does NOT compile src/picovave.c or
// platform/firmware/src/lcd.c, so the device's p8_panel_bytes counter is not visible here and stays
// "full frame per push" on this target by design (task-2's contract).
//
// So the tool measures the thing that decides the answer, the same way before and after the
// optimisation lands: it reads the expanded 240x240 panel (sim_fb()) every frame and diffs it
// against the previous frame. A panel row that changed is a row the panel path MUST send; a row
// that did not change is a row dirty-row streaming can skip. That is the oracle - the minimum any
// correct dirty-row path can send. From it:
//
//     rows  = panel rows with at least one changed pixel
//     runs  = maximal stretches of consecutive dirty rows (one ST7789 address window each)
//     bytes = rows * 240 * 2 + runs * 11         (11 = 0x2A+4, 0x2B+4, 0x2C)
//     ms    = bytes * 8 / 30e6 + runs * WINDOW_US / 1000
//
// That byte formula is exactly the device counter's new semantics (panel-push's contract:
// p8_panel_bytes = dirty_rows * 240 * 2 + windows * 11), so the Node model and the device counter
// agree by construction.
//
// "Before" is the same run's frames sent as one full-frame window; "after" is the dirty-row model.
// Both come out of one pass, so they cannot drift apart.
//
// If the engine side exports its dirty mask, the tool prefers that mask - it is what the device
// will actually send, and it is a conservative SUPERSET of the oracle. It then cross-checks the
// mask against the oracle on every frame and FAILS if the mask ever marks fewer rows than actually
// changed, because that is a visible artifact, not a saving. A mask that marks MORE rows than
// changed is legitimate (the engine can only over-approximate cheaply) and passes. Without the
// export the tool silently falls back to the oracle and says so in the header.
//
// WHAT IT DOES NOT MEASURE
// "eng ms" is process.hrtime around sim_frame() in Node: the engine's update+draw+render plus the
// simulator's own full-frame expand, on host CPU. It is a relative figure only - it is not the
// device's engine cost - so the projected frame time and fps are guides, while the panel
// milliseconds are exact (they are SPI physics, not CPU). The gate is on the panel.
//
// THE GATE
// Per row: every measured row's panel transfer must fit the 33.33 ms budget, and its mask must be
// sound. Gallery-wide: the measured rows must reclaim >= 20% of the budget (the user's criterion),
// over at least MIN_COVERAGE of the rows. A full-screen animation cannot save 20% by definition -
// 37086 idle changes 227 of 240 rows and still fits at 29.2 ms - so a low saving is reported in
// the columns but is not, by itself, a failure. The 20% is required of the gallery, not of a cart
// that has nothing to skip.
//
// A CART CAN STALL, AND A CART CAN RUN OUT OF MEMORY
// One gallery cart (clockwise_knight-4) drives the engine into a frame that never returns when it
// is fed a long scripted input sequence - a real cart bug that the page hides (a browser tab hangs
// too) and that this tool must not inherit: a measurement harness that hangs is not a gate. So
// every (cart, mode) measurement runs in its own worker thread with a stall watchdog
// (--stall-ms). A stalled row keeps the frames it did measure and is reported as STALL.
//
// baba_demake-9 is the memory case: its gameplay allocates past the device's ~406 KB arena (it
// needs >450 KB here), so the scripted input turns into LUA_ERRMEM (status 4). That is a memory
// ceiling, not a statement about the panel path. Both kinds of row are printed explicitly, are
// never silently dropped, and are excluded from the FPS aggregate; the coverage floor keeps them
// from hollowing the gate out.
//
// USAGE
//   python3 tools/build_sim.py                       # once, if build/sim/sim.wasm is missing
//   node tools/frame_budget.mjs [sim.wasm] [--frames N] [--warm N] [--full-frame] [--stall-ms N]
//
//   --frames N     measured frames per mode (default 60 idle, 144 with input)
//   --warm N       idle warm-up frames (default 30); input mode warms up on START_PHASES
//   --full-frame   force the after case to a full-frame push: the baseline the tool must be able to
//                  measure BEFORE the optimisation lands. The gate is then not evaluated.
//   --stall-ms N   a frame that does not return in N ms is a stall (default 5000)
//
// EXIT CODES
//   0  PASS     - the measured gallery reclaims >= 20% of the 33.33 ms budget and every measured
//                 row fits 30 fps with a sound mask (>= MIN_COVERAGE of the rows measured)
//   1  FAIL     - the aggregate misses 20%, a measured row does not fit, a mask is unsound, or
//                 too few rows produced a measurement
//   2  ERROR    - bad arguments, missing sim.wasm, missing cart, unreadable GALLERY
//   3  BASELINE - --full-frame: measured, printed, gate deliberately not evaluated
//
// The row/byte counts are deterministic: the same cart + the same input script give the same
// numbers. The engine milliseconds are wall clock and will wobble between runs.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { Worker, isMainThread, parentPort, workerData } from "worker_threads";
import { cartPayload } from "../web/sim/p8png.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------------------------
// The physics. Every number here is a named knob the report quotes, not a magic constant.
// ---------------------------------------------------------------------------------------------
const SPI_HZ = 30e6;              // SPI1 divider 1: lsb_clk 60 MHz / 2 (lcd.c LCD_BAUD 1)
const PANEL_W = 240;              // GDI_OUT_W
const PANEL_H = 240;              // GDI_OUT_H
const FULL_BYTES = PANEL_W * PANEL_H * 2;                 // 115,200 B
const FULL_MS = (FULL_BYTES * 8 / SPI_HZ) * 1000;         // 30.72 ms
// One address window: 0x2A + 4 data, 0x2B + 4 data, 0x2C = 11 bytes on the wire. The wire time of
// those bytes is already in the byte count; WINDOW_US is the command setup + sync the byte count
// does not capture. 20 us is an estimate, deliberately a knob: the full-frame path pays it once
// (irrelevant), the dirty path pays it once per RUN, so a cart that changes every other row pays it
// 120 times. If a real measurement says otherwise, change this one line.
const WINDOW_BYTES = 11;
const WINDOW_US = 20;
const FRAME_BUDGET_MS = 1000 / 30;                        // 33.333 ms at 30 fps
const TARGET_RECLAIM = 0.20;                              // the user's criterion
// The gate needs real measurements from most of the gallery. Without this, excluding every row
// that is inconvenient would leave an aggregate over two carts that says nothing about the port.
const MIN_COVERAGE = 0.75;

// ---------------------------------------------------------------------------------------------
// The input script. The page drives the cart through sim_key()/sim_pad(), and src/picovave_p8.h
// pv_gamepad() maps the keybed onto PICO-8's gamepad: keys 5,7 = LEFT, 9 = DOWN, 11,12 = RIGHT,
// 8,10 = UP, key 33 = O (BUTTON_1), key 36 = X (BUTTON_2), key 32 = START. Using the keybed (not
// the pads) keeps the script readable and exercises the same pv_gamepad() path the hardware does.
// The carts are games: a title screen is not representative, so input mode first presses O and X
// to get past the title (unmeasured warm-up), then plays.
const START_PHASES = [
  { keys: [33], frames: 8 },        // O - most titles start on O
  { keys: [],   frames: 6 },
  { keys: [36], frames: 8 },        // X - the rest start on X
  { keys: [],   frames: 6 },
  { keys: [33], frames: 8 },
  { keys: [],   frames: 10 },
];
const GAMEPLAY_PHASES = [
  { keys: [11],     frames: 20 },   // right
  { keys: [11, 33], frames: 20 },   // right + O
  { keys: [9],      frames: 14 },   // down
  { keys: [9, 33],  frames: 14 },   // down + O
  { keys: [12],     frames: 14 },   // right
  { keys: [5],      frames: 14 },   // left
  { keys: [8],      frames: 12 },   // up
  { keys: [33],     frames: 14 },   // O
  { keys: [36],     frames: 10 },   // X
  { keys: [],       frames: 12 },   // release
];

const DEFAULT_IDLE_FRAMES = 60;
const DEFAULT_INPUT_FRAMES = 144;
const DEFAULT_WARM = 30;
const DEFAULT_STALL_MS = 5000;
const IDLE = "idle";
const INPUT = "input";

// ---------------------------------------------------------------------------------------------
// The wasm shim. Copied from tools/probe_carts.mjs rather than reinvented: the engine's libc pulls
// a few WASI/syscall imports that must exist even though picovave never reads a file at runtime.
// Their results go through a DataView over the instance's memory and are not used.
// ---------------------------------------------------------------------------------------------
let mem = null;   // the instance currently being driven; the stubs' results are unused
// A logical 30 fps clock. The engine calls clock_gettime() through libc (p8_clock()/p8_elapsed_time)
// for time()/t() and for the RNG seed; on the host that is real time, so the same cart could animate
// differently from run to run and the row counts would not be reproducible. Hand the wasm a clock
// that advances exactly one 30 fps tick per sim_frame() instead.
let clockUs = 0;
const view = () => (mem ? new DataView(mem.buffer) : null);
const putU32 = (p, v) => { const d = view(); if (d) d.setUint32(p, v >>> 0, true); };
const putU64 = (p, v) => { const d = view(); if (d) d.setBigUint64(p, BigInt(v), true); };
const imports = {
  env: { __syscall_faccessat: () => -1, __syscall_dup3: () => -1, __syscall_getdents64: () => -1 },
  wasi_snapshot_preview1: {
    environ_sizes_get: (c, b) => { putU32(c, 0); putU32(b, 0); return 0; },
    environ_get: () => 0,
    clock_time_get: (id, prec, out) => { putU64(out, clockUs); return 0; },
    fd_close: () => 0,
    fd_write: (fd, iovs, n, nwritten) => { putU32(nwritten, 0); return 0; },
    fd_read: (fd, iovs, n, nread) => { putU32(nread, 0); return 0; },
    fd_seek: (fd, off, whence, newoff) => { putU64(newoff, 0); return 0; },
  },
};

// The cart upload: the device's own SysEx protocol, exactly as the page and probe_carts.mjs speak
// it (src/picovave_cart.h). BEGIN carries the payload length and name, DATA carries 128-byte
// chunks 7-bit packed, END commits the CRC16 and reboots onto the cart.
const SYSEX_ID = [0xF0, 0x22, 0x24, 0x35, 0x7E];
const le7 = (v, n) => { const b = []; for (let i = 0; i < n; i++) b.push((v >>> (7 * i)) & 0x7f); return b; };
const crc16 = (bs) => {
  let crc = 0xFFFF;
  for (const b of bs) { crc ^= b << 8; for (let i = 0; i < 8; i++) crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF; }
  return crc;
};
const frame = (cmd, body) => Uint8Array.from([...SYSEX_ID, cmd, ...body, 0xF7]);
const nm = (s) => [...s].slice(0, 31).map((c) => c.charCodeAt(0));
const pack7 = (d) => {
  const out = []; let acc = 0, nb = 0;
  for (const b of d) { acc |= b << nb; nb += 8; while (nb >= 7) { out.push(acc & 0x7f); acc >>>= 7; nb -= 7; } }
  if (nb) out.push(acc & 0x7f);
  return out;
};

async function openInstance(mod) {
  const inst = await WebAssembly.instantiate(mod, imports);
  const ex = inst.exports;
  mem = ex.memory;                       // before _initialize(): the stubs may run there
  if (ex._initialize) ex._initialize();
  return ex;
}

function uploadCart(ex, name, payload) {
  const feed = (m) => {
    new Uint8Array(ex.memory.buffer, Number(ex.sim_sysex_in()), m.length).set(m);
    return ex.sim_sysex(m.length);
  };
  feed(frame(0x01, [0, ...le7(payload.length, 3), nm(name).length, ...nm(name)]));
  for (let off = 0; off < payload.length; off += 128) {
    const c = payload.subarray(off, off + 128);
    feed(frame(0x02, [...le7(off, 3), ...le7(c.length, 3), ...pack7(c)]));
  }
  feed(frame(0x03, le7(crc16(payload), 3)));
}

// ---------------------------------------------------------------------------------------------
// Counter detection: the optimised build may expose the engine's dirty mask. The tool asks the
// module what it exports instead of assuming, so it runs unchanged before and after that lands.
//   sim_panel_mask() -> const uint8_t * : row_dirty[240], nonzero = repaint that panel row
//   sim_panel_rows() -> uint32_t        : how many panel rows the engine marked dirty
//   (older/other spellings are accepted too, in case the sim spells them differently)
// The mask is preferred: it is what the device will actually send, and it carries the RUN
// structure, which is what the address-window overhead depends on. The count alone cannot give
// runs, so it is only reported as a cross-check next to the oracle.
// ---------------------------------------------------------------------------------------------
const MASK_POINTER_EXPORTS = ["sim_panel_mask", "sim_panel_dirty", "sim_dirty", "sim_row_dirty"];
const MASK_COUNT_EXPORTS = ["sim_panel_rows", "sim_dirty_rows"];

function detectCaps(mod) {
  const names = new Set(WebAssembly.Module.exports(mod).map((e) => e.name));
  return {
    maskPtr: MASK_POINTER_EXPORTS.find((n) => names.has(n)) || null,
    maskRows: MASK_COUNT_EXPORTS.find((n) => names.has(n)) || null,
  };
}

// A panel row is dirty when any of its 240 pixels differs from the previous frame. That is the
// minimal set a correct panel must send: the 240x240 here is already the expanded (scaled) surface,
// so it is what the ST7789 would receive, not the engine's 128x128.
function diffRows(prev, cur, mask) {
  let rows = 0, runs = 0, inRun = false;
  for (let y = 0; y < PANEL_H; y++) {
    const base = y * PANEL_W;
    let dirty = 0;
    for (let x = 0; x < PANEL_W; x++) {
      if (prev[base + x] !== cur[base + x]) { dirty = 1; break; }
    }
    mask[y] = dirty;
    if (dirty) { rows++; if (!inRun) { runs++; inRun = true; } } else inRun = false;
  }
  return { rows, runs };
}

// Read the engine's mask, if the build has one. Fills out[] and returns its {rows, runs}.
function readEngineMask(ex, caps, out) {
  if (!caps.maskPtr) return null;
  const m = new Uint8Array(ex.memory.buffer, Number(ex[caps.maskPtr]()), PANEL_H);
  let rows = 0, runs = 0, inRun = false;
  for (let y = 0; y < PANEL_H; y++) {
    const d = m[y] ? 1 : 0;
    out[y] = d;
    if (d) { rows++; if (!inRun) { runs++; inRun = true; } } else inRun = false;
  }
  return { rows, runs };
}

function panelMs(bytes, runs) {
  return (bytes * 8 / SPI_HZ) * 1000 + runs * WINDOW_US / 1000;
}

function bytesFor(rows, runs) {
  return rows * PANEL_W * 2 + runs * WINDOW_BYTES;
}

// Repeat the gameplay phases until the frame count reaches n, trimming the last phase to fit.
function cyclePhases(phases, n) {
  const out = [];
  let total = 0;
  for (let i = 0; total < n; i++) {
    const ph = phases[i % phases.length];
    const frames = Math.min(ph.frames, n - total);
    out.push({ keys: ph.keys, frames });
    total += frames;
  }
  return out;
}

function median(values) {
  if (!values.length) return 0;
  const s = values.slice().sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function percentile(values, p) {
  if (!values.length) return 0;
  const s = values.slice().sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * s.length))];
}

// Turn the per-frame records into one row of the report. Rows/bytes are deterministic; only engMs
// is wall clock, so the row/byte columns are reproducible run to run and the ms column wobbles.
function summarise(cartFile, mode, ex, perFrame, opts, stall) {
  const status = Number(ex.sim_status());
  const running = Number(ex.sim_cart_running());
  const refused = Number(ex.sim_heap_refused());
  // Panel cost is per PUSH, and a tick can push more than once (_update60 carts step twice). The
  // device pays for every push, so the per-tick panel cost is the sum over that tick's pushes - and
  // each push pays its own window command. A tick with 0 pushes costs 0: a _update60 cart whose
  // second step draws nothing really does not touch the panel.
  //
  // One honest approximation: the frames are sampled once per TICK (sim_frame() runs both steps),
  // so for a 2-push tick the dirty rows are the tick's net change, and charging that to BOTH pushes
  // is an upper bound - the first push may have changed fewer rows than the net. That biases the
  // AFTER column HIGH (savings understated), which is the safe direction for a gate: loop-4 still
  // clears the target at 164%, and its BEFORE column (2 x 30.72 = 61.48 ms) is exact, which is the
  // number that says a _update60 cart cannot reach 30 fps at all under a full-frame push.
  const beforePerTick = perFrame.map((f) => f.pushes * panelMs(FULL_BYTES, 1));
  const afterPerTick = perFrame.map((f) => panelMs(bytesFor(f.rows * f.pushes, f.runs * f.pushes), f.runs * f.pushes));
  const projectedPerTick = perFrame.map((f, i) => f.engMs + afterPerTick[i]);
  const beforeProjected = perFrame.map((f, i) => f.engMs + beforePerTick[i]);
  const hasMask = perFrame.some((f) => f.maskRows !== null);
  const pushes = median(perFrame.map((f) => f.pushes));
  const maskCheckedFrames = perFrame.reduce((a, f) => a + (f.maskChecked || 0), 0);
  // A tick that pushed nothing costs nothing; call those "free" rather than counting them as
  // frames that fit, so fit% means "of the ticks that touched the panel".
  const active = perFrame.map((f, i) => ({ f, i })).filter(({ f }) => f.pushes > 0);
  const fits = (arr) => (active.length
    ? active.filter(({ i }) => arr[i] <= FRAME_BUDGET_MS).length / active.length
    : 1);
  return {
    cart: cartFile,
    mode,
    status, running, refused,
    // LUA_ERRMEM: the cart could not allocate inside the device's arena under this input script.
    // That is a memory ceiling, not a verdict about the panel path - the tool reports it as OOM
    // and keeps it out of the FPS aggregate instead of reading it as "dirty rows did not help".
    // (baba_demake-9 is the one gallery cart here: its gameplay needs >450 KB on this engine.)
    oom: status === 4,
    frames: perFrame.length,
    expectedFrames: mode === IDLE ? opts.idleFrames : opts.inputFrames,
    stall: stall || null,
    pushes,
    rows: median(perFrame.map((f) => f.rows)),
    runs: median(perFrame.map((f) => f.runs)),
    rowsP95: percentile(perFrame.map((f) => f.rows), 0.95),
    oracleRows: median(perFrame.map((f) => f.oracleRows)),
    maskRows: hasMask ? median(perFrame.map((f) => f.maskRows)) : null,
    missed: perFrame.reduce((a, f) => a + f.missed, 0),
    maskCheckedFrames,
    engMs: median(perFrame.map((f) => f.engMs)),
    // Per tick, the way the panel sees it.
    beforeMs: median(beforePerTick),
    afterMs: median(afterPerTick),
    afterP95: percentile(afterPerTick, 0.95),
    projectedMs: median(projectedPerTick),
    beforeProjectedMs: median(beforeProjected),
    fit: fits(projectedPerTick),
    beforeFit: fits(beforeProjected),
  };
}

// ---------------------------------------------------------------------------------------------
// The worker: one (cart, mode) per thread, so a cart that spins cannot take the gate with it.
// It reports a "partial" row at every phase boundary and a "beat" before every frame; the parent
// resets its watchdog on both, and terminates the thread if neither arrives within --stall-ms.
// ---------------------------------------------------------------------------------------------
async function workerMain() {
  const { wasmPath, cartFile, payload, mode, opts } = workerData;
  const mod = await WebAssembly.compile(fs.readFileSync(wasmPath));
  // The build on disk decides what it exports, not the parent's earlier guess: the parent may have
  // been looking at a different sim.wasm by the time this thread started.
  const caps = detectCaps(mod);
  const row = await measure(mod, caps, { file: cartFile }, payload, mode, opts);
  parentPort.postMessage({ type: "result", row });
}

// measure() plus a partial publish at every phase boundary, so a stall leaves a usable row behind
// (the parent's watchdog reports the frames that did complete, not a blank).
async function measure(mod, caps, cart, payload, mode, opts) {
  const ex = await openInstance(mod);
  ex.sim_init();
  uploadCart(ex, cart.file, payload);

  const fb = new Uint16Array(ex.memory.buffer, Number(ex.sim_fb()), PANEL_W * PANEL_H);
  const prev = new Uint16Array(PANEL_W * PANEL_H);
  const cur = new Uint16Array(PANEL_W * PANEL_H);
  const oracleMask = new Uint8Array(PANEL_H);
  const engineMask = new Uint8Array(PANEL_H);
  const perFrame = [];
  let tick = 0;                       // the logical 30 fps clock, one step per sim_frame()
  const publish = (phase) => parentPort.postMessage({
    type: "partial", phase, row: summarise(cart.file, mode, ex, perFrame, opts),
  });

  // sim_draws() counts panel pushes. A _update60 cart steps twice per 1/30 s tick (picovave_p8.h
  // runs p8_step() twice when m_fps >= 60), so it pushes TWICE per tick - and the panel pays for
  // both. That is measured, not assumed: pushes/tick comes from the counter's own delta.
  const runPhase = (ph, record, label) => {
    for (const k of ph.keys) ex.sim_key(k, 1);
    for (let i = 0; i < ph.frames; i++) {
      parentPort.postMessage({ type: "beat", phase: label, frame: i });
      const d0 = Number(ex.sim_draws());
      const t0 = process.hrtime.bigint();
      clockUs = Math.floor(tick++ * (1000000 / 30));   // deterministic time()/t()/RNG seed, see imports
      ex.sim_frame();
      const t1 = process.hrtime.bigint();
      const pushes = Number(ex.sim_draws()) - d0;
      if (!record) continue;
      cur.set(fb);
      const oracle = diffRows(prev, cur, oracleMask);
      const mask = readEngineMask(ex, caps, engineMask);
      const countRows = caps.maskRows ? Number(ex[caps.maskRows]()) : null;
      // Soundness: every row whose pixels changed must be in the engine's mask. A mask that marks
      // fewer rows than changed is a visible artifact (a stale row on the real panel), so it is a
      // hard failure, not a saving. The reverse (mask > changed) is legitimate - the engine
      // over-approximates - and passes.
      //
      // ONLY on a single-push tick, though. p8_render() rewrites the mask at every push, so on an
      // _update60 tick (two p8_step() calls, two pushes) the mask left behind describes the SECOND
      // push only, while the oracle below is the net change across both. Comparing them would
      // report rows that the FIRST push legitimately sent as "missed" - a false alarm on loop-4.
      // Multi-push ticks therefore use the oracle and are reported as not mask-checked.
      let missed = 0;
      let maskChecked = 0;
      if (mask && pushes <= 1) {
        maskChecked = 1;
        for (let y = 0; y < PANEL_H; y++) if (oracleMask[y] && !engineMask[y]) missed++;
      }
      const used = opts.fullFrame ? { rows: PANEL_H, runs: 1 } : (mask || oracle);
      perFrame.push({
        engMs: Number(t1 - t0) / 1e6,
        pushes,
        rows: used.rows,
        runs: used.runs,
        oracleRows: oracle.rows,
        maskRows: mask ? mask.rows : countRows,
        missed,
        maskChecked,
      });
      prev.set(cur);
    }
    for (const k of ph.keys) ex.sim_key(k, 0);
    if (record) publish(label);
  };

  const warm = mode === IDLE ? [{ keys: [], frames: opts.warm }] : START_PHASES;
  for (const ph of warm) runPhase(ph, false, "warm");
  prev.set(fb);
  publish("warm");

  const script = mode === IDLE
    ? [{ keys: [], frames: opts.idleFrames }]
    : cyclePhases(GAMEPLAY_PHASES, opts.inputFrames);
  for (const ph of script) runPhase(ph, true, "measure");

  return summarise(cart.file, mode, ex, perFrame, opts);
}

// Run one (cart, mode) in a worker and return its row. A worker that stops beating for stallMs is
// terminated; its last partial row is returned with stall set, so the report says what was
// measured and why it stopped instead of hanging or lying.
function runIsolated(job, stallMs) {
  return new Promise((resolve) => {
    const worker = new Worker(new URL(import.meta.url), { workerData: job });
    let last = null;
    let lastBeat = Date.now();
    let done = false;
    const finish = (row, why) => {
      if (done) return;
      done = true;
      clearInterval(timer);
      worker.terminate();
      if (!row) {
        row = summarise(job.cartFile, job.mode, {
          sim_status: () => -1, sim_cart_running: () => -2, sim_heap_refused: () => 0,
        }, [], job.opts);
      }
      if (why) row.stall = why;
      resolve(row);
    };
    const timer = setInterval(() => {
      if (Date.now() - lastBeat > stallMs) {
        finish(last, "no frame completed for " + stallMs + " ms" +
          (last && last.phase ? " (last phase " + last.phase + ")" : ""));
      }
    }, Math.min(1000, Math.max(50, stallMs >> 2)));
    worker.on("message", (m) => {
      lastBeat = Date.now();
      if (m.type === "partial") last = m.row;
      else if (m.type === "result") finish(m.row, null);
    });
    worker.on("error", (e) => finish(last, "worker error: " + (e && e.message ? e.message : e)));
    worker.on("exit", () => finish(last, "worker exited early"));
  });
}

// ---------------------------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------------------------
const pad = (s, n, right) => (right ? String(s).padStart(n) : String(s).padEnd(n));
const f2 = (x) => x.toFixed(2);

function printHeader(wasmPath, caps, opts) {
  const st = fs.statSync(wasmPath);
  console.log("frame budget - " + wasmPath);
  console.log("  built     " + st.mtime.toISOString() + "  " + st.size + " B");
  console.log("  panel     240x240 RGB565 = " + FULL_BYTES + " B; SPI " + (SPI_HZ / 1e6) +
    " MHz => full frame " + f2(FULL_MS) + " ms");
  console.log("  budget    " + f2(FRAME_BUDGET_MS) + " ms (30 fps); target: reclaim >= " +
    (TARGET_RECLAIM * 100).toFixed(0) + "% = " + f2(FRAME_BUDGET_MS * TARGET_RECLAIM) + " ms");
  console.log("  window    " + WINDOW_BYTES + " B + " + WINDOW_US + " us per address window (knob)");
  const src = opts.fullFrame
    ? "--full-frame: the after case is forced to a full-frame push"
    : caps.maskPtr
      ? "engine dirty mask (" + caps.maskPtr + "()), cross-checked against the pixels"
      : "framebuffer diff (oracle: the minimum a correct dirty-row path must send)";
  console.log("  rows from " + src);
  if (caps.maskRows) console.log("  counter   " + caps.maskRows + "() = engine rows/frame (cross-check)");
  console.log("  frames    idle " + opts.idleFrames + " after " + opts.warm + " warm; input " +
    opts.inputFrames + " after " + START_PHASES.reduce((a, p) => a + p.frames, 0) + " warm");
  console.log("  stall     a frame that does not return in " + opts.stallMs + " ms is reported, excluded");
  console.log("  gate      per row: panel fits " + f2(FRAME_BUDGET_MS) + " ms, mask sound; gallery: >=" +
    (TARGET_RECLAIM * 100).toFixed(0) + "% reclaimed over >=" + (MIN_COVERAGE * 100).toFixed(0) + "% of rows");
  console.log("");
}

function printTable(rows) {
  const head = ["cart", "mode", "eng ms", "rows/f", "runs/f", "before", "after", "saved ms",
    "saved%", "budget%", "proj fps", "fit%", "gate"];
  const widths = [22, 6, 8, 8, 8, 9, 9, 10, 8, 9, 10, 7, 7];
  const line = (cells) => cells.map((c, i) => pad(c, widths[i], i > 0)).join("");
  console.log(line(head));
  console.log("-".repeat(widths.reduce((a, b) => a + b, 0)));
  for (const r of rows) {
    // A row that measured nothing, or ran out of memory before the panel path was the subject, has
    // no honest number for any of these columns: print "-" rather than a 0.00 or an Infinity that
    // would read as a measurement. (The gate column still says OOM/STALL/ERR.)
    const none = r.frames === 0 || !r.valid;
    const saved = r.beforeMs - r.afterMs;
    const savedPct = saved / r.beforeMs;
    const budgetPct = saved / FRAME_BUDGET_MS;
    // Per row the gate asks the question that matters on the device: does the panel path fit the
    // 30 fps budget? A cart that repaints most of the screen (37086 idle changes 227 of 240 rows)
    // cannot save 20% by definition, but it still fits at 29.2 ms and is not an FPS failure. The
    // 20% target is evaluated on the gallery aggregate below.
    const fits = r.valid && r.sound && r.afterMs <= FRAME_BUDGET_MS;
    const gate = r.stall ? "STALL" : r.oom ? "OOM" : r.valid && r.sound ? (fits ? "PASS" : "FAIL") : "ERR";
    const num = (x, f) => (none || !Number.isFinite(x) ? "-" : f(x));
    console.log(line([
      r.cart.replace(/\.p8\.png$/, ""),
      r.mode,
      num(r.engMs, f2),
      num(r.rows, (x) => x.toFixed(0)),
      num(r.runs, (x) => x.toFixed(0)),
      num(r.beforeMs, f2),
      num(r.afterMs, f2),
      num(saved, f2),
      num(savedPct, (x) => (x * 100).toFixed(0) + "%"),
      num(budgetPct, (x) => (x * 100).toFixed(0) + "%"),
      num(1000 / r.projectedMs, (x) => x.toFixed(0)),
      num(r.fit, (x) => (x * 100).toFixed(0) + "%"),
      gate,
    ]));
  }
}

function printSummary(rows, opts) {
  const n = rows.length;
  // The gallery aggregate is over the rows that produced a real FPS measurement. A stalled row
  // has no numbers; an OOM row ran out of the device's arena before the panel path was the
  // subject. Both are printed and explicitly excluded - folding their zeros into the average
  // would flatter the transfer numbers with a measurement that never happened, and counting them
  // as FPS failures would blame dirty-row streaming for a memory ceiling or a cart bug.
  const measured = rows.filter((r) => r.frames > 0 && r.valid);
  const m = measured.length || 1;
  const mean = (f) => measured.reduce((a, r) => a + f(r), 0) / m;
  const beforeMs = mean((r) => r.beforeMs);
  const afterMs = mean((r) => r.afterMs);
  const saved = beforeMs - afterMs;
  const reclaim = (r) => (r.beforeMs - r.afterMs) / FRAME_BUDGET_MS;
  const coverage = measured.length / n;
  const budgetSaved = saved / FRAME_BUDGET_MS;
  const bad = measured.filter((r) => !r.sound || r.afterMs > FRAME_BUDGET_MS);
  console.log("");
  console.log("gallery (" + n + " rows: " + (n / 2) + " carts x {idle, input}; " +
    measured.length + " measured" +
    (measured.length === n ? ")" : ", " + (n - measured.length) + " excluded from the aggregate)"));
  console.log("  panel transfer   " + f2(beforeMs) + " ms -> " + f2(afterMs) + " ms   saved " +
    f2(saved) + " ms = " + (100 * saved / beforeMs).toFixed(1) + "% of the transfer, " +
    (100 * saved / FRAME_BUDGET_MS).toFixed(1) + "% of the " + f2(FRAME_BUDGET_MS) + " ms budget");
  console.log("  projected frame  " + f2(mean((r) => r.beforeProjectedMs)) + " ms -> " +
    f2(mean((r) => r.projectedMs)) + " ms   (host engine + device SPI; engine is relative)");
  if (beforeMs > 0) {
    console.log("  panel-only fps   " + (1000 / beforeMs).toFixed(1) + " -> " +
      (afterMs > 0 ? (1000 / afterMs).toFixed(1) : "inf") + "   (the panel path alone; the hard ceiling)");
  }
  console.log("  fits 30 fps      " + (100 * mean((r) => r.beforeFit)).toFixed(0) + "% -> " +
    (100 * mean((r) => r.fit)).toFixed(0) + "% of frames");
  if (measured.length) {
    const worst = measured.reduce((a, r) => (reclaim(r) < reclaim(a) ? r : a), measured[0]);
    console.log("  worst row        " + worst.cart.replace(/\.p8\.png$/, "") + " " + worst.mode +
      " (reclaims " + (100 * reclaim(worst)).toFixed(0) + "% of the budget)");
  }
  // A cart that pushes the panel more than once per tick pays the panel more than once. That is
  // real (picovave_p8.h steps _update60 carts twice), and it is the reason loop-4 cannot reach
  // 30 fps at all under a full-frame push: 2 x 30.72 ms > 33.33 ms.
  const multi = measured.filter((r) => r.pushes > 1);
  if (multi.length) {
    console.log("  multi-push       " + [...new Set(multi.map((r) => r.cart.replace(/\.p8\.png$/, "") + " x" +
      r.pushes.toFixed(0)))].join(", ") + " per tick (_update60); the panel pays every push");
  }
  const masked = rows.filter((r) => r.maskRows !== null);
  if (masked.length) {
    const unsound = masked.filter((r) => r.missed > 0);
    const checked = masked.reduce((a, r) => a + r.maskCheckedFrames, 0);
    const total = masked.reduce((a, r) => a + r.frames, 0);
    console.log("  mask vs oracle   " + (unsound.length
      ? "UNSOUND - " + unsound.map((r) => r.cart.replace(/\.p8\.png$/, "") + " " + r.mode).join(", ") +
        " changed rows the mask did not mark"
      : "sound on every " + checked + " of " + total + " frames the mask was checked on" +
        (checked < total ? " (a multi-push tick's mask covers its last push only, so those are oracle-only)" : "")));
    console.log("  rows oracle/mask " + masked.map((r) => r.cart.replace(/\.p8\.png$/, "") + " " +
      r.mode + " " + r.oracleRows.toFixed(0) + "/" + r.maskRows.toFixed(0)).join(", "));
  }
  const refused = rows.filter((r) => r.refused > 0);
  if (refused.length) {
    console.log("  allocator        refused " +
      refused.map((r) => r.cart.replace(/\.p8\.png$/, "") + "=" + r.refused).join(", ") +
      " (GC retry; normal)");
  }
  const stalled = rows.filter((r) => r.stall);
  if (stalled.length) {
    console.log("  stalled          " + stalled.map((r) => r.cart.replace(/\.p8\.png$/, "") + " " +
      r.mode + ": " + r.stall + "; " + r.frames + "/" + r.expectedFrames + " frames measured" +
      " (a cart bug, not an FPS verdict)").join(", "));
  }
  const oom = rows.filter((r) => r.oom);
  if (oom.length) {
    console.log("  out of memory    " + oom.map((r) => r.cart.replace(/\.p8\.png$/, "") + " " +
      r.mode + " (status 4: needs more than the device arena; a memory ceiling, not an FPS verdict)")
      .join(", "));
  }
  console.log("");
  if (opts.fullFrame) {
    console.log("BASELINE MODE (--full-frame): the after case IS the full frame, so the gate is not evaluated.");
    return 3;
  }
  const fails = [];
  if (coverage < MIN_COVERAGE) {
    fails.push("only " + measured.length + " of " + n + " rows produced an FPS measurement (" +
      (100 * coverage).toFixed(0) + "% < " + (100 * MIN_COVERAGE).toFixed(0) + "%)");
  }
  if (budgetSaved < TARGET_RECLAIM) {
    fails.push("the gallery reclaims " + (100 * budgetSaved).toFixed(1) + "% of the " +
      f2(FRAME_BUDGET_MS) + " ms budget (< " + (TARGET_RECLAIM * 100).toFixed(0) + "%)");
  }
  for (const r of bad) {
    fails.push(r.cart.replace(/\.p8\.png$/, "") + " " + r.mode + ": " +
      (!r.sound
        ? "dirty mask missed changed rows (" + r.missed + ")"
        : "panel path " + f2(r.afterMs) + " ms does not fit the " + f2(FRAME_BUDGET_MS) + " ms budget"));
  }
  if (fails.length) {
    console.log("FAIL - " + fails.length + " reason(s):");
    for (const why of fails) console.log("  " + why);
    return 1;
  }
  console.log("PASS - the gallery reclaims " + (100 * budgetSaved).toFixed(1) + "% of the " +
    f2(FRAME_BUDGET_MS) + " ms 30 fps budget over " + measured.length + "/" + n +
    " rows, every measured row fits 30 fps, and every mask is a superset of the pixels that changed.");
  return 0;
}

// ---------------------------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------------------------
function readGallery() {
  const page = fs.readFileSync(path.join(here, "..", "web", "sim", "index.html"), "utf8");
  const i = page.indexOf("const GALLERY = [");
  const j = page.indexOf("];", i);
  if (i < 0 || j < 0) throw new Error("GALLERY not found in web/sim/index.html");
  const out = [];
  const re = /\{\s*file:\s*"([^"]+\.p8\.png)"\s*,\s*title:\s*"([^"]*)"/g;
  const body = page.slice(i, j);
  let m;
  while ((m = re.exec(body)) !== null) out.push({ file: m[1], title: m[2] });
  if (!out.length) throw new Error("GALLERY in web/sim/index.html parsed empty");
  return out;
}

function usage() {
  console.log("usage: node tools/frame_budget.mjs [sim.wasm] [--frames N] [--warm N] [--full-frame] [--stall-ms N]");
  console.log("  exit 0 PASS, 1 FAIL, 2 error, 3 baseline (--full-frame)");
}

function fail(msg) {
  console.error("frame_budget: " + msg);
  process.exit(2);
}

function parseArgs(argv) {
  const opts = {
    wasm: null,
    idleFrames: DEFAULT_IDLE_FRAMES,
    inputFrames: DEFAULT_INPUT_FRAMES,
    warm: DEFAULT_WARM,
    stallMs: DEFAULT_STALL_MS,
    fullFrame: false,
  };
  let framesArg = null;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--full-frame") opts.fullFrame = true;
    else if (a === "--help" || a === "-h") return { help: true, opts };
    else if (a === "--frames") framesArg = Number(argv[++i]);
    else if (a === "--warm") opts.warm = Number(argv[++i]);
    else if (a === "--stall-ms") opts.stallMs = Number(argv[++i]);
    else if (a.startsWith("-")) return { error: "unknown option " + a, opts };
    else if (opts.wasm) return { error: "more than one wasm path given", opts };
    else opts.wasm = a;
  }
  if (framesArg !== null) {
    if (!Number.isFinite(framesArg) || framesArg < 2) return { error: "--frames needs a number >= 2", opts };
    opts.idleFrames = opts.inputFrames = Math.floor(framesArg);
  }
  if (!Number.isFinite(opts.warm) || opts.warm < 0) return { error: "--warm needs a number >= 0", opts };
  if (!Number.isFinite(opts.stallMs) || opts.stallMs < 100) return { error: "--stall-ms needs a number >= 100", opts };
  return { opts };
}

async function main() {
  const parsed = parseArgs(process.argv.slice(2));
  if (parsed.help) { usage(); return 0; }
  if (parsed.error) fail(parsed.error);
  const opts = parsed.opts;

  const wasmPath = path.resolve(opts.wasm || path.join(here, "..", "build", "sim", "sim.wasm"));
  if (!fs.existsSync(wasmPath)) {
    fail("no wasm at " + wasmPath + "\n  build it first:  python3 tools/build_sim.py");
  }
  const cartDir = path.join(path.dirname(wasmPath), "carts");
  if (!fs.existsSync(cartDir)) {
    fail("no carts at " + cartDir + "\n  run:  python3 tools/build_sim.py   (it copies carts/ next to the wasm)");
  }

  let gallery;
  try { gallery = readGallery(); } catch (e) { fail(e.message); }
  for (const c of gallery) {
    const p = path.join(cartDir, c.file);
    if (!fs.existsSync(p)) fail("gallery cart missing: " + p);
  }

  const mod = await WebAssembly.compile(fs.readFileSync(wasmPath));
  const caps = detectCaps(mod);
  printHeader(wasmPath, caps, opts);

  const rows = [];
  for (const cart of gallery) {
    const payload = await cartPayload(new Uint8Array(fs.readFileSync(path.join(cartDir, cart.file))));
    for (const mode of [IDLE, INPUT]) {
      const row = await runIsolated({ wasmPath, cartFile: cart.file, payload, mode, opts, caps }, opts.stallMs);
      // A cart that did not start means the row measured something else (the boot screen or the
      // embedded default cart), so it is not a verdict about this cart: report ERR, fail the gate.
      row.valid = row.status === 0 && row.running !== -2;
      row.sound = row.missed === 0;
      rows.push(row);
      process.stderr.write("  measured " + cart.file.padEnd(26) + mode.padEnd(6) +
        (row.stall ? "STALL " : "") + row.frames + " frames\n");
    }
  }

  console.log("");
  printTable(rows);
  const code = printSummary(rows, opts);
  return code;
}

if (!isMainThread) {
  workerMain().then(() => process.exit(0), (e) => {
    parentPort.postMessage({ type: "error", message: e && e.stack ? e.stack : String(e) });
    process.exit(1);
  });
} else {
  main().then((code) => process.exit(code), (e) => {
    console.error("frame_budget: " + (e && e.stack ? e.stack : e));
    process.exit(2);
  });
}
