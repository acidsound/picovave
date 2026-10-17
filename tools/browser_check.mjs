// SPDX-License-Identifier: GPL-3.0-only
// Drive the served page in a real browser over CDP: this is the one path headless Node
// cannot exercise - picovave's AudioWorklet as the engine the firmware runs in. Not part
// of the normal build (needs Chrome); run by hand: node tools/browser_check.mjs [PORT]
import fs from "fs";

const PORT = process.argv[2] || "9222";
const CDP = "http://127.0.0.1:" + PORT;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function findPage() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(CDP + "/json/list")).json();
      const p = list.find((t) => t.type === "page" && /^http/.test(t.url));
      if (p) return p;
    } catch (e) { /* not up yet */ }
    await sleep(250);
  }
  return null;
}
const page = await findPage();
if (!page) { console.log("browser: no page target (is Chrome on --remote-debugging-port=" + PORT + "?)"); process.exit(1); }

const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const events = [];
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method === "Runtime.exceptionThrown") events.push("exception: " + JSON.stringify(m.params.exceptionDetails.exception));
  else if (m.method === "Log.entryAdded" && m.params.entry.level === "error") {
    const u = m.params.entry.url || "";
    if (!/favicon\.ico$/.test(u)) events.push("log: " + m.params.entry.text);   // the browser's own favicon probe is not ours
  }
});
await new Promise((r) => ws.addEventListener("open", r));
const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });

await send("Runtime.enable");
await send("Log.enable");
await send("Page.enable");

const SHOT = process.env.SHOT;
async function screenshot(path) {
  const r = await send("Page.captureScreenshot", { format: "png" });
  if (r.result && r.result.data) { fs.writeFileSync(path, Buffer.from(r.result.data, "base64")); console.log("      wrote " + path); }
}

async function evalIn(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result && r.result.exceptionDetails) return { error: JSON.stringify(r.result.exceptionDetails) };
  return r.result && r.result.result ? r.result.result.value : undefined;
}

let fails = 0;
const check = (what, ok, extra) => { console.log((ok ? "ok  " : "FAIL") + " " + what + (extra ? "  " + extra : "")); if (!ok) fails++; };

// This script drives one boot and leaves the page in whatever state the last check produced, so
// start from a fresh load: a repeat run must not inherit the first (or a previous probe).
await send("Page.navigate", { url: page.url });
for (let i = 0; i < 40; i++) {
  const s = await evalIn(`(document.getElementById("status") || {}).textContent || ""`);
  if (s && !/wasm/.test(s)) break;
  await sleep(250);
}

const audio = await evalIn("typeof AudioContext === 'function' && ('audioWorklet' in AudioContext.prototype || typeof AudioWorkletNode === 'function')");
check("a real browser with AudioWorklet is running the page", audio === true, String(audio));

// Tap the worklet's output into an AnalyserNode so the test can hear what the engine sends to
// the speakers. Patched before the sound is switched on (the node is created at the first gesture).
await evalIn(`(() => {
  window.__probe = null;
  const orig = AudioWorkletNode.prototype.connect;
  AudioWorkletNode.prototype.connect = function (dest) {
    try {
      const an = this.context.createAnalyser();
      an.fftSize = 2048;
      window.__probe = an;
      orig.call(this, an);
    } catch (e) { window.__tapErr = String(e); }
    return orig.call(this, dest);
  };
  return true;
})()`);
const peak = async () => await evalIn(`(() => {
  const an = window.__probe; if (!an) return -1;
  const a = new Float32Array(an.fftSize);
  an.getFloatTimeDomainData(a);
  let p = 0; for (let i = 0; i < a.length; i++) { const v = a[i] < 0 ? -a[i] : a[i]; if (v > p) p = v; }
  return p;
})()`);

// The reported bug: audio must come up for picovave from the first real interaction. A genuine
// input event is the gesture the autoplay policy needs, so drive one through the browser's
// input pipeline (a trusted gesture), then confirm picovave moved into the AudioWorklet with
// sound on.
async function realClick(x, y) {
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
}
await realClick(6, 6);
const hl0 = await evalIn(`(async () => {
  await new Promise(r => setTimeout(r, 2000));
  return JSON.stringify({ status: document.getElementById("status").textContent,
                          brand: document.getElementById("brand").textContent,
                          soundOn: document.getElementById("sound").className,
                          draws: document.getElementById("draws").textContent });
})()`);
const H0 = hl0 && hl0.error ? {} : JSON.parse(hl0 || "{}");
check("picovave starts the audio engine from the first gesture",
      /실행 중/.test(H0.status || "") && /사운드/.test(H0.status || "") && H0.brand === "PICOVAVE",
      JSON.stringify(H0.status));
check("picovave draws frames from the worklet right away", (+H0.draws || 0) > 1, "draws=" + H0.draws);

// Re-apply picovave through the SIMULATOR: the engine is already up, so this just moves picovave
// into the AudioWorklet (SIMULATOR = apply on the web; FLASH(USB) writes to a real device).
const hl = await evalIn(`(async () => {
  document.getElementById("simPicovave").click();
  await new Promise(r => setTimeout(r, 2000));
  return JSON.stringify({ status: document.getElementById("status").textContent,
                          cls: document.getElementById("status").className,
                          brand: document.getElementById("brand").textContent,
                          device: document.getElementById("device").textContent,
                          draws: document.getElementById("draws").textContent,
                          soundOn: document.getElementById("sound").className });
})()`);
const H = hl && hl.error ? {} : JSON.parse(hl || "{}");
check("SIMULATOR picovave runs it in the audio worklet (sound on)",
      /실행 중/.test(H.status || "") && /사운드/.test(H.status || "") && H.brand === "PICOVAVE",
      JSON.stringify(H.status));
check("picovave draws frames from the worklet", (+H.draws || 0) > 1, "draws=" + H.draws);
check("the SOUND control is on", /on/.test(H.soundOn || ""), H.soundOn);

// picovave makes sound: a square wave at the held key's pitch.

// The square wave: silent at rest, a tone while key 24 (C5, 523 Hz) is held, silent after release.
const rest = await peak();
await evalIn(`document.getElementById("key24").dispatchEvent(new PointerEvent("pointerdown", { pointerId: 1, bubbles: true }))`);
await sleep(500);
const tone = await peak();
await evalIn(`document.getElementById("key24").dispatchEvent(new PointerEvent("pointerup", { pointerId: 1, bubbles: true }))`);
await sleep(500);
const after = await peak();
check("picovave is silent with no key held", rest >= 0 && rest < 0.05, "peak=" + rest);
check("picovave plays a square wave while a key is held", tone > 0.1, "peak=" + tone);
check("picovave goes silent when the key is released", after >= 0 && after < 0.05, "peak=" + after);

// FLASH(USB) is the real path. In this headless browser Web MIDI exists but permission is refused
// (and no device is attached), so it must fail cleanly with a reason and leave the simulator alone.
const fl = await evalIn(`(async () => {
  document.getElementById("simPicovave").click();
  document.getElementById("flashPicovave").click();
  await new Promise(r => setTimeout(r, 2500));
  return JSON.stringify({ status: document.getElementById("status").textContent,
                          log: document.getElementById("flashlog").textContent,
                          device: document.getElementById("device").textContent });
})()`);
const FL = fl && fl.error ? {} : JSON.parse(fl || "{}");
check("FLASH(USB) starts a real SysEx flash and reports the outcome",
      /FLASH\(USB\) picovave\.fwsc/.test(FL.log || "") && /identity "SMK-37 Pro_700"/.test(FL.log || ""),
      JSON.stringify((FL.log || "").split("\\n")[0]));
check("FLASH(USB) leaves the simulated device untouched", FL.device === "PICOVAVE", FL.device);

// When auto-find comes up empty, the page must list the MIDI ports and let the user pick one. Web
// MIDI is stubbed with a single unresponsive port (an unusual name and no identity answer), which
// is exactly the case a user needs the list for.
await evalIn(`(() => {
  const input = { name: "Weird Controller", id: "in1", state: "connected", onmidimessage: null };
  const output = { name: "Weird Controller", id: "out1", state: "connected", send() {} };
  navigator.requestMIDIAccess = async () => ({ inputs: new Map([["in1", input]]), outputs: new Map([["out1", output]]) });
  return true;
})()`);
const scan = await evalIn(`(async () => {
  document.getElementById("midiscan").click();
  await new Promise(r => setTimeout(r, 2500));
  return document.getElementById("flashlog").textContent;
})()`);
check("MIDI port diagnostic lists every port", /Weird Controller/.test(scan || ""), JSON.stringify((scan || "").split("\n")[3]));
const lp = await evalIn(`(async () => {
  document.getElementById("flashPicovave").click();
  await new Promise(r => setTimeout(r, 3000));
  return JSON.stringify({ log: document.getElementById("flashlog").textContent,
                          opts: Array.from(document.getElementById("midiport").options).map(o => o.value) });
})()`);
const LP = lp && lp.error ? {} : JSON.parse(lp || "{}");
check("FLASH(USB) probes every port when auto-find fails",
      /자동으로 찾지 못했다/.test(LP.log || "") && /Weird Controller/.test(LP.log || "") &&
      (LP.opts || []).includes("Weird Controller"),
      JSON.stringify(LP.opts));
const pick = await evalIn(`(async () => {
  const sel = document.getElementById("midiport");
  sel.value = "Weird Controller"; sel.dispatchEvent(new Event("change"));
  document.getElementById("flashPicovave").click();
  await new Promise(r => setTimeout(r, 6000));
  return document.getElementById("flashlog").textContent;
})()`);
check("FLASH(USB) uses the picked port, then the rest, and says it did not answer",
      /선택한 포트 Weird Controller 가 응답하지 않는다/.test(pick || ""),
      JSON.stringify((pick || "").split("\n").pop()));

// And the case the whole diagnostic exists for: one cable DOES answer - the page must find it,
// name it, and preselect it for FLASH(USB).
const answered = await evalIn(`(async () => {
  const hex = "f000324558010040294d16351973060828725e7d3a03060c0000000000000000000000000000400df7";
  const ID = hex.match(/../g).map(h => parseInt(h, 16));
  const input = { name: "SMK-37 Pro -Private", id: "in1", state: "connected", onmidimessage: null, open() {} };
  const output = { name: "SMK-37 Pro -Private", id: "out1", state: "connected",
                   send() { setTimeout(() => { if (input.onmidimessage) input.onmidimessage({ data: Uint8Array.from(ID) }); }, 20); } };
  navigator.requestMIDIAccess = async () => ({ inputs: new Map([["in1", input]]), outputs: new Map([["out1", output]]) });
  document.getElementById("midiscan").click();
  await new Promise(r => setTimeout(r, 2500));
  return JSON.stringify({ log: document.getElementById("flashlog").textContent,
                          sel: document.getElementById("midiport").value });
})()`);
const AS = answered && answered.error ? {} : JSON.parse(answered || "{}");
check("MIDI port diagnostic finds and selects the port that answers",
      AS.sel === "SMK-37 Pro -Private" && /SMK-37 Pro_700/.test(AS.log || ""), JSON.stringify(AS.sel));

// A multi-cable device may not pair in/out by name: the identity arrives on the -Private INPUT but
// only when the query goes out on -Master. The diagnostic must try the other cables, not just the
// same-named output.
const xcable = await evalIn(`(async () => {
  const hex = "f000324558010040294d16351973060828725e7d3a03060c0000000000000000000000000000400df7";
  const ID = hex.match(/../g).map(h => parseInt(h, 16));
  const inn = { name: "SMK-37 Pro -Private", id: "in1", state: "connected", onmidimessage: null, open() {} };
  const silent = { name: "SMK-37 Pro -Private", id: "o1", state: "connected", send() {} };
  const master = { name: "SMK-37 Pro -Master", id: "o2", state: "connected",
                   send() { setTimeout(() => { if (inn.onmidimessage) inn.onmidimessage({ data: Uint8Array.from(ID) }); }, 20); } };
  navigator.requestMIDIAccess = async () => ({ inputs: new Map([["in1", inn]]), outputs: new Map([["o1", silent], ["o2", master]]) });
  document.getElementById("midiscan").click();
  await new Promise(r => setTimeout(r, 6000));
  return JSON.stringify({ log: document.getElementById("flashlog").textContent,
                          sel: document.getElementById("midiport").value });
})()`);
const XC = xcable && xcable.error ? {} : JSON.parse(xcable || "{}");
check("MIDI port diagnostic also tries the other cables when the named output is silent",
      XC.sel === "SMK-37 Pro -Private" && /SMK-37 Pro_700/.test(XC.log || "") && /출력: SMK-37 Pro -Master/.test(XC.log || ""),
      JSON.stringify((XC.log || "").split("\n").slice(-4).join(" | ")));

check("the page reported no runtime error", events.length === 0, events.join("; ").slice(0, 200));

if (SHOT) await screenshot(SHOT);

console.log(fails ? "browser: " + fails + " FAILED" : "browser: all ok");
process.exit(fails ? 1 : 0);
