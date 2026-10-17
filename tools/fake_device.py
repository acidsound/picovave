#!/usr/bin/env python3
"""A device that does not exist, so the recovery path can be run without one.

`platform/tools/fm1_install.py` (and the web page's FLASH(USB)) is the only way back into the
SMK-37 Pro from macOS: the mask-ROM UBOOT needs raw SCSI vendor commands and therefore
Linux/Windows. That leaves the installer as the tool that decides whether a stuck keyboard can be
recovered - and it is worth being able to prove the whole thing works on this machine before
pointing it at the hardware.

This script is that proof. It serves the device side of both M-UPGRADE steps from ota.c and
ldr_core.c (start: the package header, the staged loader, then the host's `success`; write: every
byte of flash.bin, checked against the package, then `success`), exactly the way
sim/fm1ota_check.mjs drives the browser copy of the installer. Two ways to use it:

  # 1. whole update in one process, no MIDI involved - what tools/verify_final.py runs
  python3 tools/fake_device.py --test build/picovave.fwsc

  # 2. a fake keyboard on virtual MIDI ports, for a real installer process over real CoreMIDI
  python3 tools/fake_device.py build/picovave.fwsc --secs 180      # terminal 1
  python3 platform/tools/fm1_install.py build/picovave.fwsc --yes --port "fake SMK-37 Pro"   # terminal 2

`--phase loader --loader-id "ota-SMK-37 Pro_016"` rehearses a keyboard that an interrupted install
left in another firmware's update mode: the fake must be refused without `--resume-loader` and
written with it. The fake only ever answers: it writes nothing anywhere.
"""

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "tools"))
import fm1_install as fm1                                            # noqa: E402

FINISH_CHECK, FINISH_WRITE = fm1.FINISH_CHECK, fm1.FINISH_WRITE
MAXDATA = fm1.MAXDATA
rd16 = lambda b, o: int.from_bytes(b[o:o + 2], "little")             # noqa: E731
rd32 = lambda b, o: int.from_bytes(b[o:o + 4], "little")             # noqa: E731


def device_msg(cmd, body):
    """a device -> host frame: the same shape ota.c sends"""
    msg = bytes([0x00, 0x59, cmd, len(body) & 0xFF, 0, 0]) + body
    msg += bytes([(~sum(body)) & 0xFF])
    return b"\xF0" + fm1.pack7(msg) + b"\xF7"


def ident_msg(text, tag=""):
    """the identity reply ota.c sends (0x11): 27 NUL-padded characters, then - after the
    terminator, exactly as the firmware does it - the build tag, when the name leaves room"""
    idb = bytearray(27)
    idb[:len(text)] = text.encode("latin-1")[:27]
    if tag and len(text) + 1 + len(tag) < 27:
        idb[len(text) + 1:len(text) + 1 + len(tag)] = tag.encode("latin-1")
    return device_msg(0x11, bytes(idb))


def request_msg(addr, n):
    """a read request (0x30) as ldr_core.c / ota.c send it"""
    return device_msg(0x30, bytes([0]) + addr.to_bytes(4, "little") + bytes([n & 0xFF, (n >> 8) & 0xFF, 0]))


def parse_answer(pkt):
    """host -> device: {addr, n, data} or None. A response carries data, so its checksum covers the
    whole body (ota_take), unlike a request's"""
    if not pkt or len(pkt) < 2 or pkt[0] != 0xF0 or pkt[-1] != 0xF7:
        return None
    u = fm1.unpack7(pkt[1:-1])
    if len(u) < 15 or u[:3] != b"\x00\x59\x30":
        return None
    n = u[11] | (u[12] << 8) | (u[13] << 16)
    if len(u) != n + 15 or (~sum(u[6:-1])) & 0xFF != u[-1]:
        return None
    return {"addr": int.from_bytes(u[7:11], "little"), "n": n, "data": u[14:14 + n]}


class FakeDevice:
    """the device, driven by the host: handshake, then the step the host starts"""

    def __init__(self, image, app_id="SMK-37 Pro_700", loader_id="ota-SMK-37 Pro_700",
                 phase="app", app_tag="",
                 app_name="picovave", loader_name="picovave Update"):
        self.image, self.app_id, self.loader_id = image, app_id, loader_id
        self.app_tag = app_tag
        self.app_name, self.loader_name = app_name, loader_name
        self.phase, self.error, self.served = phase, None, 0
        self.sink = None                    # set when the frames have to leave over MIDI
        self.to_host_q, self.from_host_q = queue.Queue(), queue.Queue()
        self.answer_q = queue.Queue()
        self.lock = threading.Lock()
        self.running = True

    @property
    def name(self):
        """the MIDI port name right now: the running app and the RAM loader present different USB
        descriptors ("picovave" and "picovave Update"; Felucca's own loader says "Felucca Update"),
        so the host has to look the device up again between the two steps"""
        return self.loader_name if self.phase == "loader" else self.app_name

    # ------------------------------------------------------------ wire to the host ---
    def to_host(self, pkt):
        """a frame for the host: straight onto the wire when there is one, else into the queue the
        in-process link reads"""
        if self.sink is not None:
            self.sink(pkt)
        else:
            self.to_host_q.put(pkt)

    def on_host(self, pkt):
        """one frame from the host"""
        if pkt == fm1.HS_QUERY:
            self.to_host(ident_msg(self.loader_id if self.phase == "loader" else self.app_id,
                                   "" if self.phase == "loader" else self.app_tag))
            return
        if pkt == fm1.UPGRADE:
            step = self.check_step if self.phase == "app" else self.write_step
            threading.Thread(target=self._run_step, args=(step,), daemon=True).start()
            return
        a = parse_answer(pkt)
        if a is not None:                       # the host's answer to a request we sent
            self.answer_q.put(a)
        # anything else (a stray read, the UBOOT key) is ignored: the device would ignore it too

    def _run_step(self, step):
        try:
            step()
        except Exception as e:                  # the host must see this rather than a silent stop
            self.error = e

    def host_loop(self, secs=None, backend=None):
        """pump frames from the host until stopped; the step runs on its own thread. `backend` is
        the virtual one, which has to re-enumerate when the phase changes"""
        end = None if secs is None else time.monotonic() + secs
        while self.running and (end is None or time.monotonic() < end):
            if backend is not None:
                backend.poll_phase()
            try:
                pkt = self.from_host_q.get(timeout=0.05)
            except queue.Empty:
                continue
            self.on_host(pkt)

    # ------------------------------------------------------------ the two steps ---
    def ask(self, addr, n, secs=30.0):
        self.to_host(request_msg(addr, n))
        end = time.monotonic() + secs
        while time.monotonic() < end:
            try:
                a = self.answer_q.get(timeout=max(0.0, end - time.monotonic()))
            except queue.Empty:
                break
            if a["addr"] == addr and a["n"] == n:
                self.served += 1
                return a["data"]
        raise RuntimeError("device: no answer for 0x%x+%d" % (addr, n))

    def ufw(self):
        """the package header: the file list, decrypted, as the packer wrote it"""
        hdr = self.ask(0, 512) + self.ask(512, 512)
        h = fm1._dec(hdr[0:0x40])
        fl = ota = None
        for i in range(rd16(h, 8)):
            e = fm1._dec(hdr[0x40 + i * 0x50:0x40 + (i + 1) * 0x50])
            typ, off, size = rd16(e, 0), rd32(e, 8), rd32(e, 12)
            if typ == 0:
                fl = {"off": off, "size": size, "dcrc": rd16(e, 4)}
            elif typ == 100:
                ota = {"off": off, "size": size}
        return fl, ota

    def expect_success(self, addr):
        if self.ask(addr, 8) != b"success\0":
            raise RuntimeError("device: no success at 0x%x" % addr)

    def check_step(self):
        """step 1 (ota.c): the header, the staged loader, then the host's success"""
        fl, ota = self.ufw()
        if not fl or not ota:
            raise RuntimeError("device: no flash.bin/ota.bin in the package")
        h = self.ask(ota["off"], 512)
        if fm1.LOADER_MARK not in h:
            raise RuntimeError("device: no Felucca loader in the staged entry")
        crc, ln = 0, rd32(h, 8)
        for i in range(0, ln, 512):
            n = min(512, ln - i)
            crc = fm1._crc16(self.ask(ota["off"] + 0x20 + i, n), crc)
        if crc != rd16(h, 2):
            raise RuntimeError("device: staged loader CRC")
        self.expect_success(FINISH_CHECK)
        self.phase = "loader"

    def write_step(self):
        """step 2 (ldr_core.c): every byte of flash.bin, then the host's success"""
        fl, _ = self.ufw()
        if not fl or fl["size"] < 0x9D000:
            raise RuntimeError("device: flash.bin too small")
        crc = 0

        def read_into(addr, n):
            nonlocal crc
            d = self.ask(addr, n)
            want = self.image[addr:addr + n]
            if d != want:
                raise RuntimeError("device: served 0x%x differs from the package" % addr)
            crc = fm1._crc16(d, crc)

        for s in range(0, 0x4000, 512):                                  # head
            read_into(fl["off"] + s, 512)
        for s in range(0x4000, 0x9D000, 0x1000):                         # app area, sector by sector
            for k in range(0, 0x1000, 512):
                read_into(fl["off"] + s + k, 512)
        for s in range(0x9D000, fl["size"], 512):                        # tail
            read_into(fl["off"] + s, min(512, fl["size"] - s))
        if crc != fl["dcrc"]:
            raise RuntimeError("device: flash.bin CRC (the host served the wrong image)")
        self.expect_success(FINISH_WRITE)
        self.phase = "app"                                               # rebooted: it is an app again


# ---------------------------------------------------------------- backends ---
class Backend:
    """the installer's backend interface, with the fake on the other end of a function call"""

    def __init__(self, dev, name=None):
        self.dev = dev
        self.name = name or dev.app_name

    def input_names(self):
        return [self.dev.name]

    def output_names(self):
        return [self.dev.name]

    def open(self, in_name, out_name):
        return Link(self.dev, self.dev.name)


class Link:
    def __init__(self, dev, name):
        self.dev, self.name, self.lost = dev, name, False

    def send(self, pkt):
        self.dev.on_host(bytes(pkt))
        return True

    def read(self, secs):
        try:                                  # what the device sent, i.e. the host's MIDI IN
            return self.dev.to_host_q.get(timeout=max(0.001, secs))
        except queue.Empty:
            return None

    def drain(self):
        while True:
            try:
                self.dev.to_host_q.get_nowait()
            except queue.Empty:
                return

    def close(self):
        pass


def _import_mido():
    """mido, re-executing this script into an interpreter that has it if need be"""
    import mido_boot
    m = mido_boot.import_mido(__file__, "fake_device")
    if m is None:
        raise SystemExit("fake_device: cannot host MIDI ports without mido")
    return m


class VirtualBackend:
    """the same device, on a pair of CoreMIDI virtual ports: a real installer process talks to it
    over a real MIDI stack, which is what a macOS user has. When the fake changes phase the old
    pair goes away and a new one appears under the loader's name, like the USB re-enumeration"""

    def __init__(self, dev, name=None):
        self.mido = _import_mido()
        self.dev, self.inp, self.out, self.current = dev, None, None, None
        dev.sink = self._send                       # its frames leave over CoreMIDI now
        self.resync(name or dev.name)

    def resync(self, name):
        for p in (self.inp, self.out):
            if p is not None:
                p.close()
        self.current = name
        self.inp = self.mido.open_input(name, virtual=True,
                                        callback=lambda m: self.dev.from_host_q.put(bytes([0xF0, *m.data, 0xF7]))
                                        if m.type == "sysex" else None)
        self.out = self.mido.open_output(name, virtual=True)
        print("fake_device: MIDI ports are now %r" % name, flush=True)

    def poll_phase(self):
        """called from the device loop: re-enumerate when the fake switched to the loader or back"""
        if self.dev.name != self.current:
            self.resync(self.dev.name)

    def _send(self, pkt):
        self.out.send(self.mido.Message("sysex", data=list(pkt[1:-1])))

    def input_names(self):
        return self.mido.get_input_names()

    def output_names(self):
        return self.mido.get_output_names()

    def open(self, in_name, out_name):
        return VirtualLink(self)


class VirtualLink:
    def __init__(self, backend):
        self.b, self.name, self.lost = backend, backend.current, False

    def send(self, pkt):
        self.b.out.send(self.b.mido.Message("sysex", data=list(pkt[1:-1])))
        return True

    def read(self, secs):
        try:
            return self.b.dev.to_host_q.get(timeout=max(0.001, secs))
        except queue.Empty:
            return None

    def drain(self):
        while True:
            try:
                self.b.dev.to_host_q.get_nowait()
            except queue.Empty:
                return

    def close(self):
        pass


# ---------------------------------------------------------------- drivers ---
def once(package, app_id, loader_id, phase, resume, out, app_name="picovave", app_tag="",
         loader_name="picovave Update"):
    """one install against a fresh fake: -> (rc, device, the port names the host saw)"""
    image = fm1.deinterleave(Path(package).read_bytes())[0]
    dev = FakeDevice(image, app_id, loader_id, phase, app_tag, app_name, loader_name)
    fm1.DELAY.update({"start": 0.05, "loader": 0.1, "reboot": 0.1, "retry": 0.05, "poll": 0.01,
                      "hs": 0.3, "wait_loader": 3.0, "wait_reboot": 3.0, "idle_check": 3.0,
                      "idle_write": 10.0, "open": 0.01})
    argv = [str(package), "--yes"] + (["--resume-loader", resume] if resume else [])
    backend = Backend(dev)
    seen, stop = [], threading.Event()

    def watch():                            # what the host's port list would show over time
        while not stop.is_set():
            n = backend.input_names()[0]
            if not seen or seen[-1] != n:
                seen.append(n)
            time.sleep(0.01)

    threading.Thread(target=watch, daemon=True).start()
    rc = fm1.main(argv, backend=backend, out=out, ask=lambda p: True)
    stop.set()
    time.sleep(0.05)
    return rc, dev, [n for i, n in enumerate(seen) if i == 0 or seen[i - 1] != n]


def look(rc, dev, names, want_names):
    """the checks every case shares"""
    problems = []
    if rc != 0:
        problems.append("fm1_install exited %d" % rc)
    if dev.error:
        problems.append("device: %s" % dev.error)
    if dev.phase != "app":
        problems.append("device never came back as an app (phase %s)" % dev.phase)
    missing = [n for n in want_names if n not in names]
    if missing:
        problems.append("the host never saw the device as %s (saw %s)" % (", ".join(missing), names))
    return problems


def run_test(package, app_id, loader_id, phase="app", out=sys.stdout, app_name="picovave",
             app_tag="",
             loader_name="picovave Update", resume=None):
    """the whole update in this process. `phase=loader` is the keyboard an interrupted install
    left in update mode: without `resume` the only thing to verify is that it is refused;
    with it, the refusal AND the finished write. Both re-enumerations are required when the
    device starts as an app: the host has to find it again under the loader's port name."""
    if phase == "loader":
        rc, dev, names = once(package, app_id, loader_id, phase, None, out, app_name, app_tag,
                              loader_name)
        if rc != fm1.EXIT["foreign"]:
            return rc, dev, ["a loader that is not Felucca's own (%s) was not refused: exit %d"
                             % (loader_id, rc)], names
        if resume is None:
            return 0, dev, [], names       # refused as it should be, nothing written
        rc, dev, names = once(package, app_id, loader_id, phase, resume, out, app_name, app_tag,
                              loader_name)
        return rc, dev, look(rc, dev, names, [app_name]), names
    rc, dev, names = once(package, app_id, loader_id, phase, None, out, app_name, app_tag,
                          loader_name)
    return rc, dev, look(rc, dev, names, [app_name, loader_name]), names


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", help="the .fwsc the fake device is offered")
    ap.add_argument("--test", action="store_true", help="run the whole update in this process and report")
    ap.add_argument("--secs", type=float, default=180.0, help="host the fake over MIDI for this long")
    ap.add_argument("--name", default=None, help="virtual port name (default: fake <app identity>)")
    ap.add_argument("--app-id", default="SMK-37 Pro_700", help="identity the running app answers with")
    ap.add_argument("--app-name", default="picovave", help="MIDI port name while the app runs")
    ap.add_argument("--loader-name", default="picovave Update",
                    help="MIDI port name in update mode (Felucca's own loader: 'Felucca Update')")
    ap.add_argument("--loader-id", default=None, help="identity the update loader answers with")
    ap.add_argument("--app-tag", default="", help="build tag the app rides after its identity")
    ap.add_argument("--phase", choices=("app", "loader"), default="app",
                    help="what the fake is when the host finds it (loader: an unfinished update)")
    ap.add_argument("--resume-loader", metavar="IDENTITY",
                    help="with --test: what to pass to the installer's --resume-loader")
    a = ap.parse_args()
    loader_id = a.loader_id or "ota-" + a.app_id

    if a.test:
        rc, dev, problems, names = run_test(a.package, a.app_id, loader_id, a.phase, sys.stdout,
                                           a.app_name, a.app_tag, a.loader_name, a.resume_loader)
        if a.phase == "loader" and a.resume_loader:
            print("fake_device: the foreign loader was refused first, then finished with "
                  "--resume-loader (nothing is written unless it is named)")
        for p in problems:
            print("fake_device: %s" % p)
        print("fake_device: %s (%d requests served; the host saw %s)"
              % ("the whole update ran over the wire" if not problems else "FAILED",
                 dev.served, " -> ".join(names) or "(nothing)"))
        return 0 if not problems else 1

    image = fm1.deinterleave(Path(a.package).read_bytes())[0]     # as the device is served it
    dev = FakeDevice(image, a.app_id, loader_id, a.phase, a.app_tag, a.app_name, a.loader_name)
    name = a.name or dev.name
    mido = _import_mido()
    if name in mido.get_input_names() + mido.get_output_names():
        print("fake_device: a port named %r is already there (another fake still running?): "
              "kill it, or pick --name" % name, file=sys.stderr)
        return 2
    backend = VirtualBackend(dev, name)                            # opens the ports before we say so
    print("fake_device: hosting %r on MIDI ports %r for %.0fs"
          % (a.app_id, name, a.secs), flush=True)
    print("fake_device: now run  python3 platform/tools/fm1_install.py %s --yes --port %r"
          % (a.package, name), flush=True)
    try:
        dev.host_loop(a.secs, backend)
    except KeyboardInterrupt:
        pass
    print("fake_device: %s (%d requests served, phase %s%s)"
          % ("done" if not dev.error else "FAILED", dev.served, dev.phase,
             "" if not dev.error else " - " + str(dev.error)))
    backend.inp.close()
    backend.out.close()
    return 0 if not dev.error and dev.phase == "app" else 1


if __name__ == "__main__":
    sys.exit(main())
