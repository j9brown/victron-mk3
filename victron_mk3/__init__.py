# Implementation of MK2/MK3 protocol based on the following documentation provided by Victron:
# https://www.victronenergy.com/upload/documents/Technical-Information-Interfacing-with-VE-Bus-products-MK2-Protocol-3-14.pdf
import asyncio
from enum import Enum, IntEnum, IntFlag
import logging
import serial
import serial_asyncio
import time
from typing import Callable, List

# Although the documentation says that the AC Info frame should report different
# values in the L1 packet to indicate the numnber of phases, it doesn't actually
# seem to work that way on my Multiplus II. Hardcode the number of phases for now.
HACK_OVERRIDE_AC_NUM_PHASES = 2

# The Multiplus II sometimes reports a negative inverter current even though
# the variable info indicates it is supposed to be unsigned. Override it.
HACK_OVERRIDE_AC_INVERTER_CURRENT_SIGNEDNESS = True

logger: logging.Logger = logging.getLogger("victron_mk3")


class SwitchState(IntEnum):
    CHARGER_ONLY = 1
    INVERTER_ONLY = 2
    ON = 3
    OFF = 4


class LEDState(IntFlag):
    OFF = 0
    MAINS = 0x01
    ABSORPTION = 0x02
    BULK = 0x04
    FLOAT = 0x08
    INVERTER = 0x10
    OVERLOAD = 0x20
    LOW_BATTERY = 0x40
    TEMPERATURE = 0x80


class DeviceState(IntEnum):
    DOWN = 0
    STARTUP = 1
    OFF = 2
    SLAVE = 3
    INVERT_FULL = 4
    INVERT_HALF = 5
    INVERT_AES = 6
    POWER_ASSIST = 7
    BYPASS = 8
    STATE_CHARGE = 9


class SwitchRegister(IntFlag):
    # DIRECT_REMOTE_SWITCH: the switch state controlled by this interface's use of the 'S' command
    DIRECT_REMOTE_SWITCH_CHARGE = 0x01
    DIRECT_REMOTE_SWITCH_INVERT = 0x02
    # FRONT_SWITCH: the front panel two-position switch: UP for on, DOWN for charger-only
    FRONT_SWITCH_UP = 0x04
    FRONT_SWITCH_DOWN = 0x08
    # SWITCH: the active switch state after resolving the interaction of the various controls
    SWITCH_CHARGE = 0x10
    SWITCH_INVERT = 0x20
    # ONBOARD_REMOTE_SWITCH: unknown, always seems to be on
    ONBOARD_REMOTE_SWITCH_INVERT = 0x40
    # REMOTE_GENERATOR_SELECTED: unknown, always seems to be off, maybe used with VEConfigure assistants?
    REMOTE_GENERATOR_SELECTED = 0x80


class Frame:
    def log(self, logger: logging.Logger, level: int) -> None:
        if logger.isEnabledFor(level):
            logger.log(level, self.__class__.__qualname__)
            for field, value in vars(self).items():
                if isinstance(value, Enum):
                    value = str(value) if value.name is None else value.name
                logger.log(level, f"  {field}: {value}")


class VersionFrame(Frame):
    def __init__(self, version: int) -> None:
        self.version = version


class LEDFrame(Frame):
    def __init__(self, on: LEDState, blink: LEDState) -> None:
        self.on = on
        self.blink = blink


class ConfigFrame(Frame):
    def __init__(
        self,
        last_active_ac_input: int,
        current_limit_overridden_by_panel: bool,
        digital_multi_control_dedicated: bool,
        num_ac_inputs: int,
        remote_panel_detected: bool,
        minimum_current_limit: float,
        maximum_current_limit: float,
        actual_current_limit: float,
        switch_register: SwitchRegister,
    ) -> None:
        self.last_active_ac_input = last_active_ac_input
        self.current_limit_overridden_by_panel = current_limit_overridden_by_panel
        self.digital_multi_control_dedicated = digital_multi_control_dedicated
        self.num_ac_inputs = num_ac_inputs
        self.remote_panel_detected = remote_panel_detected
        self.minimum_current_limit = minimum_current_limit
        self.maximum_current_limit = maximum_current_limit
        self.actual_current_limit = actual_current_limit
        self.switch_register = switch_register


class DCFrame(Frame):
    def __init__(
        self,
        dc_voltage: float,
        dc_current_to_inverter: float,
        dc_current_from_charger: float,
        ac_inverter_frequency: float,
    ) -> None:
        self.dc_voltage = dc_voltage
        self.dc_current_to_inverter = dc_current_to_inverter
        self.dc_current_from_charger = dc_current_from_charger
        self.ac_inverter_frequency = ac_inverter_frequency


class ACFrame(Frame):
    def __init__(
        self,
        ac_phase: int,
        ac_num_phases: int,  # only provided when phase is 1, otherwise it is 0
        device_state: DeviceState,
        ac_mains_voltage: float,
        ac_mains_current: float,
        ac_inverter_voltage: float,
        ac_inverter_current: float,
        ac_mains_frequency: float,
    ) -> None:
        self.ac_phase = ac_phase
        self.ac_num_phases = ac_num_phases
        self.device_state = device_state
        self.ac_mains_voltage = ac_mains_voltage
        self.ac_mains_current = ac_mains_current
        self.ac_inverter_voltage = ac_inverter_voltage
        self.ac_inverter_current = ac_inverter_current
        self.ac_mains_frequency = ac_mains_frequency


class StateFrame(Frame):
    def __init__(self) -> None:
        pass


class Fault(Enum):
    INACCESSIBLE = 1
    """The interface could not be opened at the provided path."""

    IO_ERROR = 2
    """An error occurred while communicating with the interface."""

    EXCEPTION = 3
    """An unhandled exception occurred."""


class Handler:
    def on_frame(self, frame: Frame) -> None:
        """Called when a frame is received from the interface."""
        pass

    def on_idle(self) -> None:
        """Called when the interface has not sent a frame for a while. When functioning
        normally, the interface sends a VersionFrame every second when there is no
        other traffic. So when the interface goes completely idle, it typically indicates
        that the device has been disconnected from it or has been powered off."""
        pass

    def on_fault(self, fault: Fault) -> None:
        """Called when an unrecoverable communication error occurs."""
        pass


class VictronMK3:
    def __init__(self, path: str) -> None:
        """Specifies the path of the serial port to which the Victron MK3 interface is connected."""
        self._path: str = path
        self._driver: _VictronMK3Driver = None
        self._driver_task: asyncio.Task = None

    async def start(self, handler: Handler) -> None:
        """Connects to the Victron MK3 interface and starts delivering events to the handler."""
        assert self._driver_task is None
        ready = asyncio.Event()
        self._driver = _VictronMK3Driver()
        self._driver_task = asyncio.create_task(
            self._driver.run(self._path, handler, ready)
        )
        await ready.wait()

    async def stop(self) -> None:
        """Disconnects from the Victron MK3 interface and stops delivering events to the previous handler.
        After this method completes the Victron MK3 instance can be reused for another connection."""
        assert self._driver is not None
        self._driver = None
        self._driver_task.cancel()
        try:
            await self._driver_task
        except asyncio.CancelledError:
            pass
        self._driver_task = None

    def send_version_request(self) -> None:
        """Sends a request for a VersionFrame.
        Does nothing if the interface is not running."""
        if self._driver is not None:
            self._driver.send_version_request()

    def send_led_request(self) -> None:
        """Sends a request for a LEDFrame.
        Does nothing if the interface is not running."""
        if self._driver is not None:
            self._driver.send_led_request()

    def send_dc_request(self) -> None:
        """Sends a request for a DCFrame.
        Does nothing if the interface is not running."""
        if self._driver is not None:
            self._driver.send_dc_request()

    def send_ac_request(self, phase: int) -> None:
        """Sends a request for an ACFrame.
        Does nothing if the interface is not running."""
        assert phase >= 1 and phase <= 4
        if self._driver is not None:
            self._driver.send_ac_request(phase)

    def send_config_request(self) -> None:
        """Sends a request for a ConfigFrame.
        Does nothing if the interface is not running."""
        if self._driver is not None:
            self._driver.send_config_request()

    def send_state_request(
        self, switch_state: SwitchState, current_limit: float | None = None
    ) -> None:
        """Sends a request to set the remote switch state and current limit in amps.
        If the requested current limit is None, the actual current limit is set to its maximum.
        If the requested current limit is 0 or negative, the actual current limit is set to its minimum.
        Otherwise the actual current limit is clamped to the range supported by the device.
        Does nothing if the interface is not running."""
        if self._driver is not None:
            self._driver.send_state_request(switch_state, current_limit)


class _VictronMK3Driver:
    IDLE_TIMEOUT = 2  # seconds
    VARIABLE_INFO_REQUEST_TIMEOUT = 2  # seconds

    class VariableInfo:
        def __init__(self, signed: bool, scale: float, offset: int) -> None:
            self._signed = signed
            self._scale = scale
            self._offset = offset

        def parse(self, raw: bytes) -> float:
            if len(raw) == 1:
                raw = raw[0]
                if self._signed and raw >= 0x80:
                    raw -= 0x100
            elif len(raw) == 2:
                raw = raw[0] | raw[1] << 8
                if self._signed and raw >= 0x8000:
                    raw -= 0x10000
            elif len(raw) == 3:
                raw = raw[0] | raw[1] << 8 | raw[2] << 16
                if self._signed and raw >= 0x800000:
                    raw -= 0x1000000
            else:
                assert False
            return self._scale * (raw + self._offset)

    def __init__(self) -> None:
        self._writer: asyncio.StreamWriter = None
        self._w_nonce: int = 0
        self._w_completion: Callable[[bytes], None] = None
        self._variable_id_queue = [0, 1, 2, 3, 4, 5, 7, 8]
        self._variable_info = {}
        self._variable_info_request_time = None

    async def run(self, path: str, handler: Handler, ready: asyncio.Event) -> None:
        fault = Fault.EXCEPTION
        try:
            # Open the port
            try:
                reader, self._writer = await serial_asyncio.open_serial_connection(
                    url=path,
                    baudrate=2400,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                )
            except serial.SerialException:
                fault = Fault.INACCESSIBLE
                raise
            finally:
                ready.set()

            # Reset the interface
            # The sleep may not actually needed but the reset seems more reliable this way
            self._send_frame("R", [])
            await asyncio.sleep(1)
            self.send_version_request()
            self._populate_next_variable_info()

            # Listen for frames until the task is cancelled
            while True:
                try:
                    async with asyncio.timeout(_VictronMK3Driver.IDLE_TIMEOUT):
                        size = await reader.readexactly(1)
                        msg = await reader.readexactly(size[0] + 1)
                except TimeoutError:
                    # May have lost stream synchronization, start over and hope to recover eventually
                    logger.debug("** Read timeout (interface is idle)")
                    handler.on_idle()
                    continue
                except serial.SerialException:
                    fault = Fault.IO_ERROR
                    raise

                if len(msg) == size[0] + 1 and (size[0] + sum(msg)) & 255 == 0:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"<< {size.hex()}{msg.hex()}")
                    self._handle_frame(handler, msg)
        except Exception:
            # Report faults
            logger.debug(f"Fault: {fault}", exc_info=True)
            handler.on_fault(fault)
        finally:
            # Close the port
            writer = self._writer
            self._writer = None
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except serial.SerialException:
                    pass

    def send_version_request(self) -> None:
        self._send_frame("V", [])

    def send_led_request(self) -> None:
        self._send_frame("L", [])

    def send_dc_request(self) -> None:
        self._send_frame("F", [0])

    def send_ac_request(self, phase: int) -> None:
        assert phase >= 1 and phase <= 4
        self._send_frame("F", [phase])

    def send_config_request(self) -> None:
        self._send_frame("F", [5])

    def send_state_request(
        self, switch_state: SwitchState, current_limit: float | None
    ) -> None:
        if current_limit is None:
            value = 0x8000
        elif current_limit <= 0:
            value = 0
        else:
            value = min(int(current_limit * 10), 0x7FFF)
        self._send_frame("S", [switch_state, value & 255, value >> 8, 0x01, 0x81])

    def _send_frame(self, command: int, data: List[int]) -> None:
        msg = bytearray(len(data) + 4)
        msg[0] = len(data) + 2
        msg[1] = 0xFF
        msg[2] = ord(command)
        msg[3:-1] = data
        msg[-1] = (256 - sum(msg[:-1])) & 255
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f">> {msg.hex()}")
        if self._writer is not None:
            try:
                self._writer.write(msg)
            except serial.SerialException:
                # Assume that a failure to write will also manifest as a failure
                # to read in the driver task and it will then be reported to the handler.
                pass

    def _handle_frame(self, handler: Handler, msg: bytes) -> None:
        if len(msg) >= 2 and msg[0] == 0xFF:  # Command Frame
            if msg[1] == ord("V") and len(msg) >= 6:
                handler.on_frame(
                    VersionFrame(
                        version=msg[2] | msg[3] << 8 | msg[4] << 16 | msg[5] << 24
                    )
                )
            elif msg[1] == ord("L") and len(msg) >= 4:
                handler.on_frame(LEDFrame(on=LEDState(msg[2]), blink=LEDState(msg[3])))
            elif msg[1] == ord("S"):
                handler.on_frame(StateFrame())
            elif msg[1] == ord("W"):
                self._handle_w_response(0, msg)
            elif msg[1] == ord("X"):
                self._handle_w_response(1, msg)
            elif msg[1] == ord("Y"):
                self._handle_w_response(2, msg)
            elif msg[1] == ord("Z"):
                self._handle_w_response(3, msg)
        elif len(msg) >= 15 and msg[0] == 0x20:  # Info Frame
            if len(self._variable_id_queue) == 0:  # Need variables populated for these
                if msg[5] == 0x0C:
                    handler.on_frame(
                        DCFrame(
                            dc_voltage=self._variable_info[4].parse(msg[6:8]),
                            dc_current_to_inverter=self._variable_info[5].parse(
                                msg[8:11]
                            ),
                            dc_current_from_charger=self._variable_info[5].parse(
                                msg[11:14]
                            ),
                            ac_inverter_frequency=_VictronMK3Driver._period_to_frequency(
                                self._variable_info[7].parse(msg[14:15])
                            ),
                        )
                    )
                elif msg[5] >= 0x05 and msg[5] <= 0x0B:
                    handler.on_frame(
                        ACFrame(
                            ac_phase=max(9 - msg[5], 1),
                            ac_num_phases=max(msg[5] - 7, 0)
                            if HACK_OVERRIDE_AC_NUM_PHASES == 0 or msg[5] < 8
                            else HACK_OVERRIDE_AC_NUM_PHASES,
                            device_state=DeviceState(msg[4]),
                            ac_mains_voltage=self._variable_info[0].parse(msg[6:8]),
                            ac_mains_current=self._variable_info[1].parse(msg[8:10])
                            * msg[1],
                            ac_inverter_voltage=self._variable_info[2].parse(
                                msg[10:12]
                            ),
                            ac_inverter_current=self._variable_info[3].parse(msg[12:14])
                            * msg[2],
                            ac_mains_frequency=_VictronMK3Driver._period_to_frequency(
                                self._variable_info[8].parse(msg[14:15])
                            ),
                        )
                    )
            else:
                self._populate_next_variable_info()
        elif len(msg) >= 13 and msg[0] == 0x41:  # Config Frame
            handler.on_frame(
                ConfigFrame(
                    last_active_ac_input=msg[5] & 0x03,
                    current_limit_overridden_by_panel=msg[5] & 0x04 != 0,
                    digital_multi_control_dedicated=msg[5] & 0x08 != 0,
                    num_ac_inputs=(msg[5] & 0x70) >> 4,
                    remote_panel_detected=msg[5] & 0x80 != 0,
                    minimum_current_limit=(msg[6] | msg[7] << 8) / 10,
                    maximum_current_limit=(msg[8] | msg[9] << 8) / 10,
                    actual_current_limit=(msg[10] | msg[11] << 8) / 10,
                    switch_register=SwitchRegister(msg[12]),
                )
            )

    def _populate_next_variable_info(self) -> None:
        if len(self._variable_id_queue) == 0:
            return
        now = time.monotonic()
        if (
            self._variable_info_request_time is not None
            and self._variable_info_request_time
            + _VictronMK3Driver.VARIABLE_INFO_REQUEST_TIMEOUT
            > now
        ):
            return

        self._variable_info_request_time = now
        id = self._variable_id_queue[0]

        # The address frame may be lost between power cycles of the equipment so
        # might as well resend it each time.
        self._send_frame("A", [1, 0])
        self._send_w_request(
            [0x36, id & 255, id >> 8], self._handle_variable_info_response
        )

    def _handle_variable_info_response(self, msg: bytes) -> None:
        self._variable_info_request_time = None
        if len(msg) >= 8 and msg[2] == 0x8E and msg[5] == 0x8F:
            scale = msg[3] | msg[4] << 8
            signed = False
            if scale >= 0x8000:
                scale = 0x10000 - scale
                signed = True
            if scale >= 0x4000:
                scale = 1 / (0x8000 - scale)
            offset = msg[6] | msg[7] << 8
            id = self._variable_id_queue.pop(0)
            if HACK_OVERRIDE_AC_INVERTER_CURRENT_SIGNEDNESS and id == 3:
                signed = True
            self._variable_info[id] = _VictronMK3Driver.VariableInfo(
                signed, scale, offset
            )
            self._populate_next_variable_info()

    def _send_w_request(self, msg: bytes, completion: Callable[[bytes], None]) -> None:
        self._w_nonce = (self._w_nonce + 1) % 4
        self._w_completion = completion
        self._send_frame(["W", "X", "Y", "Z"][self._w_nonce], msg)

    def _handle_w_response(self, nonce: int, msg: bytes) -> None:
        if self._w_nonce != nonce or self._w_completion is None:
            return None
        completion = self._w_completion
        self._w_completion = None
        completion(msg)

    def _period_to_frequency(period: float) -> float:
        return round(0 if period == 0 else 10 / period, 2)


class ProbeResult(Enum):
    OK = 0
    """The device was present and responsive."""

    INACCESSIBLE = 1
    """The interface could not be opened at the provided path."""

    IO_ERROR = 2
    """An error occurred while communicating with the interface."""

    UNRESPONSIVE = 3
    """The interface did not respond to requests."""

    EXCEPTION = 4
    """An unhandled exception occurred."""


class _ProbeHandler(Handler):
    FAULT_MAP = {
        Fault.INACCESSIBLE: ProbeResult.INACCESSIBLE,
        Fault.IO_ERROR: ProbeResult.IO_ERROR,
        Fault.EXCEPTION: ProbeResult.EXCEPTION,
    }

    def __init__(self) -> None:
        self.result = ProbeResult.INACCESSIBLE
        self.finished = asyncio.Event()

    def on_frame(self, frame: Frame) -> None:
        self.result = ProbeResult.OK
        self.finished.set()

    def on_idle(self) -> None:
        self.result = ProbeResult.UNRESPONSIVE
        self.finished.set()

    def on_fault(self, fault: Fault) -> None:
        self.result = _ProbeHandler.FAULT_MAP[fault]
        self.finished.set()


async def probe(path: str) -> ProbeResult:
    """Attempts to connect to a Victron MK3 interface then disconnects and reports what happened."""
    handler = _ProbeHandler()
    mk3 = VictronMK3(path)
    await mk3.start(handler)
    await handler.finished.wait()
    await mk3.stop()
    return handler.result
