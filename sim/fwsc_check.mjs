// SPDX-License-Identifier: GPL-3.0-only
// Cross-check web/sim/fwsc.mjs (the browser's package reader) against tools/fwsc.py, the
// independent structural codec every other tool trusts.
//
//   node picovave/sim/fwsc_check.mjs <fwsc.mjs> <package.fwsc> [more.fwsc ...]
//
// Prints one canonical JSON summary (no file payloads) per package. tools/verify_final.py
// computes the same summary with fwsc.py and requires them to be equal, so the two readers
// cannot drift apart silently.
import fs from "fs";
import { pathToFileURL } from "url";

const [, , modPath, ...pkgs] = process.argv;
if (!modPath || pkgs.length === 0) {
  console.error("usage: node fwsc_check.mjs <fwsc.mjs> <package.fwsc> [...]");
  process.exit(2);
}

const { parse } = await import(pathToFileURL(modPath).href);

function summary(d) {
  return {
    product: d.product,
    blocks: d.blocks,
    logicalSize: d.logicalSize,
    nfiles: d.header.nfiles,
    crcHdrOk: d.header.crcHdrOk,
    crcListOk: d.header.crcListOk,
    totalOk: d.header.totalOk,
    chip: d.header.chip,
    consts: d.header.consts,
    files: d.files.map((f) => ({
      name: f.name, type: f.type, index: f.index, offset: f.offset,
      length: f.length, padded: f.padded, crc: f.crc, crcOk: f.crcOk, got: f.got,
    })),
  };
}

const out = {};
for (const p of pkgs) {
  const d = parse(new Uint8Array(fs.readFileSync(p)));
  out[p.split("/").pop()] = summary(d);
}
console.log(JSON.stringify(out));
