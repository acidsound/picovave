// luabc.mjs - the page's cart compiler: femto8's own Lua, compiled to wasm32.
//
// It turns the 32,800-byte cooked cart into Lua bytecode the device can undump, so an upload
// never makes the device run the compiler (femto8/src/p8_emu.c's p8_materialize_script ->
// p8_lua.c's lua_init_script) and never asks the pool for a source buffer. The chunk is produced
// by the SAME Lua sources the device links, so it is the chunk the device would have made.
//
// The wasm is built by tools/build_luabc.py and served next to this page.

let compiled = null;   // the WebAssembly.Module, compiled once
let lastError = "";

const IMPORTS = {
  env: {
    emscripten_notify_memory_growth: () => {},
    __syscall_faccessat: () => -1,
    __syscall_dup3: () => -1,
    __syscall_getdents64: () => -1,
  },
  wasi_snapshot_preview1: {
    environ_sizes_get: (c, b) => { u32(c, 0); u32(b, 0); return 0; },
    environ_get: () => 0,
    clock_time_get: (id, prec, out) => { u64(out, BigInt(Date.now()) * 1000n); return 0; },
    fd_close: () => 0,
    fd_write: (fd, iovs, n, written) => { u32(written, 0); return 0; },
    fd_read: () => 0,
    fd_seek: () => 0,
  },
};

let mem = null;        // the instance being initialised; only its stubs read these
const u32 = (p, v) => { if (mem) new DataView(mem.buffer).setUint32(p, v >>> 0, true); };
const u64 = (p, v) => { if (mem) new DataView(mem.buffer).setBigUint64(p, BigInt(v), true); };

async function load() {
  if (!compiled) {
    const r = await fetch(new URL("./luabc.wasm", import.meta.url));
    if (!r.ok) throw new Error("luabc.wasm 없음 (" + r.status + ")");
    compiled = await WebAssembly.compile(await r.arrayBuffer());
  }
  return compiled;
}

/* cooked: the 32,800-byte cooked cart (PVC1's payload past its 4-byte magic).
 * Returns the bytecode, or null when this cart does not compile. */
export async function compileCart(cooked) {
  const mod = await load();
  const { exports: e } = await WebAssembly.instantiate(mod, IMPORTS);
  if (e._initialize) e._initialize();
  mem = e.memory;
  new Uint8Array(e.memory.buffer).set(cooked, e.luabc_in());
  const n = e.luabc_compile(cooked.length);
  lastError = n <= 0 ? new TextDecoder().decode(new Uint8Array(e.memory.buffer, e.luabc_error(), 160)).replace(/\0.*$/s, "") : "";
  mem = null;
  if (n <= 0) return null;
  return new Uint8Array(e.memory.buffer).slice(e.luabc_out(), e.luabc_out() + n);
}

/* Why the last compileCart() returned null ("" when the cart simply has no script). */
export function compileError() { return lastError; }
