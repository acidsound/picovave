import usb.core
import usb.util
import struct
from scsiio.common import SCSIDevBase, SCSIException

class SCSIDev(SCSIDevBase):
    """
    SCSI Generic I/O device implementation for macOS using PyUSB (Bulk-Only Transport).
    """

    def __init__(self, path=None):
        self.dev = None
        self.ep_out = None
        self.ep_in = None
        self.tag = 1
        super().__init__(path)

    def open(self, path=None):
        # Locate device 0x4C4A:0x8057
        dev = usb.core.find(idVendor=0x4C4A, idProduct=0x8057)
        if dev is None:
            raise SCSIException("No JieLi UBOOT device found (VID 0x4C4A, PID 0x8057)")

        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:
            pass

        try:
            dev.set_configuration()
        except Exception:
            pass

        try:
            usb.util.claim_interface(dev, 0)
        except Exception:
            pass

        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
        )

        if ep_out is None or ep_in is None:
            raise SCSIException("Failed to find Bulk endpoints on JieLi UBOOT device")

        self.dev = dev
        self.ep_out = ep_out
        self.ep_in = ep_in
        self.is_open = True

    def close(self):
        if self.is_open and self.dev is not None:
            try:
                usb.util.release_interface(self.dev, 0)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
            self.dev = None
            self.is_open = False

    def execute(self, cdb, data_out=None, data_in=None, max_sense_len=32, return_sense_buffer=False):
        if not self.is_open:
            raise SCSIException("Device is not open")

        self.tag = (self.tag + 1) & 0xFFFFFFFF
        tag = self.tag

        if data_out and data_in:
            raise NotImplementedError("Bidirectional SCSI transfers are not supported")

        dxfer_len = 0
        direction = 0x00
        if data_out:
            dxfer_len = len(data_out)
            direction = 0x00
        elif data_in:
            dxfer_len = len(data_in)
            direction = 0x80

        # CBW: 31 bytes
        # struct { uint32 sig; uint32 tag; uint32 data_len; uint8 flags; uint8 lun; uint8 cdb_len; uint8 cdb[16]; }
        cdb_padded = cdb.ljust(16, b"\x00") if len(cdb) < 16 else cdb[:16]
        cbw = struct.pack("<4sIIBBB16s", b"USBC", tag, dxfer_len, direction, 0, len(cdb), cdb_padded)

        try:
            self.ep_out.write(cbw, timeout=5000)
        except Exception as e:
            raise SCSIException(f"Failed to send CBW: {e}")

        if data_out:
            try:
                self.ep_out.write(data_out, timeout=5000)
            except Exception as e:
                raise SCSIException(f"Failed to write data: {e}")
        elif data_in:
            try:
                raw = self.ep_in.read(dxfer_len, timeout=5000)
                data_in[:len(raw)] = raw
            except Exception as e:
                raise SCSIException(f"Failed to read data: {e}")

        # CSW: 13 bytes
        try:
            csw = self.ep_in.read(13, timeout=5000)
        except Exception as e:
            raise SCSIException(f"Failed to read CSW: {e}")

        if len(csw) != 13:
            raise SCSIException(f"CSW length mismatch: got {len(csw)} bytes")

        sig, csw_tag, residue, status = struct.unpack("<4sIIB", bytes(csw))
        if sig != b"USBS" or csw_tag != tag:
            raise SCSIException(f"Invalid CSW: sig={sig!r}, tag={csw_tag:#x} (expected {tag:#x})")

        if status != 0:
            raise SCSIException(f"SCSI command failed with status {status}")

        if return_sense_buffer:
            return residue, b""
        return residue
