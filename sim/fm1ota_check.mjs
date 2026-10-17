// SPDX-License-Identifier: GPL-3.0-only
// Pin the browser installer (web/sim/fm1ota.mjs) to the updater the hardware trusts
// (picovave/platform/tools/fm1_install.py) and run its whole two-step update against a fake device.
//
//   node picovave/sim/fm1ota_check.mjs <fm1ota.mjs> <package.fwsc> [more.fwsc ...]
//
// Two things come out of this, one JSON blob:
//   wire      fixed vectors (pack7/unpack7/crc16/jlEnc/response/parseRequest/parseIdentity).
//             tools/verify_final.py recomputes every one of them with fm1_install.py and
//             requires the same answer, so the wire format cannot drift from the reference.
//   install   the module's Updater, driven end to end against a FAKE device that speaks the
//             device side (ota.c start, then the loader) and checks every byte it is served
//             against the real package. If the host answers anything wrong, the update fails.
import fs from "fs";
import { pathToFileURL } from "url";

const [, , modPath, ...pkgPaths] = process.argv;
if (!modPath || pkgPaths.length === 0) {
  console.error("usage: node fm1ota_check.mjs <fm1ota.mjs> <package.fwsc> [...]");
  process.exit(2);
}
const F = await import(pathToFileURL(modPath).href);

const hex = (u) => Array.from(u).map((b) => b.toString(16).padStart(2, "0")).join("");
const fromHex = (s) => Uint8Array.from((s.match(/../g) || []).map((h) => parseInt(h, 16)));
const concat = (...parts) => { const o = []; for (const p of parts) for (const b of p) o.push(b); return Uint8Array.from(o); };
const rd16 = (u, i) => u[i] | (u[i + 1] << 8);
const rd32 = (u, i) => (u[i] | (u[i + 1] << 8) | (u[i + 2] << 16) | (u[i + 3] << 24)) >>> 0;
const latin1 = (u) => String.fromCharCode.apply(null, u);
const eqBytes = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

// ------------------------------------------------------------- wire vectors ---

const SEQ = Uint8Array.from(Array.from({ length: 40 }, (_, i) => i));       // 00..27
const DATA32 = Uint8Array.from(Array.from({ length: 32 }, (_, i) => i));
const SUCCESS = Uint8Array.from([0x73, 0x75, 0x63, 0x63, 0x65, 0x73, 0x73, 0x00]);
// fixed wires, generated from the reference: a read request and an identity reply
const WIRE_REQ = [
  "f0003241410000000000000100004000003d01f7",                                       // 0x4000 len 512
  "f00032414100000000000000000e0100001700f7",                                       // 0xE0000000 len 8
];
const WIRE_ID = [
  "f000324558010040294d16351973060828725e7d3a03060c0000000000000000000000000000400df7",  // SMK-37 Pro_700
  "f000324558010040377442351955695216336e0001256e5b2f376040010000000000000000002031f7",  // ota-SMK-37 Pro_700
];

const wire = {
  pack7: [{ in: hex(SEQ), out: hex(F.pack7(SEQ)) }],
  unpack7: [{ in: hex(F.pack7(SEQ)), out: hex(F.unpack7(F.pack7(SEQ))) }],
  crc16: [{ in: hex(SEQ), out: F.crc16(SEQ) }],
  jlEnc: [{ in: hex(SEQ), out: hex(F.jlEnc(SEQ)) }],
  response: [
    { addr: 0x4000, fl: 0, data: hex(DATA32), out: hex(F.response(0x4000, DATA32, 0)) },
    { addr: 0xE0000000, fl: 0, data: hex(SUCCESS), out: hex(F.response(0xE0000000, SUCCESS, 0)) },
  ],
  reqParse: WIRE_REQ.map((w) => Object.assign({ wire: w }, F.parseRequest(fromHex(w)))),
  idParse: WIRE_ID.map((w) => ({ wire: w, text: (F.parseIdentity(fromHex(w)) || {}).text })),
  pairOutput: [
    { name: "SMK-37 Pro", outs: ["SMK-37 Pro"], out: F.pairOutput("SMK-37 Pro", ["SMK-37 Pro"]) },
    { name: "SMK-37 Pro 2", outs: ["SMK-37 Pro"], out: F.pairOutput("SMK-37 Pro 2", ["SMK-37 Pro"]) },
    { name: "SMK-37 Pro", outs: ["SMK-37 Pro 1"], out: F.pairOutput("SMK-37 Pro", ["SMK-37 Pro 1"]) },
    { name: "Foo", outs: ["Only"], out: F.pairOutput("Foo", ["Only"]) },
    { name: "Foo", outs: ["A", "B"], out: F.pairOutput("Foo", ["A", "B"]) },
  ],
};

// ----------------------------------------------------------------- packages ---

const pkgs = pkgPaths.map((p) => ({ name: p.split("/").pop(), raw: new Uint8Array(fs.readFileSync(p)) }));
const packages = {};
for (const { name, raw } of pkgs) {
  let summary;
  try {
    const product = F.productOf(raw);
    const { image, blocks } = F.deinterleave(raw);
    summary = { product, blocks, damage: F.packageDamage(image) };
  } catch (e) { summary = { error: String(e.message || e) }; }
  packages[name] = summary;
}

// --------------------------------------------------------------- fake device ---
//
// The device side of the update, from ota.c (start: header, the staged loader, 0xE0000000) and
// ldr_core.c (write: the whole flash.bin, 0xF0000000). It sends read requests and validates
// every byte the host serves; a wrong answer aborts the update.

function deviceMsg(cmd, body) {
  const msg = [0, 0x59, cmd, body.length, 0, 0, ...body];
  let s = 0; for (const b of body) s += b;
  msg.push((~s) & 0xFF);
  return concat([0xF0], F.pack7(msg), [0xF7]);
}
function identMsg(text) {
  const idb = new Array(27).fill(0);
  Array.from(text).forEach((c, i) => { idb[i] = c.charCodeAt(0); });
  return deviceMsg(0x11, idb);
}
function parseAnswer(pkt) {
  // a response carries data, so its checksum covers the whole body (ota_take), unlike a
  // request's: parse it here rather than through parseRequest.
  if (!pkt || pkt.length < 2 || pkt[0] !== 0xF0 || pkt[pkt.length - 1] !== 0xF7) return null;
  const u = F.unpack7(pkt.subarray(1, pkt.length - 1));
  if (u.length < 15 || u[0] !== 0x00 || u[1] !== 0x59 || u[2] !== 0x30) return null;
  const n = u[11] | (u[12] << 8) | (u[13] << 16);
  if (u.length !== n + 15) return null;
  let s = 0; for (let i = 6; i < u.length - 1; i++) s += u[i];
  if (((~s) & 0xFF) !== u[u.length - 1]) return null;
  return { addr: (u[7] | (u[8] << 8) | (u[9] << 16) | (u[10] << 24)) >>> 0, n, data: u.subarray(14, 14 + n) };
}

const APP_ID = "SMK-37 Pro_700", LOADER_ID = "ota-SMK-37 Pro_700";

function makeDevice(image) {
  let phase = "app";
  let error = null;
  const hostQ = [], hostW = { w: null };
  const respQ = [], respW = { w: null };

  const readHost = (ms) => new Promise((res) => {
    if (hostQ.length) return res(hostQ.shift());
    const t = setTimeout(() => { if (hostW.w === cb) hostW.w = null; res(null); }, Math.max(0, ms));
    const cb = (v) => { clearTimeout(t); res(v); };
    hostW.w = cb;
  });
  const toHost = (m) => { if (hostW.w) { const w = hostW.w; hostW.w = null; w(m); } else hostQ.push(m); };

  const ask = async (addr, len) => {
    const pkt = deviceMsg(0x30, [0, addr & 0xFF, (addr >>> 8) & 0xFF, (addr >>> 16) & 0xFF, (addr >>> 24) & 0xFF,
      len & 0xFF, (len >>> 8) & 0xFF, 0]);
    toHost(pkt);
    for (;;) {
      const p = respQ.length ? respQ.shift() : await new Promise((res) => { respW.w = res; });
      const a = parseAnswer(p);
      if (a && a.addr === addr && a.n === len) return a.data;
    }
  };
  const expectSuccess = async (addr) => {
    const d = await ask(addr, 8);
    if (latin1(d) !== "success\0") throw new Error("device: no success at 0x" + addr.toString(16));
  };
  const sameAsServed = (got, addr, n) => {
    const want = image.subarray(addr, addr + n);
    if (!eqBytes(got, want)) throw new Error("device: served 0x" + addr.toString(16) + " differs from the package");
  };

  const ufw = async () => {
    const hdr = new Uint8Array(1024);
    hdr.set(await ask(0, 512), 0);
    hdr.set(await ask(512, 512), 512);
    const h = F.jlEnc(hdr.subarray(0, 0x40));
    const nent = rd16(h, 8);
    let fl = null, ota = null;
    for (let i = 0; i < nent; i++) {
      const e = F.jlEnc(hdr.subarray(0x40 + i * 0x50, 0x40 + (i + 1) * 0x50));
      const typ = rd16(e, 0);
      const off = rd32(e, 8), size = rd32(e, 12);
      if (typ === 0) fl = { off, size, dcrc: rd16(e, 4) };
      else if (typ === 100) ota = { off, size };
    }
    return { fl, ota };
  };

  // step 1: ota.c — the header, the staged loader, then the host's "success"
  const checkStep = async () => {
    const { fl, ota } = await ufw();
    if (!fl || !ota) throw new Error("device: no flash.bin/ota.bin");
    const h = await ask(ota.off, 512);
    if (!F.hasLoaderMark(h)) throw new Error("device: no Felucca loader in the staged entry");
    const len = rd32(h, 8);
    let crc = 0;
    for (let i = 0; i < len; i += 512) {
      const n = Math.min(512, len - i);
      crc = F.crc16(await ask(ota.off + 0x20 + i, n), crc);
    }
    if (crc !== rd16(h, 2)) throw new Error("device: staged loader CRC");
    await expectSuccess(0xE0000000);
    phase = "loader";
  };

  // step 2: ldr_core.c — the whole flash.bin (head, app area, tail), then the host's "success"
  const writeStep = async () => {
    const { fl } = await ufw();
    if (!fl || fl.size < 0x9D000) throw new Error("device: flash.bin too small");
    let crc = 0;
    const readInto = async (addr, n) => { const d = await ask(addr, n); sameAsServed(d, addr, n); crc = F.crc16(d, crc); };
    for (let s = 0; s < 0x4000; s += 512) await readInto(fl.off + s, 512);
    for (let s = 0x4000; s < 0x9D000; s += 0x1000) for (let k = 0; k < 0x1000; k += 512) await readInto(fl.off + s + k, 512);
    for (let s = 0x9D000; s < fl.size; s += 512) await readInto(fl.off + s, Math.min(512, fl.size - s));
    if (crc !== fl.dcrc) throw new Error("device: flash.bin CRC (host served the wrong image)");
    await expectSuccess(0xF0000000);
    phase = "app";
  };

  const start = (ph) => { Promise.resolve(ph === "app" ? checkStep() : writeStep()).catch((e) => { error = e; }); };

  const onHost = (pkt) => {
    if (eqBytes(pkt, F.HS_QUERY)) { toHost(identMsg(phase === "loader" ? LOADER_ID : APP_ID)); return; }
    if (eqBytes(pkt, F.UPGRADE)) { start(phase); return; }
    if (respW.w) { const w = respW.w; respW.w = null; w(pkt); } else respQ.push(pkt);
  };

  const backend = {
    inputNames: () => [APP_ID],
    outputNames: () => [APP_ID],
    open: () => ({
      link: {
        name: APP_ID,
        send: (pkt) => { onHost(pkt); return true; },
        read: (ms) => readHost(ms),
        drain: () => { hostQ.length = 0; },
        get lost() { return false; },
        close: () => {},
      },
    }),
  };
  return { backend, getError: () => error, getPhase: () => phase };
}

// ------------------------------------------------------------------- run it ---

const source = pkgs[0];
let install = { ok: false };
try {
  const { image } = F.loadPackage(source.raw, true);
  const dev = makeDevice(image);
  const delays = { open: 0, start: 0, reply: 0, loader: 0, reboot: 0, retry: 0.02, poll: 0.02, hs: 0.2,
    idleCheck: 2, idleWrite: 5, waitLoader: 3, waitReboot: 3 };
  const up = new F.Updater(dev.backend, { delays });
  const found = await up.find();
  if (!found) throw new Error("no device");
  const product = found.id.text;
  const back = await up.install(found, image, product);
  const err = dev.getError();
  install = { ok: back === product && !err, product: back, phase: dev.getPhase(), error: err ? String(err.message || err) : null };
} catch (e) {
  install = { ok: false, error: String((e && e.message) || e) };
}

console.log(JSON.stringify({ wire, packages, install }));
