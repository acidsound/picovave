#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Upload a PICO-8 cart to SMK-37 Pro over USB-MIDI SysEx."""
import argparse
import queue
import struct
import sys
import time
from pathlib import Path

try:
    import mido
except ImportError:
    print("Run with .venv-ota/bin/python3", file=sys.stderr)
    sys.exit(1)

SYSEX_HDR = bytes([0x22, 0x24, 0x35, 0x7E])  # mido sysex data excludes F0 and F7


def le7(val, n):
    res = []
    for _ in range(n):
        res.append(val & 0x7F)
        val >>= 7
    return bytes(res)


def pack7(data):
    out = []
    acc = 0
    nb = 0
    for b in data:
        acc |= b << nb
        nb += 8
        while nb >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            nb -= 7
    if nb > 0:
        out.append(acc & 0x7F)
    return bytes(out)


def crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            if c & 0x8000:
                c = ((c << 1) ^ 0x1021) & 0xFFFF
            else:
                c = (c << 1) & 0xFFFF
    return c


def open_ports():
    inp_names = mido.get_input_names()
    outp_names = mido.get_output_names()
    felucca_in = [n for n in inp_names if "felucca" in n.lower() or "smk" in n.lower()]
    felucca_out = [n for n in outp_names if "felucca" in n.lower() or "smk" in n.lower()]
    if not felucca_in or not felucca_out:
        print(f"Device not found. Inputs: {inp_names}, Outputs: {outp_names}", file=sys.stderr)
        sys.exit(1)
    q = queue.Queue()
    inp = mido.open_input(felucca_in[0], callback=lambda m: q.put(m))
    outp = mido.open_output(felucca_out[0])
    return inp, outp, q


def wait_reply(q, timeout=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            m = q.get(timeout=0.05)
            if m.type == "sysex" and bytes(m.data[:4]) == SYSEX_HDR:
                return bytes(m.data)
        except queue.Empty:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", help="Payload file (PVC1 or PVB1) or cooked bin")
    ap.add_argument("--name", default="cart.p8.png")
    args = ap.parse_args()

    raw = Path(args.payload).read_bytes()
    if raw.startswith(b"PVC1") or raw.startswith(b"PVB1"):
        payload = raw
    elif len(raw) == 32800:
        payload = b"PVC1" + raw
    else:
        print("Unknown payload format (must start with PVC1 or PVB1 or be 32,800 B cooked bin)", file=sys.stderr)
        sys.exit(1)

    inp, outp, q = open_ports()
    print(f"Connected to Felucca! Uploading {args.name} ({len(payload)} B)...")

    # Send BEGIN (0x01)
    name_bytes = args.name.encode("latin-1")[:31]
    begin_args = bytes([0]) + le7(len(payload), 3) + bytes([len(name_bytes)]) + name_bytes
    outp.send(mido.Message("sysex", data=SYSEX_HDR + bytes([0x01]) + begin_args))
    rep = wait_reply(q, 1.5)
    if not rep or rep[4] != 0x10:
        print(f"BEGIN failed: {rep.hex() if rep else 'timeout'}", file=sys.stderr)
        sys.exit(1)
    print("  BEGIN OK")

    # Send DATA (0x02) in 128-byte chunks
    chunk_sz = 128
    for off in range(0, len(payload), chunk_sz):
        chunk = payload[off:off + chunk_sz]
        data_args = le7(off, 3) + le7(len(chunk), 3) + pack7(chunk)
        outp.send(mido.Message("sysex", data=SYSEX_HDR + bytes([0x02]) + data_args))
        rep = wait_reply(q, 0.5)
        if not rep or rep[4] != 0x10:
            print(f"DATA at {off} failed: {rep.hex() if rep else 'timeout'}", file=sys.stderr)
            sys.exit(1)
        if (off // chunk_sz) % 20 == 0:
            print(f"  {off * 100 // len(payload)}%...")

    # Send END (0x03)
    crc = crc16(payload)
    end_args = le7(crc, 3)
    outp.send(mido.Message("sysex", data=SYSEX_HDR + bytes([0x03]) + end_args))
    rep = wait_reply(q, 2.0)
    if not rep or rep[4] != 0x10:
        print(f"END failed: {rep.hex() if rep else 'timeout'}", file=sys.stderr)
        sys.exit(1)
    print(f"Cart {args.name} committed and running! Reply: {rep.hex()}")


if __name__ == "__main__":
    main()
