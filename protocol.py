"""CH75K vendor feature reports recovered from the Windows configurator."""

from __future__ import annotations

from contextlib import suppress
from time import sleep

REPORT_ID = 7
BLOCK_SIZE = 8
PROFILE_POINTER = 0x00D0


class ProtocolError(RuntimeError):
    """The device response did not match the expected CH75K protocol."""


class SettingsLink:
    def __init__(self, hid, report_size: int, wait=sleep):
        if not 8 <= report_size <= 64:
            raise ProtocolError("Feature report 7 is missing or has an unsupported length.")
        self.hid = hid
        self.report_size = report_size
        # A transport can ask for longer pauses and a settle time after each save.
        scale = getattr(hid, "wait_scale", 1)
        self.wait = wait if scale == 1 else (lambda seconds: wait(seconds * scale))
        self._settle = getattr(hid, "save_settle", 0)
        self._sleep = wait

    def _send(self, opcode, *, address=0, index=0, value=0, last=0, group=0x18):
        packet = bytes((REPORT_ID, group, opcode, index, address & 0xFF,
                        address >> 8, value, last))
        self.hid.set_feature(packet.ljust(self.report_size, b"\0"))

    def _receive(self, minimum: int) -> bytes:
        response = self.hid.get_feature(REPORT_ID, self.report_size)
        if len(response) < minimum or response[0] != REPORT_ID:
            raise ProtocolError("The keyboard returned a short or unexpected feature report.")
        return response

    @staticmethod
    def _check_range(address: int, count: int):
        if not 1 <= count <= BLOCK_SIZE or not 0 <= address <= 0x10000 - count:
            raise ProtocolError("Invalid CH75K settings block address or length.")

    def finish_configuration(self):
        """Return to the normal mode used by the vendor after a settings save."""
        self._send(0x11)
        self.wait(0.010)
        self._send(0x00)
        if self._settle:
            self._sleep(self._settle)

    def begin_configuration(self, expected_base: int):
        """Enter the vendor's save mode and check the live base when supplied."""
        self._send(0x41, group=0x09)
        self.wait(0.200)
        if self.report_size >= 16:
            response = self._receive(16)
        else:
            response = self.hid.get_feature_extended(REPORT_ID, 16)
        if len(response) >= 16 and int.from_bytes(response[14:16], "little") != expected_base:
            raise ProtocolError("The active profile address changed. Reconnect and read it again.")

    def release(self):
        """Run the vendor's final report before closing the USB interface."""
        try:
            self.finish_configuration()
        finally:
            self.close_configuration()

    def close_configuration(self):
        self._send(0x00, group=0x20)

    def read_block(self, address: int, count: int = BLOCK_SIZE) -> bytes:
        self._check_range(address, count)
        data = bytearray()
        try:
            for index in range(count):
                self._send(0x03, address=address, index=index, last=count - 1)
            self.wait(0.001)
            self._send(0x00, address=address)
            self.wait(0.001)
            for index in range(count):
                self._send(0x05, address=address, index=index, last=count - 1)
                self.wait(0.001)
                data.append(self._receive(2)[1])
            self.wait(0.001)
        except Exception:
            with suppress(Exception):
                self._send(0x01)
            raise
        self._send(0x01)
        return bytes(data)

    def write_block(self, address: int, data: bytes):
        count = len(data)
        self._check_range(address, count)
        try:
            for index, value in enumerate(data):
                self._send(0x03, address=address, index=index, value=value, last=count - 1)
            self.wait(0.002)
            self._send(0x09, address=address, last=count - 1)
            self.wait(0.010)
        except Exception:
            with suppress(Exception):
                self._send(0x01, address=address)
            raise
        self._send(0x01, address=address)
