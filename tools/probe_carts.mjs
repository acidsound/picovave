// Measure every served cart against the device's pool, one fresh instance each - the way the page
// (web/sim/index.html, measureCart) and sim_test.mjs's probeCart() do it. Prints the peak the
// device's own allocator served, how much of the arena (414,184 B, sim/device_alloc.c's PV_ARENA)
// is left, and whether the pool refused anything / which slot ended up running - i.e. the numbers
// docs/HANDOVER.md §13 quotes.
//
// Build the wasm first (tools/build_sim.py's default IS the device allocator):
//   node tools/probe_carts.mjs [path/to/sim.wasm]        # one pass over carts/
//   SWEEP_KB=368,384,400,416 SWEEP_FRAMES=1800 \
//       node tools/probe_carts.mjs path/to/sim.wasm --sweep   # same carts at several arena sizes
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { cartPayload } from "../web/sim/p8png.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const wasmPath = process.argv[2] || path.join(here, "..", "build", "sim", "sim.wasm");
const bytes = fs.readFileSync(wasmPath);
const args = process.argv.slice(2);

let mem = null;   // the instance currently being initialised; the stubs' results are unused
const view = () => (mem ? new DataView(mem.buffer) : null);
const putU32 = (p, v) => { const d = view(); if (d) d.setUint32(p, v >>> 0, true); };
const putU64 = (p, v) => { const d = view(); if (d) d.setBigUint64(p, BigInt(v), true); };
const imports = {
  env: { __syscall_faccessat: () => -1, __syscall_dup3: () => -1, __syscall_getdents64: () => -1 },
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

const ARENA = 414184 - 1024;
const cartDir = path.join(path.dirname(wasmPath), "carts");
const mod = await WebAssembly.compile(bytes);

const probe = async (name, payload, frames, arena) => {
  const inst = await WebAssembly.instantiate(mod, imports);
  const p = inst.exports;
  mem = p.memory;
  if (p._initialize) p._initialize();
  if (arena && p.sim_arena_set) p.sim_arena_set(arena);   // before the first allocation
  const feed = (m) => { new Uint8Array(p.memory.buffer, p.sim_sysex_in(), m.length).set(m); return p.sim_sysex(m.length); };
  p.sim_init();
  if (payload) {
    const slot = p.sim_cart_active() === 1 ? 0 : 1;
    feed(frame(0x01, [slot, ...le7(payload.length, 3), nm(name).length, ...nm(name)]));
    for (let off = 0; off < payload.length; off += 128) {
      const c = payload.subarray(off, off + 128);
      feed(frame(0x02, [...le7(off, 3), ...le7(c.length, 3), ...pack7(c)]));
    }
    feed(frame(0x03, le7(crc16(payload), 3)));
  }
  for (let i = 0; i < frames; i++) p.sim_frame();
  return { peak: Number(p.sim_heap_peak()), used: Number(p.sim_heap_used()),
           refused: Number(p.sim_heap_refused()), deviceAlloc: Number(p.sim_device_alloc()) === 1,
           status: p.sim_status(), slot: p.sim_cart_active(), running: Number(p.sim_cart_running()) };
};

console.log("wasm: " + wasmPath + (mod ? "" : ""));
const probe0 = await probe(null, null, 30);
console.log("baseline (built-in cart): peak " + Math.round(probe0.peak / 1024) + " KB, device-alloc=" + probe0.deviceAlloc);

const files = fs.readdirSync(cartDir).filter((n) => n.endsWith(".p8.png")).sort();
if (args.includes("--sweep")) {
  // How big an arena would each cart NEED? Not its cost-model peak: under the device allocator a
  // refusal makes Lua run a full GC and retry (lmem.c), so a cart can survive in an arena far
  // below the peak it reaches when nothing presses back. Each cell is "s<status>r<refused>" at
  // that arena size, with the running cart in <>. s0<> = it ran; sN = the engine error it threw.
  const sizes = (process.env.SWEEP_KB || "272,304,320,352,384,448,512,640,768,1024").split(",").map(Number);
  const frames = Number(process.env.SWEEP_FRAMES || 300);
  console.log("arena KB ->".padEnd(36) + sizes.map((k) => String(k).padStart(9)).join("") + "   frames " + frames);
  for (const f of files) {
    const payload = await cartPayload(new Uint8Array(fs.readFileSync(path.join(cartDir, f))));
    const cells = [];
    for (const k of sizes) {
      const m = await probe(f, payload, frames, k * 1024);
      const cartOf = m.running < 0 ? (m.running === -1 ? "emb" : "none") : (m.running === m.slot ? "OK" : "slot" + m.running);
      cells.push(("s" + m.status + "r" + m.refused + "<" + cartOf + ">").padStart(9));
    }
    console.log(f.padEnd(34) + cells.join(""));
  }
} else {
console.log("arena (device, PV_ARENA less the 1 KiB red zone): " + ARENA + " B; baseline below");
console.log("");
console.log("cart                                  peak300B   peak1B   refused  status  running     free@300B");
for (const f of files) {
  const payload = await cartPayload(new Uint8Array(fs.readFileSync(path.join(cartDir, f))));
  const a = await probe(f, payload, 300);
  const b = await probe(f, payload, 1);
  const cartOf = a.running < 0 ? (a.running === -1 ? "embedded" : "nothing") : "slot " + a.running;
  console.log(f.padEnd(34) + String(a.peak).padStart(10) + String(b.peak).padStart(10) +
    String(a.refused).padStart(9) + String(a.status).padStart(8) + "  " + cartOf.padEnd(11) +
    String(ARENA - a.peak).padStart(10));
}
}
