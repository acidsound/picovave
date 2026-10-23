// SPDX-License-Identifier: GPL-3.0-only
// The browser's installer for the SMK-37 Pro (FM-1 family): a port of repo/tools/fm1_install.py
// that talks over Web MIDI (SysEx) instead of mido. The wire format and the two-step update are
// the device's own (repo/firmware/src/ota.c, repo/firmware/loader/ldr_core.c); this file only
// speaks them. tools/fm1_install.py is the reference, and picovave/sim/fm1ota_check.mjs pins the
// wire format to it, so the port cannot drift from the updater the hardware trusts.
//
//   1. the running firmware reads parts of the package and stages the update loader
//      (F0 22 24 35 7F F7 "upgrade" -> cmd 0x30 read requests -> 0xE0000000 "success")
//   2. the loader reads the whole package, writes the app area, then restarts
//      (upgrade again -> reads -> 0xF0000000 "success")
//
// In both steps the device asks (SysEx read requests on the logical image) and we answer.
//
// The link is async so the browser can await MIDI: a backend exposes inputNames()/outputNames()
// and open(inName, outName) -> { link }, where link = { name, send(pkt), read(ms), drain(),
// get lost, close() }. WebMidiBackend implements it over navigator.requestMIDIAccess(); tests
// inject their own backend, so the whole update runs without hardware.

export const HS_QUERY = Uint8Array.from([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7]);
export const UPGRADE = Uint8Array.from([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7]);
export const FINISH_CHECK = 0xE0000000;
export const FINISH_WRITE = 0xF0000000;
export const MAXDATA = 512;
export const BLOCKS = 20, BLK = 0x30, KEEP = 0x2F, MAX_BLOCKS = 64;
export const LOADER_MARK = "FELUCCA-LOADER-1";
// never probe other gear: only ports whose name looks like this family / other Felucca devices
export const PORT_RE = /picovave|fm-1|smk|elite|mke-p37|starry|felucca|melodee|fomni|x0x|makkuro|snoop|vector|vox|ota|composite|sinco|usb-midi/i;
export const OWN_LOADER_RE = /^ota-(FM-1|SMK-37 Pro|SMK-37 Elite|MKE-P37)_[79]\d\d$/i;

// seconds; tests shorten them
export const DELAY = {
  open: 0.3, start: 2.0, reply: 0.01, loader: 3.0, reboot: 3.0, retry: 1.0,
  poll: 1.0, hs: 1.0, idleCheck: 8.0, idleWrite: 180.0, waitLoader: 30.0, waitReboot: 40.0,
};

export class InstallError extends Error {
  constructor(code, msg) { super(msg); this.name = "InstallError"; this.code = code; }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const nowMs = () => Date.now();

// ------------------------------------------------------------ wire format ---

export function pack7(data) {
  const out = [];
  let acc = 0, nb = 0;
  for (const b of data) {
    acc |= b << nb;
    nb += 8;
    while (nb >= 7) { out.push(acc & 0x7F); acc >>>= 7; nb -= 7; }
  }
  if (nb) out.push(acc & 0x7F);
  return Uint8Array.from(out);
}

export function unpack7(s) {
  const out = [];
  let acc = 0, nb = 0;
  for (const b of s) {
    acc |= (b & 0x7F) << nb;
    nb += 7;
    while (nb >= 8) { out.push(acc & 0xFF); acc >>>= 8; nb -= 8; }
  }
  return Uint8Array.from(out);
}

// CRC-16/XMODEM, as the package and the device use
export function crc16(data, crc = 0) {
  for (const b of data) {
    crc ^= b << 8;
    for (let i = 0; i < 8; i++) crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) & 0xFFFF : (crc << 1) & 0xFFFF;
  }
  return crc & 0xFFFF;
}

// JieLi ENC stream cipher (its own inverse), key 0xFFFF
export function jlEnc(data) {
  const out = Uint8Array.from(data);
  let key = 0xFFFF;
  for (let i = 0; i < out.length; i++) {
    out[i] ^= key & 0xFF;
    key = ((key << 1) ^ (key & 0x8000 ? 0x1021 : 0)) & 0xFFFF;
  }
  return out;
}

const SUCCESS = Uint8Array.from([0x73, 0x75, 0x63, 0x63, 0x65, 0x73, 0x73, 0x00]);   // "success\0"

// the answer to a read request: F0 pack7(00 59 30 len24 fl addr32 n24 data chk) F7
export function response(addr, data, fl = 0) {
  const n = data.length;
  const body = [
    0x00, 0x59, 0x30, (n + 8) & 0xFF, ((n + 8) >> 8) & 0xFF, 0, fl & 0xFF,
    addr & 0xFF, (addr >>> 8) & 0xFF, (addr >>> 16) & 0xFF, (addr >>> 24) & 0xFF,
    n & 0xFF, (n >>> 8) & 0xFF, 0,
  ];
  for (let i = 0; i < n; i++) body.push(data[i]);
  let s = 0;
  for (let i = 6; i < body.length; i++) s += body[i];
  body.push((~s) & 0xFF);
  const p = pack7(body);
  const out = new Uint8Array(p.length + 2);
  out[0] = 0xF0; out.set(p, 1); out[out.length - 1] = 0xF7;
  return out;
}

// a device read request (cmd 0x30) -> { fl, addr, n }, or null
export function parseRequest(pkt) {
  if (!pkt || pkt.length < 2 || pkt[0] !== 0xF0 || pkt[pkt.length - 1] !== 0xF7) return null;
  const u = unpack7(pkt.subarray(1, pkt.length - 1));
  if (u.length !== 15 || u[0] !== 0x00 || u[1] !== 0x59 || u[2] !== 0x30) return null;
  let s = 0;
  for (let i = 6; i < 14; i++) s += u[i];
  if (((~s) & 0xFF) !== u[14]) return null;
  return {
    fl: u[6],
    addr: (u[7] | (u[8] << 8) | (u[9] << 16) | (u[10] << 24)) >>> 0,
    n: (u[11] | (u[12] << 8) | (u[13] << 16)) >>> 0,
  };
}

const latin1 = (u) => String.fromCharCode.apply(null, u);

// a device identity reply (cmd 0x11) -> { text, model, version, loader, tag }, or null
export function parseIdentity(pkt) {
  if (!pkt || pkt.length < 2 || pkt[0] !== 0xF0 || pkt[pkt.length - 1] !== 0xF7) return null;
  const d = unpack7(pkt.subarray(1, pkt.length - 1));
  if (d.length !== 34 || d[0] !== 0x00 || d[1] !== 0x59 || d[2] !== 0x11) return null;
  let end = 6;
  while (end < 33 && d[end] !== 0) end++;
  const text = latin1(d.subarray(6, end));
  const m = /^([^_]+)_(\d+)$/.exec(text);
  if (!m) return null;
  let tag = "";
  if (end < 33) {
    let tagStart = end + 1;
    while (tagStart < 33 && d[tagStart] === 0) tagStart++;
    let tagEnd = tagStart;
    while (tagEnd < 33 && d[tagEnd] !== 0) tagEnd++;
    if (tagStart < tagEnd) tag = latin1(d.subarray(tagStart, tagEnd));
  }
  return { text, model: m[1], version: parseInt(m[2], 10), tag, loader: m[1].toLowerCase().startsWith("ota-") };
}

// --------------------------------------------------------------- package ---

const _idChar = (m, i) => { const c = (m - i - 1) & 0xFF; return c >= 0x20 && c < 0x7F; };

// the identity blocks: 20, or more when the identity is longer (the 36-block stock layout)
export function identityBlocks(raw) {
  const marker = (k) => ((k + 1) * BLK <= raw.length ? raw[k * BLK + KEEP] : null);
  let n = BLOCKS;
  while (n < MAX_BLOCKS) { const m = marker(n); if (m === null || m === 0x7D || !_idChar(m, n)) break; n++; }
  return (n > BLOCKS && marker(n) === 0x7D) ? n : BLOCKS;
}

export function blockCount(raw) {
  let n = identityBlocks(raw);
  while (n < MAX_BLOCKS && (n + 1) * BLK <= raw.length && raw[n * BLK + KEEP] === 0x7D) n++;
  return n;
}

// the image the device reads during an update: the package without the marker bytes
export function logicalImage(raw, blocks = BLOCKS) {
  const out = [];
  for (let i = 0; i < blocks; i++) for (let j = 0; j < KEEP; j++) out.push(raw[i * BLK + j]);
  for (let i = blocks * BLK; i < raw.length; i++) out.push(raw[i]);
  return Uint8Array.from(out);
}

// -> { image, blocks }: the detected count when the files' CRCs agree, else 20
export function deinterleave(raw) {
  const n = blockCount(raw);
  const image = logicalImage(raw, n);
  if (n !== BLOCKS && packageDamage(image)) {
    const alt = logicalImage(raw, BLOCKS);
    if (!packageDamage(alt)) return { image: alt, blocks: BLOCKS };
  }
  return { image, blocks: n };
}

export function productOf(raw) {
  if (raw.length < BLOCKS * BLK) throw new InstallError("badpkg", "not an FM-1 package (too short)");
  const ib = identityBlocks(raw);
  const n = ib === BLOCKS ? BLOCKS : Math.min(ib, deinterleave(raw).blocks);
  let s = "";
  for (let i = 0; i < n; i++) { const m = raw[i * BLK + KEEP]; if (m !== 0x7D) s += String.fromCharCode((m - i - 1) & 0xFF); }
  return s;
}

// what is wrong with the update image (header, file list, each file's CRC), or null
export function packageDamage(image) {
  if (image.length < 0x40) return "too short";
  const hdr = jlEnc(image.subarray(0, 0x40));
  if (crc16(hdr.subarray(2, 0x40)) !== (hdr[0] | (hdr[1] << 8))) return "header CRC";
  const count = hdr[8] | (hdr[9] << 8);
  if (!(count >= 1 && count <= 8) || image.length < 0x40 + 0x50 * count) return "file list";
  const lst = image.subarray(0x40, 0x40 + 0x50 * count);
  if (crc16(lst) !== (hdr[2] | (hdr[3] << 8))) return "file list CRC";
  for (let i = 0; i < count; i++) {
    const e = jlEnc(lst.subarray(i * 0x50, (i + 1) * 0x50));
    const dcrc = e[4] | (e[5] << 8);
    const off = (e[8] | (e[9] << 8) | (e[10] << 16) | (e[11] << 24)) >>> 0;
    const size = (e[12] | (e[13] << 8) | (e[14] << 16) | (e[15] << 24)) >>> 0;
    let end = 0x40; while (end < 0x50 && e[end] !== 0) end++;
    const name = latin1(e.subarray(0x40, end));
    if (off + size > image.length) return name + ": cut short";
    const typ = e[0] | (e[1] << 8);
    // flash.bin (0) and ota.bin (100) carry the CRC of their bytes as served; the packer's other
    // entries store a CRC of decrypted contents (checked for their bounds only)
    if ((typ === 0 || typ === 100) && crc16(image.subarray(off, off + size)) !== dcrc) return name + ": CRC";
  }
  return null;
}

function indexOfBytes(hay, needle) {
  outer: for (let i = 0; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}
const MARK = Uint8Array.from(Array.from(LOADER_MARK).map((c) => c.charCodeAt(0)));
export const hasLoaderMark = (raw) => indexOfBytes(raw, MARK) >= 0;

// verify a raw .fwsc -> { product, image }. The device only checks the staged loader before it
// rewrites the firmware, so a damaged file is caught here.
export function loadPackage(raw, force = false) {
  const product = productOf(raw);
  if (!/^[^_]+_\d+$/.test(product)) throw new InstallError("badpkg", "not an FM-1 package (identity " + JSON.stringify(product) + ")");
  if (!hasLoaderMark(raw) && !force) throw new InstallError("badpkg", "no Felucca update loader in this package; use FLASH 복원 or --force");
  const { image } = deinterleave(raw);
  const bad = packageDamage(image);
  if (bad) throw new InstallError("badpkg", "damaged package (" + bad + "); nothing was written");
  return { product, image };
}

// --------------------------------------------------------------- identity ---

export const modelOf = (text) => text.split("_")[0].replace(/^ota-/, "");
// the SMK-37 Pro, SMK-37 Elite, MKE-P37 and the Starrykey 37 Play: one keyboard under many names
export const SMK_FAMILY = /^SMK-37[ +].*$|^MKE-P37$|^STARRYKEY-37.*$/i;
export function sameKeyboard(a, b) {
  const ma = modelOf(a), mb = modelOf(b);
  return ma === mb || (SMK_FAMILY.test(ma) && SMK_FAMILY.test(mb));
}

export function pairOutput(name, outs) {
  if (outs.includes(name)) return name;
  const base = name.replace(/\s+\d+$/, "");           // Windows numbers each port
  const same = outs.filter((o) => o.replace(/\s+\d+$/, "") === base);
  if (same.length === 1) return same[0];
  return outs.length === 1 ? outs[0] : null;
}

// --------------------------------------------------------------- updater ---

export class Updater {
  constructor(backend, opts = {}) {
    this.backend = backend;
    this.port = opts.port || null;
    this.delays = Object.assign({}, DELAY, opts.delays || {});
  }

  candidates(onlyPort) {
    const names = this.backend.inputNames();
    if (this.port) {
      const mine = names.filter((n) => n.toLowerCase().includes(this.port.toLowerCase()));
      if (onlyPort) return mine;
      return mine.concat(names.filter((n) => !mine.includes(n) && PORT_RE.test(n)));
    }
    return names.filter((n) => PORT_RE.test(n));
  }

  /* the outputs to try with one input: its named pair first, then the other family ports. A
   * multi-cable device (SMK-37 Pro: -Private/-Master/-MCP) does not have to pair in/out by name. */
  outputsFor(name) {
    const outs = this.backend.outputNames();
    const first = pairOutput(name, outs);
    const rest = outs.filter((o) => o !== first && PORT_RE.test(o));
    return first ? [first].concat(rest) : rest;
  }

  /* open one named input and try the handshake over each candidate output, letting CoreMIDI settle
   * first: -> { link, id, name, out }, or null. An identity is only accepted on THIS input. */
  async openPort(name, tries = 2) {
    for (const out of this.outputsFor(name)) {
      let dev;
      try { dev = this.backend.open(name, out); } catch (e) { continue; }
      // fm1_install.py sleeps here too: CoreMIDI may drop a message sent just after opening.
      if (this.delays.open) await sleep(this.delays.open * 1000);
      const ident = await this.handshake(dev.link, tries);
      if (ident) return { link: dev.link, id: ident, name, out };
      dev.link.close();
    }
    return null;
  }

  /* the first matching port that answers the handshake (and want(identity)). onlyPort=false also
   * tries the other family ports, which is what wait_for does after the loader reboots. onTry
   * reports each port as it is probed (for the page's log). */
  async find(want = null, onlyPort = true, onTry = null, skip = null) {
    for (const name of this.candidates(onlyPort)) {
      if (skip && skip.includes(name)) continue;
      if (onTry) onTry(name);
      const dev = await this.openPort(name);
      if (dev && (!want || want(dev.id))) return dev;
    }
    return null;
  }

  /* Probe EVERY input port (family-looking ones first) and report the identity each answers, so a
   * failed auto-find can show exactly which port is the device - even if its name is unusual, or
   * none answers at all. -> [{ name, out, family, id, raw, err }] */
  async probeAll(onResult = null) {
    const outs = this.backend.outputNames();
    const names = this.backend.inputNames();
    const order = names.filter((n) => PORT_RE.test(n)).concat(names.filter((n) => !PORT_RE.test(n)));
    const res = [];
    for (const name of order) {
      const family = PORT_RE.test(name);
      const outs = this.outputsFor(name);
      let id = null, raw = 0, err = null, sendErr = null, state = null, used = null;
      if (!outs.length) err = "출력 없음";
      else for (const out of outs) {
        let dev = null;
        try { dev = this.backend.open(name, out); } catch (e) { err = "열 수 없음"; continue; }
        if (dev.link.input) state = dev.link.input.state || null;
        if (this.delays.open) await sleep(this.delays.open * 1000);
        const got = await this.handshake(dev.link, family ? 2 : 1);
        raw = (dev.link.raw || []).length;
        sendErr = dev.link.sendErr || null;
        dev.link.close();
        used = out;
        if (got) { id = got; break; }
      }
      const r = { name, out: used, family, id, raw, err, sendErr, state };
      res.push(r);
      if (onResult) onResult(r, res.length, order.length);
    }
    return res;
  }

  async waitFor(want, secs) {
    const end = nowMs() + secs * 1000;
    while (nowMs() < end) {
      const dev = await this.find(want, false);
      if (dev) return dev;
      await sleep(this.delays.retry * 1000);
    }
    return null;
  }

  async handshake(link, tries = 3) {
    if (link.drain) link.drain();
    for (let i = 0; i < tries; i++) {
      if (!link.send(HS_QUERY)) return null;
      const end = nowMs() + this.delays.hs * 1000;
      for (;;) {
        const left = end - nowMs();
        if (left <= 0) break;
        const p = await link.read(left);
        if (p == null) break;
        const ident = parseIdentity(p);
        if (ident) return ident;
      }
    }
    return null;
  }

  /* answer read requests until the device asks for `finish`; -> { served, finished, lost } */
  async serve(link, image, finish, idle, progress) {
    let served = 0, last = nowMs();
    for (;;) {
      const pkt = await link.read(this.delays.poll * 1000);
      if (pkt == null) {
        if (link.lost) return { served, finished: false, lost: true };
        if (nowMs() - last > idle * 1000) return { served, finished: false, lost: false };
        continue;
      }
      const r = parseRequest(pkt);
      if (!r) continue;
      const { fl, addr, n } = r;
      last = nowMs();
      if (addr === FINISH_CHECK || addr === FINISH_WRITE) {
        await link.send(response(addr, SUCCESS, 0));
        if (addr === finish) return { served, finished: true, lost: false };
        continue;
      }
      if (n > MAXDATA || addr + n > image.length)
        throw new InstallError("badreq", "the device asked for 0x" + addr.toString(16) + "+" + n + ", outside the package");
      await sleep(this.delays.reply * 1000);
      if (!await link.send(response(addr, image.subarray(addr, addr + n), fl)))
        return { served, finished: false, lost: true };
      served++;
      progress(served, addr + n);
    }
  }

  /* step 1: the running firmware checks the package and starts the loader */
  async check(dev, image, step) {
    step("start", dev.id.text);
    dev.link.send(UPGRADE);
    await sleep(this.delays.start * 1000);
    const { served, finished, lost } = await this.serve(dev.link, image, FINISH_CHECK, this.delays.idleCheck,
      (k) => step("check", k));
    dev.link.close();
    if (!finished) throw new InstallError(lost ? "lost" : "stopped",
      "the SMK-37 " + (lost ? "was disconnected" : "stopped answering") + " after " + served + " requests; nothing was written");
    step("loader");
    await sleep(this.delays.loader * 1000);
    const ota = await this.waitFor((i) => i.loader, this.delays.waitLoader);
    if (!ota) throw new InstallError("noloader",
      "the update loader did not appear. Replug the USB cable and install again: it stays in update mode until done");
    return ota;
  }

  /* step 2: the loader reads and writes the whole package, then restarts */
  async write(ota, image, step) {
    step("write", 0);
    ota.link.send(UPGRADE);
    await sleep(this.delays.start * 1000);
    const { served, finished, lost } = await this.serve(ota.link, image, FINISH_WRITE, this.delays.idleWrite,
      (k, end) => step("write", Math.min(99, Math.floor((end * 100) / image.length))));
    ota.link.close();
    if (!finished) throw new InstallError(lost ? "lost" : "stopped",
      "the loader " + (lost ? "was disconnected" : "stopped answering") + " after " + served + " requests");
    step("write", 100);
  }

  async verify(product, step) {
    step("reboot");
    await sleep(this.delays.reboot * 1000);
    const back = await this.waitFor((i) => !i.loader, this.delays.waitReboot);
    if (!back) throw new InstallError("noreturn", "the SMK-37 did not come back: power-cycle it");
    back.link.close();
    if (back.id.text !== product) throw new InstallError("mismatch", "written, but it reports " + back.id.text + " (expected " + product + ")");
    step("done", back.id.text);
    return back.id.text;
  }

  /* running firmware -> loader -> new firmware; dev from find(). A device already in update
   * mode only needs the write. */
  async install(dev, image, product, step = () => {}) {
    if (!sameKeyboard(dev.id.text, product)) {
      dev.link.close();
      throw new InstallError("model", "the device is " + dev.id.text + ", the package is for " + product);
    }
    if (dev.id.loader && !OWN_LOADER_RE.test(dev.id.text)) {
      dev.link.close();
      throw new InstallError("foreign", "the device is in the update mode of another firmware (" + dev.id.text + "): finish that update first");
    }
    const ota = dev.id.loader ? dev : await this.check(dev, image, step);
    await this.write(ota, image, step);
    return this.verify(product, step);
  }
}

// -------------------------------------------------------------- Web MIDI ---

export function webMidiSupported() {
  return typeof navigator !== "undefined" && typeof navigator.requestMIDIAccess === "function";
}

class WebMidiLink {
  constructor(input, output, name) {
    this.input = input; this.output = output; this.name = name;
    this.q = []; this.waiters = []; this.raw = [];
    // Chrome on macOS opens an input implicitly when a handler is set, but some builds want it
    // said out loud; either way the port is open from here.
    try { input.open && input.open(); } catch (e) { /* already open / not needed */ }
    input.onmidimessage = (ev) => this._rx(ev.data);
  }
  _rx(data) {
    if (!data || data.length < 2 || data[0] !== 0xF0) return;
    const pkt = Uint8Array.from(data);
    if (this.raw.length < 64) this.raw.push(pkt);   // kept for the port diagnostic
    const w = this.waiters.shift();
    if (w) w(pkt); else this.q.push(pkt);
  }
  drain() { this.q.length = 0; }
  get lost() { return this.input.state === "disconnected" || this.output.state === "disconnected"; }
  send(pkt) {
    try { this.output.send(Array.from(pkt)); this.sendErr = null; return true; }
    catch (e) { this.sendErr = String((e && e.name) || e); return false; }
  }
  read(timeoutMs) {
    if (this.q.length) return Promise.resolve(this.q.shift());
    return new Promise((res) => {
      const cb = (v) => { clearTimeout(t); res(v); };
      const t = setTimeout(() => { const i = this.waiters.indexOf(cb); if (i >= 0) this.waiters.splice(i, 1); res(null); },
        Math.max(0, timeoutMs));
      this.waiters.push(cb);
    });
  }
  close() { this.input.onmidimessage = null; this.waiters = []; }
}

export class WebMidiBackend {
  constructor(access) { this.access = access; }
  static async request() {
    if (!webMidiSupported()) throw new InstallError("usage", "this browser has no Web MIDI (use Chrome on https / localhost)");
    let access;
    try { access = await navigator.requestMIDIAccess({ sysex: true }); }
    catch (e) { throw new InstallError("usage", "Web MIDI permission was refused (allow it in the address bar)"); }
    return new WebMidiBackend(access);
  }
  inputNames() { return Array.from(this.access.inputs.values()).map((p) => p.name || p.id); }
  outputNames() { return Array.from(this.access.outputs.values()).map((p) => p.name || p.id); }
  _byName(map, name) { for (const p of map.values()) if ((p.name || p.id) === name) return p; return null; }
  open(inName, outName) {
    const input = this._byName(this.access.inputs, inName);
    const output = this._byName(this.access.outputs, outName);
    if (!input || !output) throw new InstallError("notfound", "the MIDI port vanished");
    return { link: new WebMidiLink(input, output, inName) };
  }
}

/* Request MIDI (a permission prompt), find a device that answers the handshake, and return
 * { up, dev }. The caller closes dev.link or runs up.install(dev, ...). */
export async function connect(opts = {}) {
  const backend = await WebMidiBackend.request();
  const up = new Updater(backend, opts);
  const dev = await up.find();
  if (!dev) throw new InstallError("notfound", "no SMK-37 Pro (FM-1) answered on the MIDI ports");
  return { up, dev, backend };
}
