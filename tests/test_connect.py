import asyncio
import sys
import types
import unittest

# Stand-ins for the serial packages so the driver can run without a port.
serial = types.ModuleType("serial")
serial.PARITY_NONE = "N"
serial.STOPBITS_ONE = 1
serial.SerialException = type("SerialException", (Exception,), {})
serial_asyncio_fast = types.ModuleType("serial_asyncio_fast")
sys.modules.setdefault("serial", serial)
sys.modules.setdefault("serial_asyncio_fast", serial_asyncio_fast)

import victron_mk3  # noqa: E402


class _Writer:
    def __init__(self):
        self.frames = []

    def write(self, data):
        self.frames.append(bytes(data))

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _Reader:
    async def readexactly(self, n):
        raise EOFError


class _Handler(victron_mk3.Handler):
    def __init__(self):
        self.faults = []

    def on_fault(self, fault):
        self.faults.append(fault)


class ConnectTest(unittest.TestCase):
    def _connect_frames(self):
        writer = _Writer()

        async def open_serial_connection(**kwargs):
            return _Reader(), writer

        sys.modules["serial_asyncio_fast"].open_serial_connection = (
            open_serial_connection
        )
        driver = victron_mk3._VictronMK3Driver()
        asyncio.run(driver.run("/dev/null", _Handler(), asyncio.Event()))
        return writer.frames

    def test_connect_sends_version_then_short_frame_state(self):
        frames = self._connect_frames()
        self.assertEqual(frames[0], bytes.fromhex("02ff56a9"))
        self.assertEqual(frames[1], bytes.fromhex("09ff530000000190000113"))

    def test_frames_have_valid_checksums(self):
        for frame in self._connect_frames():
            self.assertEqual(frame[0], len(frame) - 2)
            self.assertEqual(sum(frame) & 0xFF, 0)


if __name__ == "__main__":
    unittest.main()
