// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 M. Geisler (makkuro)
// picovave's AudioWorklet: sim.wasm is the firmware's engine, UI and screen against a simulated
// SMK-37 Pro. Vendored into picovave (picovave/sim/worklet.js) so the simulator stands on its own:
// picovave never reads repo/. The protocol it speaks (sim_*) is the firmware's own contract,
// exported by picovave/sim/sim.c.
// Each render quantum runs the device's clock; ~30 times a second the screen, the pads and the LEDs go out.
class Picovave extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ex = null;
    this.sent = -1;
    this.last = 0;
    this.port.onmessage = (e) => this.onMessage(e.data);
  }
  async onMessage(m) {
    if (m.type === "load") {
      try {
        const mod = await WebAssembly.compile(m.wasm);
        const imports = {};
        let mem = null;
        const set32 = (p, v) => { try { new DataView(mem.buffer).setUint32(p, v >>> 0, true); } catch (e) {} };
        const set64 = (p, v) => { try { new DataView(mem.buffer).setBigUint64(p, BigInt(v), true); } catch (e) {} };
        for (const i of WebAssembly.Module.imports(mod)) {       // stubs: the engine's libc names a few WASI imports
          imports[i.module] = imports[i.module] || {};
          if (i.kind === "function") imports[i.module][i.name] = () => 0;
        }
        const env = imports.env, wasi = imports.wasi_snapshot_preview1;
        if (env) {
          env.__syscall_faccessat = () => -1;
          env.__syscall_dup3 = () => -1;
          env.__syscall_getdents64 = () => -1;
        }
        if (wasi) {                                              // these write through out-pointers
          wasi.environ_sizes_get = (c, b) => { set32(c, 0); set32(b, 0); return 0; };
          wasi.environ_get = () => 0;
          wasi.clock_time_get = (id, prec, out) => { set64(out, Date.now() * 1000); return 0; };
          wasi.fd_close = () => 0;
          wasi.fd_write = (fd, iovs, n, nwritten) => { set32(nwritten, 0); return 0; };
          wasi.fd_read = (fd, iovs, n, nread) => { set32(nread, 0); return 0; };
          wasi.fd_seek = (fd, off, whence, newoff) => { set64(newoff, 0); return 0; };
        }
        const inst = await WebAssembly.instantiate(mod, imports);
        this.ex = inst.exports;
        mem = this.ex.memory;
        if (this.ex._initialize) this.ex._initialize();
        this.ex.sim_init();
        this.port.postMessage({ type: "ready", rate: this.ex.sim_rate() });
      } catch (err) {
        this.port.postMessage({ type: "error", message: String((err && err.message) || err) });
      }
      return;
    }
    const ex = this.ex;
    if (!ex) return;
    if (m.type === "pad") ex.sim_pad(m.pad, m.vel);
    else if (m.type === "key") ex.sim_key(m.key, m.down ? 1 : 0);
    else if (m.type === "button") ex.sim_button(m.b, m.down ? 1 : 0);
    else if (m.type === "enc") ex.sim_enc(m.e, m.steps | 0);
    else if (m.type === "fader") ex.sim_fader(m.i, m.v);
    else if (m.type === "wheel") ex.sim_wheel(m.i, m.raw);
    else if (m.type === "midi") ex.sim_midi(m.d[0], m.d[1], m.d[2]);
    else if (m.type === "sysex") {
      // A cart upload frame: copy it into the wasm and run the device's own parser. A frame that
      // commits/erases a slot reboots the firmware onto the new cart, so repaint then.
      const d = m.d, p = ex.sim_sysex_in();
      new Uint8Array(ex.memory.buffer, p, d.length).set(d);
      const re = ex.sim_sysex(d.length);
      this.port.postMessage({ type: "cart", reboot: re, active: ex.sim_cart_active(),
        reply: Array.from(new Uint8Array(ex.memory.buffer, ex.sim_reply(), ex.sim_reply_len())) });
      if (re) this.publish();
    }
  }
  publish() {
    const ex = this.ex, mem = ex.memory.buffer, msg = { type: "frame" }, tr = [];
    const d = ex.sim_draws();
    if (d !== this.sent) {
      this.sent = d;
      msg.fb = new Uint16Array(mem, ex.sim_fb(), 240 * 240).slice();
      tr.push(msg.fb.buffer);
    }
    msg.pads = Array.from(new Uint32Array(mem, ex.sim_pads(), 16));
    msg.leds = ex.sim_leds();
    this.port.postMessage(msg, tr);
  }
  process(inputs, outputs) {
    const ex = this.ex;
    if (!ex) return true;
    const out = outputs[0], n = out[0].length;
    ex.sim_render(n);
    out[0].set(new Float32Array(ex.memory.buffer, ex.sim_out_l(), n));
    if (out[1]) out[1].set(new Float32Array(ex.memory.buffer, ex.sim_out_r(), n));
    if (currentTime - this.last >= 1 / 30) {
      this.last = currentTime;
      this.publish();
    }
    return true;
  }
}
registerProcessor("picovave", Picovave);
