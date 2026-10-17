#!/usr/bin/env python3
"""Talk to a picovave / Felucca device over USB-MIDI by hand.

The web installer and platform/tools/fm1_install.py are both complete M-UPGRADE hosts; this is
the opposite: a few raw bytes so the device can be asked questions when a session does not start
and the installer only says "stopped answering after 0 requests". What it sends:

  --ask      the identity query (the same as the installer's handshake)
  --upgrade  F0 22 24 35 7F F7, the M-UPGRADE upgrade command
  --uboot    F0 22 24 35 7D F7, the soft key that resets into the mask-ROM USB updater
  --watch    nothing; just print what the device sends
  --flash    hand the whole job to platform/tools/fm1_install.py: serve the package, write it,
             wait for the restart and check the identity. That is the actual recovery.

Every received MIDI message is printed with a timestamp, so a device that answers the handshake
and then goes quiet is visibly different from one that never answers at all.

Both directions are opened: the host talks on its MIDI OUT (mido's output port) and the device's
answers arrive on the host's MIDI IN (mido's input port), which is a separate object - listening
on the output port reads nothing. When --list shows no port at all, the device is not in a mode
that speaks MIDI (the mask-ROM UBOOT is a USB disk, not MIDI; writing that one needs raw SCSI and
therefore Linux/Windows - see docs/RECOVERY.md for the macOS decision tree).

Needs mido + python-rtmidi, which the system python3 usually does not have. Rather than making
that the user's problem, this script re-executes itself into the first interpreter it can find
that has them (a .venv / .venv-ota next to the script, the repository or $HOME, or $PICOVAVE_PY;
the search lives in tools/mido_boot.py and platform/tools/fm1_install.py uses it too), so
`python3 tools/ota_probe.py --ask` works as typed. To fix the environment instead:

  python3 -m venv .venv-ota && .venv-ota/bin/pip install mido python-rtmidi
  PICOVAVE_PY=.venv-ota/bin/python python3 tools/ota_probe.py --list

Examples:

  python3 tools/ota_probe.py --list
  python3 tools/ota_probe.py --ask --secs 3
  python3 tools/ota_probe.py --upgrade --secs 10
  python3 tools/ota_probe.py --flash build/picovave.fwsc      # the recovery itself
  python3 tools/ota_probe.py --uboot           # then use jl-uboot-tool on Linux/Windows
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))     # tools/mido_boot.py, next to this file
import mido_boot                                             # noqa: E402

HS_QUERY = bytes([0xF0, 0x00, 0x32, 0x45, 0x00, 0x00, 0x00, 0x40, 0x7F, 0xF7])
UPGRADE = bytes([0xF0, 0x22, 0x24, 0x35, 0x7F, 0xF7])
UBOOT = bytes([0xF0, 0x22, 0x24, 0x35, 0x7D, 0xF7])
SHORT = {0xF0: "SOX", 0xF7: "EOX", 0xF8: "CLOCK", 0xFA: "START"}
MATCH = ("felucca", "picovave", "hello", "smk", "ac79", "m-vave", "mvave", "jie li", "jieli")
INSTALLER = Path(__file__).resolve().parents[1] / "platform" / "tools" / "fm1_install.py"


mido = mido_boot.import_mido()
if mido is None:
    sys.exit(1)


# ------------------------------------------------------------------------ ports ---
def matches(name):
    low = name.lower()
    return any(s in low for s in MATCH)


def devices():
    """[(name, can_send, can_receive)] - one entry per name, both directions collapsed."""
    ins, outs = mido.get_input_names(), mido.get_output_names()
    out = []
    for n in outs + [x for x in ins if x not in outs]:
        out.append((n, n in outs, n in ins))
    return out


def pick(name=None, want_input=False):
    """Resolve a port name: an explicit --port substring, else the first device-looking one.
    Replies come back on the input side, whose name matches the output's on every backend here."""
    names = mido.get_input_names() if want_input else mido.get_output_names()
    if name:
        for n in names:
            if name.lower() in n.lower():
                return n
        return None
    for n in names:
        if matches(n):
            return n
    return None


def unpack7(s):
    """the 7-bit packing the M-UPGRADE frames use (byte for byte what fm1_install.py unpacks)"""
    out, acc, nb = [], 0, 0
    for b in s:
        acc |= (b & 0x7F) << nb
        nb += 7
        while nb >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nb -= 8
    return bytes(out)


def describe(msg):
    if msg.type != "sysex":
        return str(msg)
    hexs = "sysex " + " ".join("%02X" % b for b in msg.data)
    d = unpack7(bytes(msg.data))
    if len(d) >= 33 and d[:3] == b"\x00\x59\x11":          # cmd 0x11: the identity reply
        field = d[6:33]                                     # 6-byte header, 27 NUL-padded bytes
        txt = field.split(b"\0")[0].decode("latin-1")
        tag = field.split(b"\0", 1)[1].lstrip(b"\0").split(b"\0")[0].decode("latin-1") \
            if b"\0" in field else ""
        return "%s   <- identity: %s%s" % (hexs, txt, "  build " + tag if tag else
                                           "  (no build tag: a pre-2026-10-17 build)")
    return hexs


def watch(port, secs):
    """Print everything the device sends, with the time since the first byte."""
    if port is None:
        print("no input port to listen on: the device's answers arrive on the host's MIDI IN")
        time.sleep(secs)
        return 0
    n = 0
    t0 = time.time()
    while True:
        dt = time.time() - t0
        if dt >= secs:
            break
        for msg in port.iter_pending():
            n += 1
            print("  +%5.2fs  %s" % (dt, describe(msg)))
        time.sleep(0.002)
    return n


def flash(pkg, port=None, yes=False):
    """Recover the device: run the real M-UPGRADE host (platform/tools/fm1_install.py) on the
    package, in this interpreter. It serves the package to the running firmware or to the update
    loader that an interrupted install left behind, waits for the restart, then checks the identity.
    """
    if not INSTALLER.exists():
        print("no %s: this tree cannot install a package" % INSTALLER, file=sys.stderr)
        return 1
    cmd = [sys.executable, str(INSTALLER), pkg]
    if port:
        cmd += ["--port", port]
    if yes:
        cmd += ["--yes"]
    print("flash: %s" % " ".join(cmd), flush=True)
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        print("\ninterrupted: the update loader stays; run --flash again to finish it", file=sys.stderr)
        return 130


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list MIDI ports and exit")
    ap.add_argument("--port", help="port name (or a substring of it)")
    ap.add_argument("--ask", action="store_true", help="send the identity query")
    ap.add_argument("--upgrade", action="store_true", help="send the M-UPGRADE upgrade command")
    ap.add_argument("--uboot", action="store_true",
                    help="send the soft key that enters the mask-ROM UBOOT (on macOS there is no way "
                         "back from it: see docs/RECOVERY.md, use --flash instead)")
    ap.add_argument("--watch", action="store_true", help="send nothing, only listen")
    ap.add_argument("--flash", metavar="PACKAGE.fwsc",
                    help="install PACKAGE.fwsc and check the identity (runs fm1_install.py)")
    ap.add_argument("--yes", action="store_true", help="with --flash: do not ask for confirmation")
    ap.add_argument("--secs", type=float, default=5.0, help="how long to listen (default 5)")
    args = ap.parse_args()

    if args.flash:
        return flash(args.flash, args.port, args.yes)

    if args.list:
        found = devices()
        for n, can_send, can_recv in found:
            print("%s %-40s %s%s" % ("*" if matches(n) else " ", n,
                                     "send" if can_send else "    ",
                                     " recv" if can_recv else ""))
        if not found:
            print("(no MIDI ports at all)")
        elif not any(matches(n) for n, _, _ in found):
            print("(nothing that looks like a Felucca/picovave device; the mask-ROM UBOOT is a "
                  "USB disk, not MIDI - jl-uboot-tool, Linux/Windows)")
        return 0

    out_name = pick(args.port)
    if out_name is None:
        print("no MIDI output port matches %r; --list shows what is there" % (args.port or "a Felucca/picovave device"),
              file=sys.stderr)
        return 1
    in_name = pick(args.port or out_name, want_input=True)

    sends = []
    if args.ask:
        sends.append(("identity query", HS_QUERY))
    if args.upgrade:
        sends.append(("M-UPGRADE upgrade", UPGRADE))
    if args.uboot:
        sends.append(("UBOOT soft key", UBOOT))
        if sys.platform == "darwin":
            print("note: the mask-ROM UBOOT is a USB disk, and only Linux/Windows hosts can write "
                  "it (raw SCSI vendor commands). On macOS --uboot is a one-way door unless the "
                  "app area is already good - recover over MIDI with --flash instead.",
                  file=sys.stderr)
    if not sends and not args.watch:
        print("nothing to do: pick --ask / --upgrade / --uboot / --watch / --flash", file=sys.stderr)
        return 1

    print("out: %s" % out_name)
    print("in : %s" % (in_name if in_name else "(none - replies cannot be read)"))
    got = 0
    with mido.open_output(out_name) as port:
        with (mido.open_input(in_name) if in_name else _nullport()) as inp:
            for label, data in sends:
                print("send %-20s %s" % (label, " ".join("%02X" % b for b in data)))
                port.send(mido.Message("sysex", data=list(data[1:-1])))
                time.sleep(0.05)
            got = watch(inp, args.secs)
    print("%d messages in %.1fs" % (got, args.secs))
    if got == 0 and sends:
        print("nothing came back: not in a mode that answers on this port, or not that device")
    return 0


class _nullport:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    sys.exit(main())
