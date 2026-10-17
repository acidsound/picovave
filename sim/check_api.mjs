// SPDX-License-Identifier: GPL-3.0-only
// Cross-check the simulator against the wasm builds it will be served with.
//
// This exists because a renamed export once shipped a page that still called the old name:
// the boot test and the wasm were consistent, but the page was not, and no check looked at
// the page. Now every simulator export the page or the worklet names must exist.
//
// The page runs picovave in picovave's own AudioWorklet engine (picovave/sim/worklet.js). So the members
// the simulator reads off the wasm are named both in the page (the silent main-thread runner)
// and in worklet.js (the audio engine). Both are scanned, and the wasm must expose the whole
// contract.
//
// Files are classified by extension: .wasm are the builds, everything else is scanned.
//
//   node picovave/sim/check_api.mjs sim.wasm index.html worklet.js
import fs from "fs";
import os from "os";
import path from "path";
import { spawnSync } from "child_process";

const wasmPaths = [], scanPaths = [];
for (const a of process.argv.slice(2)) {
  if (a.endsWith(".wasm")) wasmPaths.push(a);
  else scanPaths.push(a);
}
if (wasmPaths.length === 0 || scanPaths.length === 0) {
  console.error("usage: node check_api.mjs sim.wasm index.html [worklet.js ...]");
  process.exit(2);
}

async function instantiate(p) {
  const mod = await WebAssembly.compile(fs.readFileSync(p));
  const imports = {};
  for (const i of WebAssembly.Module.imports(mod)) {
    imports[i.module] = imports[i.module] || {};
    if (i.kind === "function") imports[i.module][i.name] = () => 0;
  }
  const instance = await WebAssembly.instantiate(mod, imports);
  return new Set(Object.keys(instance.exports));
}

// The engine the simulator drives: the screen, the digital inputs, and the worklet contract
// (the audio clock that runs the device). picovave/sim/sim.c must speak all of it, because
// picovave/sim/worklet.js runs it inside the AudioWorklet.
const CORE = [
  "memory", "sim_init", "sim_fb", "sim_draws",
  "sim_rate", "sim_render", "sim_out_l", "sim_out_r", "sim_pads", "sim_leds",
  "sim_button", "sim_key", "sim_enc", "sim_pad", "sim_fader", "sim_wheel", "sim_midi",
  "sim_sysex_in", "sim_sysex", "sim_cart_active", "sim_reply", "sim_reply_len",
];

let fails = 0;
const check = (what, ok, extra = "") => {
  console.log("api: " + what.padEnd(56) + " " + (ok ? "ok" : "FAIL") + (extra ? "  " + extra : ""));
  if (!ok) fails++;
};

const strip = (t) => t.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");

const union = new Set();
for (const p of wasmPaths) {
  const exports = await instantiate(p);
  for (const n of exports) union.add(n);
  const miss = CORE.filter((n) => !exports.has(n));
  check(p.split("/").pop() + " exports the simulator contract", miss.length === 0,
        miss.length ? "missing: " + miss.join(", ") : CORE.length + " names");
}

// Every sim_* name the simulator's own code mentions must be a real export.
const nameRe = /\bsim_[A-Za-z0-9_]+/g;
for (const scanPath of scanPaths) {
  const raw = fs.readFileSync(scanPath, "utf8");
  const code = strip(raw);
  const named = [...new Set([...code.matchAll(nameRe)].map((m) => m[0]))];
  const missing = named.filter((n) => !union.has(n));
  check(scanPath.split("/").pop() + " names only real exports", missing.length === 0,
        missing.length ? "missing: " + missing.join(", ") : named.length + " names");
}

// The page's inline module script (and any scanned .js) must at least parse. A syntax error
// there is invisible to every other check (it never reaches the wasm) and shows up only in
// the browser console.
for (const scanPath of scanPaths) {
  const raw = fs.readFileSync(scanPath, "utf8");
  const name = path.basename(scanPath);
  let body = raw;
  if (scanPath.endsWith(".html")) {
    const m = raw.match(/<script type="module">([\s\S]*?)<\/script>/);
    if (!m) { check(name + " has a module script", false); continue; }
    body = m[1];
  }
  const tmp = path.join(os.tmpdir(), "picovave_page_check_" + process.pid + (scanPath.endsWith(".html") ? ".mjs" : ".js"));
  fs.writeFileSync(tmp, body);
  const r = spawnSync(process.execPath, ["--check", tmp], { encoding: "utf8" });
  fs.unlinkSync(tmp);
  check(name + " parses", r.status === 0,
        r.status ? (r.stderr.split("\n").slice(0, 2).join(" ").trim()) : "");
}

console.log(fails ? "api: " + fails + " FAILED" : "api: all ok");
process.exit(fails ? 1 : 0);
