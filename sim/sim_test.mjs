// SPDX-License-Identifier: GPL-3.0-only
// Boot the picovave simulator's wasm in Node, exactly as the browser does, and check what it
// produces: that the FEMTO8 engine boots a cart, steps frames, renders to the panel, and reacts
// to the same inputs the page sends.
//
//   node picovave/sim/sim_test.mjs [sim.wasm] [OUT.ppm]
//
// The wasm is STANDALONE_WASM, but the PICO-8 engine's libc pulls a few WASI/syscall imports
// (time, stdio, dirent). picovave never reads a file at runtime (the cart is baked in or arrives
// over the SysEx protocol below, which sim.c parses itself), so they are stubbed here exactly as
// the page stubs them - the imports must exist, their results are not used.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const wasmArg = args.find((a) => a.endsWith(".wasm"));
const wasmPath = wasmArg || path.join(here, "sim.wasm");
const outPath = args.find((a) => !a.endsWith(".wasm")) || null;

const bytes = fs.readFileSync(wasmPath);

let mem = null;
const view = () => new DataView(mem.buffer);
const putU32 = (ptr, v) => view().setUint32(ptr, v >>> 0, true);
const putU64 = (ptr, v) => view().setBigUint64(ptr, BigInt(v), true);

const imports = {
  env: {
    __syscall_faccessat: () => -1,
    __syscall_dup3: () => -1,
    __syscall_getdents64: () => -1,
  },
  wasi_snapshot_preview1: {
    environ_sizes_get: (c, b) => { putU32(c, 0); putU32(b, 0); return 0; },
    environ_get: () => 0,
    clock_time_get: (id, prec, out) => { putU64(out, Date.now() * 1000); return 0; },
    fd_close: () => 0,
    fd_write: (fd, iovs, n, nwritten) => { putU32(nwritten, 0); return 0; },
    fd_read: (fd, iovs, n, nread) => { putU32(nread, 0); return 0; },
    fd_seek: (fd, off, whence, newoff) => { putU64(newoff, 0); return 0; },
  },
};

const { instance } = await WebAssembly.instantiate(bytes, imports);
const ex = instance.exports;
mem = ex.memory;
if (ex._initialize) ex._initialize();

let fails = 0;
const check = (what, ok, extra = "") => {
  console.log("sim: " + what.padEnd(56) + " " + (ok ? "ok" : "FAIL") + (extra ? "   " + extra : ""));
  if (!ok) fails++;
};

// ---------------------------------------------------------------------------------------------
const NAMES = ["sim_init", "sim_frame", "sim_fb", "sim_draws", "sim_ms", "sim_bytes", "sim_boots",
  "sim_wdt", "sim_button", "sim_key", "sim_enc", "sim_pad", "sim_fader", "sim_wheel", "sim_midi",
  "sim_rate", "sim_render", "sim_out_l", "sim_out_r", "sim_pads", "sim_leds",
  "sim_heap_peak", "sim_heap_used", "sim_heap_refused", "sim_device_alloc"];
check("exports the simulator contract", NAMES.every((n) => typeof ex[n] === "function"),
  NAMES.filter((n) => typeof ex[n] !== "function").join(",") || "");

ex.sim_init();
check("sim_init() did not throw", true);
check("booted a frame", ex.sim_boots() >= 1, "boots=" + ex.sim_boots());

const fb = new Uint16Array(ex.memory.buffer, ex.sim_fb(), 240 * 240);
const snap = () => Uint16Array.from(fb);
const nonBlack = (px) => { let n = 0; for (let i = 0; i < px.length; i++) if (px[i]) n++; return n; };

// Run a stretch of frames so the cart's _update/_draw settle and it paints.
for (let i = 0; i < 60; i++) ex.sim_frame();
const a = snap();
check("the cart painted the panel", nonBlack(a) > 0, nonBlack(a) + " lit px");
check("draws reached the display", ex.sim_draws() > 0, "draws=" + ex.sim_draws());
check("bytes reached the display", ex.sim_bytes() >= 240 * 240 * 2, ex.sim_bytes() + " B");

// ---------------------------------------------------------------------------------------------
// Input: the baked default cart (tools/pvcart.py) lights a pixel when btn(n)/btnp(n) is true.
// Press the owner-verified O key (keybed key 33 -> pad 15 -> BUTTON_1) and check the panel
// changes from the idle frame.
const idle = snap();
ex.sim_pad(15, 127);                 // pad 15 == PICO-8 O
for (let i = 0; i < 4; i++) ex.sim_frame();
const pressed = snap();
let changed = 0;
for (let i = 0; i < idle.length; i++) if (idle[i] !== pressed[i]) changed++;
check("an O press changes the panel", changed > 0, changed + " px changed");

ex.sim_pad(15, 0);
ex.sim_pad(8, 127);                  // pad 8 == PICO-8 LEFT
for (let i = 0; i < 4; i++) ex.sim_frame();
const left = snap();
let changed2 = 0;
for (let i = 0; i < pressed.length; i++) if (pressed[i] !== left[i]) changed2++;
check("a LEFT press changes the panel", changed2 > 0, changed2 + " px changed");
ex.sim_pad(8, 0);

// ---------------------------------------------------------------------------------------------
// Audio: the worklet's render entry must produce finite samples at the engine's rate and advance
// frames on the audio clock.
check("audio rate is 44100", ex.sim_rate() === 44100, "rate=" + ex.sim_rate());
const bootsBefore = ex.sim_boots();
ex.sim_render(1024);
ex.sim_render(1024);                   // two quanta > 44100/30 samples, so a frame must advance
const ol = new Float32Array(ex.memory.buffer, ex.sim_out_l(), 1024);
let finite = true, nonzero = 0;
for (let i = 0; i < 1024; i++) { if (!Number.isFinite(ol[i])) finite = false; if (ol[i] !== 0) nonzero++; }
check("sim_render produces finite samples", finite);
check("sim_render advanced the audio clock", ex.sim_boots() > bootsBefore,
  (ex.sim_boots() - bootsBefore) + " frames / quantum");

// ---------------------------------------------------------------------------------------------
// The cart upload: the device's own SysEx protocol, driven exactly as the page drives it. A cart
// is a .p8.png, and p8png.mjs - the page's own module, run here in Node - unwraps it into what
// BEGIN/DATA/END carries: a magic, then the 32,800-byte cooked cart. END commits it to a slot and
// reboots onto it, so the engine has to run what the protocol stored - including the cart's
// PXA-compressed code section, which only femto8's expander (compiled into this wasm exactly as
// the device compiles it) can read. That is the check that the .p8.png path works end to end.
import { cartPayload, cookedFromScript, CART_LEN, COOKED_SIZE, COOKED_MAGIC } from "./p8png.mjs";

// The device's whole heap for the engine and Lua: POOL_LEN 294,912 B (femto8/stub/device + app.ld)
// less the allocator's 1 KiB red zone - the number tools/build_picovave.py prints. sim_heap_peak()
// is the same pool measured here (sim/heap_probe.c), so the two are directly comparable.
const DEVICE_HEAP = 294912 - 1024;

const cartDir = path.join(path.dirname(wasmPath), "carts");
let fixtures = [];
try { fixtures = fs.readdirSync(cartDir).filter((n) => n.endsWith(".p8.png")).sort(); } catch (e) {}
check("real .p8.png carts are served for the upload test", fixtures.length > 0,
  cartDir + " -> " + fixtures.join(" "));

if (fixtures.length) {
  const SYSEX_ID = [0xF0, 0x22, 0x24, 0x35, 0x7E];
  const le7 = (v, n) => { const b = []; for (let i = 0; i < n; i++) b.push((v >>> (7 * i)) & 0x7f); return b; };
  const crc16 = (bytes) => {
    let crc = 0xFFFF;
    for (const b of bytes) { crc ^= b << 8; for (let i = 0; i < 8; i++) crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF; }
    return crc;
  };
  const frame = (cmd, body) => Uint8Array.from([...SYSEX_ID, cmd, ...body, 0xF7]);
  const feed = (msg) => {
    new Uint8Array(ex.memory.buffer, ex.sim_sysex_in(), msg.length).set(msg);
    return ex.sim_sysex(msg.length);
  };
  const reply = () => new Uint8Array(ex.memory.buffer, ex.sim_reply(), ex.sim_reply_len());
  const replyOk = (code) => { const r = reply(); return r.length >= 7 && r[5] === 0x10 && r[6] === code; };
  const nm = (s) => [...s].slice(0, 31).map((c) => c.charCodeAt(0));
  // DATA payloads are 8->7 packed (the page's pack7, and pv_unpack7 in src/picovave_cart.h is the
  // inverse); off and n are RAW counts. The CRC is over the raw payload on both ends, so a
  // committing END doubles as a byte-for-byte proof of the split - and the cooked cart is binary,
  // so that split is the only way it can travel at all.
  const pack7 = (d) => {
    const out = []; let acc = 0, nb = 0;
    for (const b of d) { acc |= b << nb; nb += 8; while (nb >= 7) { out.push(acc & 0x7f); acc >>>= 7; nb -= 7; } }
    if (nb) out.push(acc & 0x7f);
    return out;
  };
  const upload = (slot, name, d) => {
    feed(frame(0x01, [slot, ...le7(d.length, 3), name.length, ...name]));
    for (let off = 0; off < d.length; off += 128) {
      const c = d.subarray(off, off + 128);
      feed(frame(0x02, [...le7(off, 3), ...le7(c.length, 3), ...pack7(c)]));
    }
  };

  // The page's own unwrapping: image bytes -> the payload the wire carries.
  const payloadA = await cartPayload(new Uint8Array(fs.readFileSync(path.join(cartDir, fixtures[0]))));
  check("p8png.mjs unwraps a real .p8.png into the cooked cart",
        payloadA.length === CART_LEN && COOKED_MAGIC.every((v, i) => payloadA[i] === v),
        fixtures[0] + " -> " + payloadA.length + " B");
  check("that payload is binary (the DATA frames have to split 8 bits into 7)",
        payloadA.some((b) => b > 0x7f), "");

  // A text .p8 is not a cart: BEGIN's length is the format gate, so nothing is erased or written.
  const text = Buffer.from("pico-8 cartridge // http://www.pico-8.com\nversion 16\n__lua__\n" +
                           "function _init() end\nfunction _update() end\nfunction _draw() cls(8) end\n");
  feed(frame(0x01, [1, ...le7(text.length, 3), 4, ...nm("t.p8")]));
  check("a text .p8 is refused (not a cooked cart)", reply()[5] === 0x11 && reply()[6] === 7,
        "reply=" + Array.from(reply().slice(5, 8)).join(","));
  check("...and nothing was committed", ex.sim_cart_active() === -1, "active=" + ex.sim_cart_active());

  // A wrong CRC must be refused: nothing commits, so the running cart is untouched.
  upload(1, nm("bad.p8.png"), payloadA);
  feed(frame(0x03, le7((crc16(payloadA) ^ 1) & 0xFFFF, 3)));
  check("a bad CRC is refused", ex.sim_cart_active() === -1 && reply()[5] === 0x11,
        "active=" + ex.sim_cart_active());

  // Now the real cart, into slot 0: BEGIN + DATA* + END must commit and reboot onto it.
  upload(0, nm(fixtures[0]), payloadA);
  const re = feed(frame(0x03, le7(crc16(payloadA), 3)));
  check("END commits the .p8.png cart and reboots", re === 1 && replyOk(3), "active=" + ex.sim_cart_active());
  check("the committed cart is the active slot", ex.sim_cart_active() === 0, "slot=" + ex.sim_cart_active());

  for (let i = 0; i < 30; i++) ex.sim_frame();
  // This cart is one of the big ones (600 KB+ of Lua under the cost model), so on the device's own
  // allocator the honest outcome is the pool refusing it - --device-alloc links that allocator, and
  // then "it did not run" must be a refusal, not a status of -1 out of nowhere.
  const committedRefused = Number(ex.sim_heap_refused());
  check("the committed cart is the active slot and it runs",
        ex.sim_cart_active() === 0 &&
        (ex.sim_status() === 0 ? committedRefused === 0 : committedRefused > 0),
        "slot=" + ex.sim_cart_active() + " status=" + ex.sim_status() +
        " refused=" + committedRefused);
  check("the uploaded cart draws", nonBlack(snap()) > 0, nonBlack(snap()) + " lit px");

  // LIST answers with one INFO frame per slot and then an OK: the slot, its length and its name.
  feed(frame(0x06, []));
  const li = new Uint8Array(ex.memory.buffer, ex.sim_info(), ex.sim_info_len());
  check("LIST reports the committed slot",
        li.length >= 11 && li[5] === 0x12 && li[6] === 0 && li[7] === (CART_LEN & 0x7f) &&
        li[10] === fixtures[0].length && replyOk(6),
        "info=" + Array.from(li.slice(0, 13)).join(","));

  // The decisive one: upload a cart that fills the panel (to slot 1 - the slot that is NOT
  // running) and the engine must switch to it. Its code section is the RAW form, so this also
  // covers the pre-0.1.7 branch - the one the baked default cart uses.
  const fillCooked = cookedFromScript("function _init() end\nfunction _update() end\nfunction _draw() cls(8) end\n");
  const fill = new Uint8Array(CART_LEN);
  fill.set(COOKED_MAGIC, 0);
  fill.set(fillCooked, COOKED_MAGIC.length);
  const litBefore = nonBlack(snap());
  upload(1, nm("fill.p8.png"), fill);
  const re2 = feed(frame(0x03, le7(crc16(fill), 3)));
  check("a second upload takes over (newest slot wins)", re2 === 1 && ex.sim_cart_active() === 1,
        "active=" + ex.sim_cart_active());
  for (let i = 0; i < 8; i++) ex.sim_frame();
  check("the engine runs the uploaded cart (it filled the panel)",
        nonBlack(snap()) > 50000 && ex.sim_status() === 0,
        litBefore + " -> " + nonBlack(snap()) + " lit px · status=" + ex.sim_status());

  // A reboot re-reads the store: the newest slot still wins.
  ex.sim_init();
  for (let i = 0; i < 8; i++) ex.sim_frame();
  check("a POWER-ON keeps running the uploaded cart",
        ex.sim_cart_active() === 1 && nonBlack(snap()) > 50000 && ex.sim_status() === 0,
        "slot=" + ex.sim_cart_active() + " lit=" + nonBlack(snap()) + " status=" + ex.sim_status());

  // ERASE the running slot: only the older slot (the real .p8.png) is left, so it is newest again.
  feed(frame(0x05, [1]));
  check("ERASE clears the running slot", ex.sim_cart_active() === -1, "active=" + ex.sim_cart_active());
  ex.sim_init();
  check("after ERASE the store still holds the .p8.png cart", ex.sim_cart_active() === 0,
        "slot=" + ex.sim_cart_active());

  // Every served .p8.png, one after another: each has to commit and RUN. These are real carts, so
  // this exercises the PXA code-section branch on five different payloads (and the engine loading
  // whatever the store holds) - the end-to-end claim, not a synthetic cart that happens to fit.
  //
  // The heap figure that says "this cart runs on an SMK-37 Pro" (the device gives the engine and
  // Lua ONE pool: 288 KiB, POOL_LEN minus the red zone) is measured the way the PAGE measures it
  // before it lets anyone upload - in a FRESH wasm instance, one per cart. A fresh one matters
  // twice over: its store is empty, so the baseline is the engine's own footprint with the baked
  // cart, and no cart can leave its memory behind for the next cart's measurement. The main
  // instance keeps its store (what was uploaded stays uploaded, as on the device), and nothing
  // here fails for a cart the hardware cannot hold: that is not a failure of the port, and the
  // page says so before anyone uploads one.
  const probeMod = await WebAssembly.compile(bytes);
  const probeCart = async (name, payload, frames) => {
    const probe = (await WebAssembly.instantiate(probeMod, imports)).exports;
    if (probe._initialize) probe._initialize();
    const pfeed = (m) => {
      new Uint8Array(probe.memory.buffer, probe.sim_sysex_in(), m.length).set(m);
      return probe.sim_sysex(m.length);
    };
    probe.sim_init();                                 // POWER-ON
    if (payload) {                                    // ...then the device's own protocol, if asked
      const slot = probe.sim_cart_active() === 1 ? 0 : 1;   // the slot that is NOT running
      pfeed(frame(0x01, [slot, ...le7(payload.length, 3), nm(name).length, ...nm(name)]));
      for (let off = 0; off < payload.length; off += 128) {
        const c = payload.subarray(off, off + 128);
        pfeed(frame(0x02, [...le7(off, 3), ...le7(c.length, 3), ...pack7(c)]));
      }
      pfeed(frame(0x03, le7(crc16(payload), 3)));
    }
    for (let i = 0; i < frames; i++) probe.sim_frame();
    return { peak: Number(probe.sim_heap_peak()), used: Number(probe.sim_heap_used()),
             refused: Number(probe.sim_heap_refused()), deviceAlloc: Number(probe.sim_device_alloc()) === 1,
             status: probe.sim_status(), slot: probe.sim_cart_active() };
  };
  // Under --device-alloc the pool is really 294,912 B and the allocator really refuses, so the
  // verdict is a fact; over the cost model the peak IS the answer (nothing can refuse there).
  const fit = (peak, refused) =>
    Math.round(peak / 1024) + " KB of " + Math.round(DEVICE_HEAP / 1024) + " KB" +
    (refused ? " (실기 RAM 초과: the pool refused " + refused + " allocation(s))"
            : peak <= DEVICE_HEAP ? " (실기 실행 가능)" : " (실기 RAM 초과 - 시뮬레이터 전용)");

  // What the DEVICE can actually hold is a contract, not a hope: the baked cart - the one every
  // device ships with - has to fit the pool with room to spare, or nothing runs on the hardware.
  const builtin = await probeCart(null, null, 30);
  check("the built-in cart fits the device's pool with room to spare",
        builtin.peak > 0 && builtin.peak <= DEVICE_HEAP - 64 * 1024 && builtin.refused === 0,
        Math.round(builtin.peak / 1024) + " KB peak, " +
        Math.round((DEVICE_HEAP - builtin.peak) / 1024) + " KB spare on the device");

  for (const name of fixtures) {
    const payload = await cartPayload(new Uint8Array(fs.readFileSync(path.join(cartDir, name))));
    ex.sim_init();                                    // POWER-ON: the engine re-reads the store
    const slot = ex.sim_cart_active() === 1 ? 0 : 1;  // never the slot that is already running
    upload(slot, nm(name), payload);
    feed(frame(0x03, le7(crc16(payload), 3)));
    for (let i = 0; i < 30; i++) ex.sim_frame();
    const lit = nonBlack(snap());
    // The commit is the claim; the run can only happen if the pool can serve the cart (these five
    // are real games, 600 KB to 2 MB of Lua under the cost model, so on the device's own allocator
    // only the smallest fits). "It did not run" must then BE a refusal - never a silent status -1.
    const refusedHere = Number(ex.sim_heap_refused());
    const ranHere = ex.sim_status() === 0 && lit > 0;
    check("a real .p8.png cart commits and runs: " + name,
          ex.sim_cart_active() === slot && (ranHere ? refusedHere === 0 : refusedHere > 0),
          "status=" + ex.sim_status() + " lit=" + lit +
          (refusedHere ? " · the pool refused " + refusedHere : "") + " (" + payload.length + " B)");
    const m = await probeCart(name, payload, 300);    // 10 s of cart time: a game's steady state
    // A cart the pool can serve must run, and one it cannot must be the pool's own refusal - never
    // a silent -1: with the device's allocator linked in, this is what says "it fits the SMK-37".
    const ran = m.status === 0 && m.peak >= 64 * 1024;   // >= the engine's own buffers: it ran
    check("  ...and the device pool it would need",
          m.deviceAlloc ? (ran ? m.refused === 0 : m.refused > 0) : ran,
          fit(m.peak, m.refused) + " (settled " + Math.round(m.used / 1024) + " KB)");
  }

  // ...and a real one the device CAN run must fit, measured rather than assumed. flipside is the
  // smallest of the five served carts, so it is the one that decides whether the pool is useful.
  const flipIdx = fixtures.findIndex((n) => n.startsWith("flipside"));
  if (flipIdx >= 0) {
    const name = fixtures[flipIdx];
    const m = await probeCart(name, await cartPayload(
      new Uint8Array(fs.readFileSync(path.join(cartDir, name)))), 300);
    check("a real cart that is small enough runs inside the device's pool: " + name,
          m.status === 0 && m.peak <= DEVICE_HEAP && m.refused === 0,
          Math.round(m.peak / 1024) + " KB peak, " +
          Math.round((DEVICE_HEAP - m.peak) / 1024) + " KB spare, " + m.refused + " refusal(s)");
  }
}

// ---------------------------------------------------------------------------------------------
console.log("sim: " + (fails ? fails + " FAILED" : "all ok") + "   (boots=" + ex.sim_boots() +
  ", draws=" + ex.sim_draws() + ", parity=" + (ex.sim_status() === 0 ? "ok" : "status " + ex.sim_status()) + ")");

if (outPath) {
  // A PPM of what the wasm drew, for the preview the firmware verification also compares against.
  let s = "P6\n240 240\n255\n";
  const buf = Buffer.alloc(240 * 240 * 3);
  for (let i = 0; i < 240 * 240; i++) {
    const c = a[i];
    buf[i * 3 + 0] = ((c >> 11) & 0x1f) << 3;
    buf[i * 3 + 1] = ((c >> 5) & 0x3f) << 2;
    buf[i * 3 + 2] = (c & 0x1f) << 3;
  }
  fs.writeFileSync(outPath, Buffer.concat([Buffer.from(s, "ascii"), buf]));
  console.log("sim: wrote " + outPath);
}

process.exit(fails ? 1 : 0);
