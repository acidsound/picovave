// SPDX-License-Identifier: GPL-3.0-only
// Run web/sim/index.html's own inline module script for real, against a minimal DOM and the
// real wasm. The page is NOT re-implemented here - it is executed.
//
// This is the check that was missing twice:
//   1) a renamed wasm export shipped a page that called the old name (ex.sim_ms).
//      A text scan (check_api.mjs) now covers that.
//   2) "mod is not defined": a const declared inside a try block and used after it. That is
//      valid syntax and invisible to every static check - only running the page catches it.
//
// The page runs picovave in its own AudioWorklet engine (picovave/sim/worklet.js). In a browser that
// engine is audio; headless there is no AudioContext, so the page's silent main-thread runner
// drives the same wasm through the same message protocol. That is what runs here: the panel
// presses, the DEVICE box (SIMULATOR and FLASH(USB)) - the page's own code, for real.
//
//   node picovave/sim/run_page.mjs sim.wasm index.html
import fs from "fs";
import os from "os";
import path from "path";
import { pathToFileURL } from "url";

const wasmPaths = [], htmlArgs = [];
for (const a of process.argv.slice(2)) {
  if (a.endsWith(".html")) htmlArgs.push(a);
  else wasmPaths.push(a);
}
const htmlPath = htmlArgs[0];
if (wasmPaths.length === 0 || !htmlPath) {
  console.error("usage: node run_page.mjs sim.wasm index.html");
  process.exit(2);
}
const serveDir = path.dirname(path.resolve(htmlPath));   // the page fetches ./sim.wasm, ./picovave.fwsc, ...

let fails = 0;
function check(what, ok, extra) {
  console.log((ok ? "ok  " : "FAIL") + " " + what + (extra ? "  " + extra : ""));
  if (!ok) fails++;
}

const html = fs.readFileSync(htmlPath, "utf8");
const m = html.match(/<script type="module">([\s\S]*?)<\/script>/);
if (!m) { check("page has a module script", false); process.exit(1); }

/* ---- a DOM just rich enough for this page ---- */
const elements = {};
const listeners = {};
let paints = 0;

function makeStyle() {
  const s = {};
  s.setProperty = function (k, v) { s[k] = v; };
  return s;
}

function makeEl(id) {
  /* A real class list: the page lights a pad, a key or a button by putting "down"/"lit" on it,
   * so a test that presses something has to be able to see what it lit. */
  const classes = new Set();
  const el = {
    textContent: "",
    innerHTML: "",
    className: "",
    value: "",
    width: 240,
    height: 240,
    style: makeStyle(),
    classList: {
      toggle: function (c, on) {
        if (on === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); }
        else if (on) classes.add(c); else classes.delete(c);
        return classes.has(c);
      },
      add: function (c) { classes.add(c); }, remove: function (c) { classes.delete(c); },
      contains: function (c) { return classes.has(c); }
    },
    // keyed by the element's CURRENT id, so a created element that sets el.id in the page
    // (buttons "btn6", pads "pad0", keys "key12", ...) can be fired from a test.
    addEventListener: function (ev, fn) {
      const k = this.id || id;
      listeners[k] = listeners[k] || {};
      listeners[k][ev] = fn;
    },
    setPointerCapture: function () {},
    appendChild: function () {},
    getContext: function () {
      // putImageData is the wasm screen; fillRect/fillText are the DEVICE box's
      // "cannot run this ROM" screen, which the page draws with the 2D context itself.
      return {
        createImageData: function (w, h) {
          return { data: new Uint8ClampedArray(w * h * 4), width: w, height: h };
        },
        putImageData: function () { paints++; },
        fillRect: function () {}, fillText: function () {}, strokeRect: function () {},
        save: function () {}, restore: function () {},
        measureText: function () { return { width: 0 }; }
      };
    }
  };
  el.firstChild = { style: makeStyle() };          /* the knobs poke at their dial */
  /* The page names its panel elements as it builds them (btn6, pad0, key12, ...). Keep the
   * registry keyed by the CURRENT id, so a test can read what a pad was given and what it lights. */
  let current = id;
  Object.defineProperty(el, "id", {
    get: function () { return current; },
    set: function (v) { if (listeners[current]) listeners[v] = listeners[current]; current = v; elements[v] = el; },
    configurable: true, enumerable: true
  });
  elements[id] = el;
  return el;
}

globalThis.document = {
  getElementById: function (id) { return elements[id] || makeEl(id); },
  createElement: function () { return makeEl("el" + (elements.__n = (elements.__n || 0) + 1)); }
};
const winListeners = {};
globalThis.addEventListener = function (ev, fn) { (winListeners[ev] = winListeners[ev] || []).push(fn); };
function fireKey(type, key) {
  for (const fn of (winListeners[type] || [])) fn({ key: key, repeat: false, ctrlKey: false, metaKey: false });
}
function fire(id, ev, arg) { const l = listeners[id] && listeners[id][ev]; if (l) l(arg || {}); }
globalThis.fetch = async function (url) {
  // The page loads both wasms and, for the DEVICE box, the packages served next to it.
  const b = fs.readFileSync(path.join(serveDir, path.basename(String(url))));
  return {
    ok: true, status: 200,
    arrayBuffer: async function () { return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); }
  };
};

const uncaught = [];
process.on("unhandledRejection", function (e) { uncaught.push(String((e && e.message) || e)); });

/* keep the page's relative imports (./picovave_render.js, ./fwsc.mjs) working from a temp file elsewhere */
const body = m[1].replace(/from\s+"\.\/([^"]+)"/g, function (all, f) {
  return 'from "' + pathToFileURL(path.join(serveDir, f)).href + '"';
});
const tmp = path.join(os.tmpdir(), "picovave_page_under_test_" + process.pid + ".mjs");
fs.writeFileSync(tmp, body);

try {
  await import(pathToFileURL(tmp).href);
} catch (e) {
  check("page module evaluated", false, String((e && e.message) || e));
}
fs.unlinkSync(tmp);

/* wait for the page's own boot() to settle */
const deadline = Date.now() + 3000;
const LOADING = "wasm " + String.fromCharCode(47196, 46377) + " " + String.fromCharCode(51473, 8230);
while (Date.now() < deadline) {
  if (uncaught.length) break;
  const s = elements["status"];
  if (s && s.textContent !== "" && s.textContent !== LOADING) break;
  await new Promise(function (r) { setTimeout(r, 25); });
}

const status = elements["status"] || { textContent: "", className: "" };
const running = /실행 중/.test(status.textContent || "");
const counters = ["draws", "boots", "ms", "bytes", "wdt"];
const shown = {};
counters.forEach(function (k) { shown[k] = (elements[k] || {}).textContent; });
const txt = (id) => (elements[id] || {}).textContent || "";

check("page threw no uncaught error", uncaught.length === 0, uncaught.join("; "));
check("page boot() completed", running, JSON.stringify(status.textContent || "").slice(0, 140));
check("page is not in an error state", !String(status.className).includes("err"));
check("page painted the screen", paints > 0, paints + " paints");
check("page filled the device counters", counters.every(function (k) { return shown[k] !== "" && shown[k] !== undefined; }), JSON.stringify(shown));
check("page wired POWER-ON", !!(listeners["boot"] && listeners["boot"].click));
check("page wired TICK", !!(listeners["refresh"] && listeners["refresh"].click));
check("page wired SOUND", !!(listeners["sound"] && listeners["sound"].click));
check("page defaults to PICOVAVE", txt("device") === "PICOVAVE", txt("device"));
check("page offers a Korean/English switch",
      !!(listeners["langKo"] && listeners["langKo"].click &&
         listeners["langEn"] && listeners["langEn"].click), "");
fire("langEn", "click");
check("English switch translates the static UI and the current status",
      txt("statssum") === "Stats" && txt("lblDraws") === "draws" &&
      /^Running ·/.test(txt("status")) && /flash \/ carts/.test(txt("devtitle")) &&
      /Upload cart/.test(txt("cartSend")) && txt("helpsumm") === "Help · key map",
      txt("status").slice(0, 60) + " | " + txt("cartSend"));
fire("langKo", "click");
check("Korean switch restores the Korean UI and the current status",
      txt("statssum") === "상태" && txt("lblDraws") === "갱신" &&
      /^실행 중 ·/.test(txt("status")) && /플래시 \/ 카트/.test(txt("devtitle")) &&
      /카트\(\.p8\.png\) 업로드/.test(txt("cartSend")),
      txt("status").slice(0, 60) + " | " + txt("cartSend"));
check("page would load picovave's worklet as the audio engine", /\.\/worklet\.js/.test(html));
check("page wires SIMULATOR and FLASH(USB) as separate controls",
      !!(listeners["simPicovave"] && listeners["simRestore"] &&
         listeners["flashPicovave"] && listeners["flashRestore"]), "");
check("page wires the MIDI port list and scan",
      !!(listeners["midiport"] && listeners["midiscan"]), "");
check("page wires the cart upload and erase",
      !!(listeners["cartp8"] && listeners["cartSend"] && listeners["cartErase"]), "");
check("page offers a cart TARGET select (SIMULATOR vs FLASH(USB))", /id="cartmode"/.test(html), "");

// The gallery: a curated grid of community carts, each card naming the thread it came from. Every
// card must point at a file the build actually serves (tools/build_sim.py copies carts/*.p8.png),
// or the page would offer a one-click transfer that 404s.
const grid = (elements["galleryGrid"] || {}).innerHTML || "";
const cardFiles = (grid.match(/data-cart="([^"]+)"/g) || []).map(function (s) { return s.slice(11, -1); });
const missingCards = cardFiles.filter(function (f) {
  return !fs.existsSync(path.join(serveDir, "carts", f));
});
check("page shows a gallery of community carts", cardFiles.length >= 8 &&
      new Set(cardFiles).size === cardFiles.length, cardFiles.length + " cards");
check("every gallery card is a cart the build serves",
      cardFiles.length > 0 && missingCards.length === 0, missingCards.join(", ") || "all served");
check("every gallery card links to its source thread",
      (grid.match(/href="https:\/\/www\.lexaloffle\.com\/bbs\/\?[pt]id=\d+"/g) || []).length === cardFiles.length,
      (grid.match(/lexaloffle\.com\/bbs\/\?[pt]id=/g) || []).length + " links");
// A .p8.png IS the label, so the card's artwork is the cart it sends - no second image to drift.
const thumbs = (grid.match(/class="cart-thumb" src="\.\/carts\/([^"]+)"/g) || [])
  .map(function (s) { return s.slice(s.indexOf("./carts/") + 8, -1); });
check("every gallery card shows the cart's own label image",
      thumbs.length === cardFiles.length && thumbs.join("|") === cardFiles.join("|"),
      thumbs.length + " thumbnails");
// The grid is alphabetical by title, whatever order the list is written in.
const titles = (grid.match(/class="cart-name">([^<]*)</g) || [])
  .map(function (s) { return s.slice(18, -1).replace(/&amp;/g, "&"); });
const sorted = titles.slice().sort(function (a, b) {
  return a.localeCompare(b, "en", { sensitivity: "base" });
});
check("the gallery is alphabetical by cart name",
      titles.length > 1 && titles.join(" | ") === sorted.join(" | "), titles.join(" · "));
check("the gallery names the target the cartridge slot panel chose",
      /SIMULATOR/.test((elements["galleryTargetNote"] || {}).innerHTML || ""),
      (elements["galleryTargetNote"] || {}).innerHTML || "");
check("the gallery is reachable from the masthead", /href="#gallery"/.test(html) && /id="gallery"/.test(html), "");

// The panel presses drive the firmware through the same path the worklet uses.
try {
  const d0 = txt("draws");
  fire("btn13", "pointerdown", { pointerId: 1 });   // PARA: opens SOUND
  fire("btn13", "pointerup", {});
  check("a button press reaches the picovave firmware", txt("draws") !== d0, d0 + " -> " + txt("draws"));
} catch (e) {
  check("picovave button press works", false, String((e && e.message) || e));
}

// The panel's pads, faders and wheels reach the picovave firmware too, along the same path:
// each one is an event on its debug screen, so the frame is redrawn.
try {
  const d0 = txt("draws");
  fire("pad0", "pointerdown", { pointerId: 1 });    // a pad hit, velocity 110
  check("a pad hit reaches the picovave firmware", txt("draws") !== d0, d0 + " -> " + txt("draws"));
  fire("pad0", "pointerup", {});
  const d1 = txt("draws");
  fire("fader0", "input", { target: { value: "100" } });
  check("a fader move reaches the picovave firmware", txt("draws") !== d1, d1 + " -> " + txt("draws"));
  const d2 = txt("draws");
  fire("mod", "input", { target: { value: "80" } });   // mod wheel, raw 535
  check("a wheel move reaches the picovave firmware", txt("draws") !== d2, d2 + " -> " + txt("draws"));
} catch (e) {
  check("picovave pad/fader/wheel work", false, String((e && e.message) || e));
}

// The DEVICE box: verify the served picovave.fwsc and install it into the simulator.
try {
  check("page listed the served package", /PICOVAVE/.test(txt("pkg")),
        txt("pkg").split("\n")[0]);
  listeners["simPicovave"].click();
  check("SIMULATOR picovave writes head+app and never touches a protected region",
        /0x00000\.\.0x9d000/.test(txt("flashlog")) && /침범 없음/.test(txt("flashlog")),
        (txt("flashlog").split("\n")[2] || "").trim());
  check("SIMULATOR picovave keeps running the picovave build (sim.wasm)", txt("device") === "PICOVAVE" && /실행 중/.test(txt("status")),
        txt("device"));

  const before = txt("draws");
  listeners["refresh"].click();
  check("TICK advances the picovave loop", txt("draws") !== before, before + " -> " + txt("draws"));
  listeners["boot"].click();
  check("POWER-ON reboots picovave", parseInt(txt("boots"), 10) === 1 && parseInt(txt("draws"), 10) > 0,
        "draws=" + txt("draws") + " boots=" + txt("boots"));
} catch (e) {
  check("picovave FLASH/loop works", false, String((e && e.message) || e));
}

// The cart upload: choose a served .p8.png and send it over the device's own SysEx protocol. The
// page unwraps the image itself (web/sim/p8png.mjs) and the parser that receives the cooked cart
// is the firmware's (src/picovave_cart.h) inside the wasm, so the panel must switch to it.
let sampleCart = null;
try {
  const names = fs.readdirSync(path.join(serveDir, "carts")).filter((n) => n.endsWith(".p8.png")).sort();
  if (names.length) sampleCart = path.join(serveDir, "carts", names[0]);
} catch (e) { /* checked below */ }
if (sampleCart) {
  const sampleName = path.basename(sampleCart);
  try {
    const b = fs.readFileSync(sampleCart);
    const file = { name: sampleName,
      arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) };
    fire("cartp8", "change", { target: { files: [file] } });
    // The page measures the cart before it offers to send it - 300 frames in a throwaway wasm. On a
    // cart that lives at the edge the pool refuses and Lua collects, so this can take a moment: poll
    // for the answer instead of guessing a sleep.
    const waitFor = async (pred, ms = 20000) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) { if (pred()) return true; await new Promise((r) => setTimeout(r, 20)); }
      return pred();
    };
    await waitFor(() => /해독/.test(txt("cartsay")));        // loadCart() inflates the image
    check("page unwrapped the chosen .p8.png (160x205 -> the 32,804 B cooked payload)",
          txt("cartsay").includes(sampleName) && /해독/.test(txt("cartsay")) &&
          /32804/.test(txt("cartsay")), txt("cartsay"));
    // ...and told the user, before anything is sent, whether the HARDWARE can hold it: the pool is
    // measured by the device's own allocator over the device's own arena, not guessed from the size.
    await waitFor(() => /peak/.test(txt("cartsay")));        // the measurement's verdict line
    check("the page measured the cart in the device's arena before sending it",
          /기기 (OK|불가|빠듯)/.test(txt("cartsay")) && /거부/.test(txt("cartsay")) &&
          /peak/.test(txt("cartsay")), txt("cartsay"));
    fire("langEn", "click");
    check("English switch re-renders a loaded cart's compile and fit verdict",
          /bytecode/.test(txt("cartsay")) && /device (OK|is tight|cannot run|verdict)/.test(txt("cartsay")) &&
          !/[가-힣]/.test(txt("cartsay")), txt("cartsay"));
    fire("langKo", "click");
    if (/기기 불가/.test(txt("cartsay")))
      check("...and warned instead of claiming success", elements.status.className === "warn",
            txt("status"));
    const d0 = txt("draws");
    listeners["cartSend"].click();
    await new Promise((r) => setTimeout(r, 30));
    check("page uploaded the cart over SysEx (BEGIN/DATA/END) and it committed",
          /업로드 완료/.test(txt("status")) && /슬롯 0/.test(txt("cartsay")),
          txt("status") + " | " + txt("cartsay"));
    check("the uploaded cart runs (the panel repainted)", txt("draws") !== d0, d0 + " -> " + txt("draws"));
    listeners["boot"].click();
    check("POWER-ON re-boots the uploaded cart from the slot",
          parseInt(txt("boots"), 10) === 1 && parseInt(txt("draws"), 10) > 0,
          "draws=" + txt("draws") + " boots=" + txt("boots"));

    // The two cart targets are genuinely different paths, and the page says which is which.
    // FLASH(USB) needs Web MIDI - a headless run has none - so the same button with that target
    // chosen must fail on the TRANSPORT, and must not hand the frames to the wasm.
    const dT = txt("draws");
    fire("cartmode", "change", { target: { value: "usb" } });
    check("choosing FLASH(USB) relabels the cart buttons",
          /FLASH\(USB\)/.test(txt("cartSend")) && /FLASH\(USB\)/.test(txt("cartErase")),
          txt("cartSend") + " | " + txt("cartErase"));
    listeners["cartSend"].click();
    await new Promise((r) => setTimeout(r, 30));
    check("FLASH(USB) cart goes to the MIDI transport, not the wasm (no Web MIDI here)",
          /Web MIDI/.test(txt("status")), txt("status"));
    check("the FLASH(USB) cart attempt left the wasm panel alone", txt("draws") === dT, dT + " -> " + txt("draws"));
    fire("cartErase", "click", {});
    await new Promise((r) => setTimeout(r, 30));
    check("FLASH(USB) erase is the device's ERASE command too, not the wasm",
          /Web MIDI/.test(txt("status")), txt("status"));
    fire("cartmode", "change", { target: { value: "sim" } });
    check("choosing SIMULATOR relabels the cart buttons back",
          /SIMULATOR/.test(txt("cartSend")) && /SIMULATOR/.test(txt("cartErase")), txt("cartSend"));

    // The refusal, last (it clears the chosen cart): a text .p8 is not a cart, and the page says
    // so before anything is sent - there is exactly one cart format here.
    const text = Buffer.from("pico-8 cartridge // http://www.pico-8.com\nversion 16\n__lua__\n" +
                             "function _init() end\nfunction _update() end\nfunction _draw() cls(8) end\n");
    fire("cartp8", "change", { target: { files: [{ name: "text.p8", arrayBuffer: async () => text }] } });
    await new Promise((r) => setTimeout(r, 30));
    check("page refuses a text .p8 (only .p8.png is a cart)",
          /카트가 아니다/.test(txt("status")) && /\.p8\.png/.test(txt("status")), txt("status"));
    const dR = txt("draws");
    listeners["cartSend"].click();
    await new Promise((r) => setTimeout(r, 30));
    check("...and then there is nothing to send", /카트 \.p8\.png/.test(txt("status")), txt("status"));
    check("the refused .p8 changed nothing on the device", txt("draws") === dR, dR + " -> " + txt("draws"));

    // The far end of the gradation, and why the measurement must yield and stop early: terra_1cart-44
    // is served only by refusing - ~400 refusals a frame, each one a full GC - so the page must stop
    // long before frame 300, say how much it measured, and never claim the cart runs well. Last,
    // because it leaves its own cart chosen.
    const cartDir = path.join(serveDir, "carts");
    const violent = fs.readdirSync(cartDir)
      .filter((n) => n.startsWith("terra") && n.endsWith(".p8.png"))[0];
    if (violent) {
      const vb = fs.readFileSync(path.join(cartDir, violent));
      const dV = txt("draws");
      fire("cartp8", "change", { target: { files: [{ name: violent,
        arrayBuffer: async () => vb.buffer.slice(vb.byteOffset, vb.byteOffset + vb.byteLength) }] } });
      // Wait for THIS cart's verdict: the line still carries the previous cart's name until
      // loadCart() has inflated the image, and its verdict ("peak") until the measurement is done.
      await waitFor(() => txt("cartsay").includes(violent) && /peak/.test(txt("cartsay")));
      check("the page reports a cart the pool only serves by refusing, and warns",
            /기기 불가/.test(txt("cartsay")) && /거부/.test(txt("cartsay")) &&
            elements.status.className === "warn", txt("cartsay") + " | " + txt("status"));
      check("...and stopped measuring early instead of freezing on it",
            /첫 \d+프레임/.test(txt("cartsay")), txt("cartsay"));
      check("...and choosing a cart changed nothing on the wasm panel", txt("draws") === dV,
            dV + " -> " + txt("draws"));
    } else {
      console.log("skip the refusal-gradation test (no terra*.p8.png served)");
    }
  } catch (e) {
    check("page cart upload works", false, String((e && e.message) || e));
  }
} else {
  console.log("skip CART upload test (no served .p8.png carts; run tools/build_sim.py)");
}

// The SIMULATOR restore path, exercised with the package we ship: dropping picovave.fwsc must be
// recognised by CONTENT (its flash.bin) and run its wasm.
const picovavePkg = path.join(serveDir, "picovave.fwsc");
if (fs.existsSync(picovavePkg)) {
  try {
    const b = fs.readFileSync(picovavePkg);
    const file = { name: "picovave.fwsc",
      arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) };
    listeners["drop"].drop({ preventDefault() {}, dataTransfer: { files: [file] } });
    await new Promise((r) => setTimeout(r, 250));            // loadFile() is async
    listeners["simRestore"].click();
    check("SIMULATOR restore of picovave.fwsc is matched to picovave (by content)",
          txt("device") === "PICOVAVE" && /실행 중/.test(txt("status")), txt("device"));
  } catch (e) {
    check("SIMULATOR restore works", false, String((e && e.message) || e));
  }
}

// The foreign-ROM branch, exercised with the user's own v016 if it is on this machine. We
// never ship that file; the page is fed it exactly as a dropped file, then the browser
// cannot execute it, so it must be shown as device-only.
const stock = process.env.SMK_V016 || path.join(os.homedir(), "Downloads", "SMK-37_Pro_016.fwsc");
if (fs.existsSync(stock)) {
  try {
    const b = fs.readFileSync(stock);
    const file = { name: path.basename(stock),
      arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) };
    listeners["drop"].drop({ preventDefault() {}, dataTransfer: { files: [file] } });
    await new Promise((r) => setTimeout(r, 250));            // loadFile() is async
    check("dropped v016 is parsed in the page", /SMK-37 Pro_016/.test(txt("pkg")), txt("pkg").split("\n")[0]);
    listeners["simRestore"].click();
    check("SIMULATOR restore installs the foreign ROM as device-only",
          txt("device") === "STOCK" && /실기 전용/.test(txt("status")), txt("device") + " | " + txt("status"));
    check("SIMULATOR restore writes head+app too", /0x00000\.\.0x9d000/.test(txt("flashlog")), "");
    listeners["simPicovave"].click();
    check("SIMULATOR returns to the picovave build after restore",
          txt("device") === "PICOVAVE" && /실행 중/.test(txt("status")), txt("device"));
  } catch (e) {
    check("SIMULATOR restore v016 works", false, String((e && e.message) || e));
  }
} else {
  console.log("skip RESTORE v016 test (no stock package; set SMK_V016=path to test)");
}

// FLASH(USB) is the real path: with no Web MIDI (headless Node), it must fail cleanly, say why,
// and leave the simulated device untouched - never throw.
try {
  listeners["simPicovave"].click();                            // a known good state first
  const usb = typeof navigator !== "undefined" && typeof navigator.requestMIDIAccess === "function";
  listeners["flashPicovave"].click();
  await new Promise((r) => setTimeout(r, 50));
  if (usb) {
    check("FLASH(USB) starts a real flash when Web MIDI exists", /FLASH\(USB\)/.test(txt("flashlog")), txt("flashlog").split("\n")[0]);
  } else {
    check("FLASH(USB) without Web MIDI fails cleanly and says why",
          /FLASH\(USB\)/.test(txt("flashlog")) && /Web MIDI/.test(txt("flashlog")) && uncaught.length === 0,
          (txt("flashlog").split("\n")[1] || "").trim());
    check("FLASH(USB) leaves the simulated device alone", txt("device") === "PICOVAVE", txt("device"));
    // Listing ports is how a failed auto-find becomes an explicit choice; headless has no Web MIDI,
    // so it must also fail cleanly and say so.
    listeners["midiscan"].click();
    await new Promise((r) => setTimeout(r, 30));
    check("MIDI port diagnostic runs, or fails cleanly without Web MIDI",
          /MIDI 포트/.test(txt("flashlog")) && uncaught.length === 0,
          (txt("flashlog").split("\n")[1] || "").trim());
  }
} catch (e) {
  check("FLASH(USB) path works", false, String((e && e.message) || e));
}

/* ---- the PC keyboard, and what the page prints on the panel ----
 * Both halves of one promise, and both come from src/picovave_p8.h's pv_gamepad(): a pad or a
 * keybed key carries a PICO-8 control written on it ONLY because the firmware reads that input for
 * that control, and PICO-8's own default PC keys (the arrows, Z, X, ENTER) drive exactly those
 * inputs - so the icon printed on a pad is also the key to press for it. The bit numbers here are
 * read off pv_gamepad(): pads 1/8/9/10 for the D-pad, 15/14 for O/X and 13 for Pause, the low keys
 * 5/7 8/10 9 11/12 for the same D-pad, and key 32 / key 33 / key 36 (hi bit 0 / bit 1 / bit 4) for
 * Pause / O / X.
 * O and X are the engine's own two action buttons: p8_input.h BUTTON_ACTION1 (femto8's p8_lua.c
 * names glyph 142 "O/Z") and BUTTON_ACTION2 (glyph 151, "X") - which is why Z goes with O.
 * Pause is PICO-8's sixth button: ENTER / P is PICO-8's Pause key (p8_input.c maps it to
 * BUTTON_MASK_PAUSE, which btn() hands out as btn(6)). PICO-8 has no separate restart control, so
 * the page prints none; sim_test.mjs still drives the engine's own abort input directly.
 * The page prints each control as an icon from its own SVG sprite, never as a text glyph - so
 * these checks name the sprite a pad or key must reference (href="#ic-<name>"). */
const iconOf = (el) => {
  const m = (el && el.innerHTML || "").match(/href="#ic-([a-z]+)"/);
  return m ? m[1] : null;
};
try {
  const P8 = [
    // pc key      the panel input it drives   the icon the page must print there
    ["ArrowUp",    "pad1",  "up"],
    ["ArrowLeft",  "pad8",  "left"],
    ["ArrowDown",  "pad9",  "down"],
    ["ArrowRight", "pad10", "right"],
    ["z",          "pad15", "o"],
    ["x",          "pad14", "x"],
    ["Enter",      "pad13", "pause"]
  ];
  let wrong = [];
  for (const one of P8) {
    const key = one[0], id = one[1], icon = one[2], el = elements[id];
    if (!el) { wrong.push(id + " was never built"); continue; }
    if (iconOf(el) !== icon || !/<i>/.test(el.innerHTML || "")) { wrong.push(id + " prints " + JSON.stringify(el.innerHTML)); continue; }
    fireKey("keydown", key);
    const down = el.classList.contains("down");
    fireKey("keyup", key);
    if (!down || el.classList.contains("down")) wrong.push(key + " does not hold " + id + " down and let it go");
  }
  check("PICO-8's default PC keys drive the pads that carry their icons", wrong.length === 0, wrong.join("; "));

  wrong = [];
  const onKeys = [["key5", "left"], ["key7", "left"], ["key8", "up"], ["key10", "up"], ["key9", "down"],
                  ["key11", "right"], ["key12", "right"], ["key32", "pause"],
                  ["key33", "o"], ["key36", "x"]];
  for (const one of onKeys) {
    const el = elements[one[0]];
    if (!el || iconOf(el) !== one[1]) wrong.push(one[0] + " prints " + JSON.stringify(el && el.innerHTML));
  }
  check("the keybed keys pv_gamepad() reads carry the same seven controls", wrong.length === 0, wrong.join("; "));

  // A cart cannot read the rest of the panel, so the rest of it carries nothing: no pad numbers,
  // no KB/P/H/C letters, no glyph on a key that does nothing.
  const labelled = new Set(onKeys.map((o) => o[0]).concat(P8.map((o) => o[1])));
  const bare = Object.keys(elements).filter(function (id) {
    return /^(pad|key)\d+$/.test(id) && elements[id].innerHTML && !labelled.has(id);
  });
  check("no other pad or key carries a label", bare.length === 0, bare.join(" "));

  // The keybed still plays, and its octave moved off z/x - those two are the cart's now.
  fireKey("keydown", "a");
  const octave12 = elements["key12"].classList.contains("down");
  fireKey("keyup", "a");
  fireKey("keydown", "z"); fireKey("keyup", "z");
  fireKey("keydown", "a");
  const zIsNotAnOctave = elements["key12"].classList.contains("down");
  fireKey("keyup", "a");
  fireKey("keydown", ",");
  fireKey("keydown", "a");
  const shifted = elements["key0"].classList.contains("down");
  fireKey("keyup", "a");
  fireKey("keydown", ".");
  fireKey("keydown", "a");
  const back = elements["key12"].classList.contains("down");
  fireKey("keyup", "a");
  check("A W S E plays the keybed and , / . shift its octave (z no longer does)",
        octave12 && zIsNotAnOctave && shifted && back,
        [octave12, zIsNotAnOctave, shifted, back].join("/"));

  // 1..8 stay the eight buttons under the faders
  fireKey("keydown", "1");
  const btn6 = elements["btn6"].classList.contains("down");
  fireKey("keyup", "1");
  check("1..8 hold down the buttons under the faders and let go",
        btn6 && !elements["btn6"].classList.contains("down"), "");
} catch (e) {
  check("the PC keyboard mapping works", false, String((e && e.message) || e));
}

console.log(fails ? "page: " + fails + " FAILED" : "page: all ok");
process.exit(fails ? 1 : 0);
