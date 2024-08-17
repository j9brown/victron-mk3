# Implementation of MK2/MK3 protocol based on the following documentation provided by Victron:
# https://www.victronenergy.com/upload/documents/Technical-Information-Interfacing-with-VE-Bus-products-MK2-Protocol-3-14.pdf
import asyncio
from enum import IntEnum, IntFlag
import serial
import serial_asyncio
import time
from typing import Callable, List

# Enable printing of protocol bytes
DEBUG_MESSAGES = False

# Although the documentation says that the AC Info frame should report different
# values in the L1 packet to indicate the numnber of phases, it doesn't actually
# seem to work that way on my Multiplus II. Hardcode the number of phases for now.
HACK_OVERRIDE_AC_NUM_PHASES = 2

# The Multiplus II sometimes reports a negative inverter current even though
# the variable info indicates it is supposed to be unsigned. Override it.
HACK_OVERRIDE_AC_INVERTER_CURRENT_SIGNEDNESS = True


class SwitchState(IntEnum):
    CHARGER_ONLY = 1
    INVERTER_ONLY = 2
    ON = 3
    OFF = 4


class LEDState(IntFlag):
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


class VariableInfo:
    def __init__(self, signed: bool, scale: float, offset: int):
        self._signed = signed
        self._scale = scale
        self._offset = offset

    def parse(self, raw: bytes):
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
        # print(f'{raw}, {self._signed}, {self._scale}, {self._offset}')
        return self._scale * (raw + self._offset)


class Frame:
    pass


class VersionFrame(Frame):
    def __init__(self, version):
        self.version = version


class LEDFrame(Frame):
    def __init__(self, on: LEDState, blink: LEDState):
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
    ):
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
    ):
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
    ):
        self.ac_phase = ac_phase
        self.ac_num_phases = ac_num_phases
        self.device_state = device_state
        self.ac_mains_voltage = ac_mains_voltage
        self.ac_mains_current = ac_mains_current
        self.ac_inverter_voltage = ac_inverter_voltage
        self.ac_inverter_current = ac_inverter_current
        self.ac_mains_frequency = ac_mains_frequency


class StateFrame(Frame):
    def __init__(self):
        pass


Handler = Callable[[int], None]


class VEBus:
    _VARIABLE_INFO_REQUEST_TIMEOUT = 2  # seconds
    _READ_TIMEOUT = 2

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handler: Handler,
    ):
        self._reader = reader
        self._writer = writer
        self._handler = handler
        self._w_nonce = 0
        self._w_completion = None
        self._variable_id_queue = [0, 1, 2, 3, 4, 5, 7, 8]
        self._variable_info = {}
        self._variable_info_request_time = None

    def close(self):
        self._writer.close()

    async def wait_closed(self):
        await self._writer.wait_closed()

    def send_version_request(self):
        self._send_frame("V", [])

    def send_led_request(self):
        self._send_frame("L", [])

    def send_dc_request(self):
        self._send_frame("F", [0])

    def send_ac_request(self, phase: int):
        assert phase >= 1 and phase <= 4
        self._send_frame("F", [phase])

    def send_config_request(self):
        self._send_frame("F", [5])

    # current limit is in amps
    # - if None, the limit is set to its maximum
    # - if 0, the value is set to its minimum
    # - otherwise it is set to the provided value and clamped to the range supported by the device
    def send_state_request(
        self, switch_state: SwitchState, current_limit: float|None = None
    ):
        if current_limit is None:
            value = 0x8000
        elif current_limit <= 0:
            value = 0
        else:
            value = min(int(current_limit * 10), 0x7fff)
        self._send_frame("S", [switch_state, value & 255, value >> 8, 0x01, 0x81])

    def _send_frame(self, command: int, data: List[int]):
        msg = bytearray(len(data) + 4)
        msg[0] = len(data) + 2
        msg[1] = 0xFF
        msg[2] = ord(command)
        msg[3:-1] = data
        msg[-1] = (256 - sum(msg[:-1])) & 255
        if DEBUG_MESSAGES:
            print(f">> {msg.hex()}")
        self._writer.write(msg)

    async def listen(self):
        await self._reset_interface()
        self._populate_next_variable_info()

        while True:
            try:
                async with asyncio.timeout(VEBus._READ_TIMEOUT):
                    size = await self._reader.read(1)
                    if len(size) != 1:
                        break
                    msg = await self._reader.readexactly(size[0] + 1)
            except TimeoutError:
                # May have lost stream synchronization, start over and hope to recover eventually
                if DEBUG_MESSAGES:
                    print('** Read timeout')
                continue

            if len(msg) == size[0] + 1 and (size[0] + sum(msg)) & 255 == 0:
                if DEBUG_MESSAGES:
                    print(f"<< {size.hex()}{msg.hex()}")
                self._handle_frame(msg)

    def _handle_frame(self, msg: bytes):
        if len(msg) >= 2 and msg[0] == 0xFF:  # Command Frame
            if msg[1] == ord("V") and len(msg) >= 6:
                self._handler(
                    VersionFrame(
                        version=msg[2] | msg[3] << 8 | msg[4] << 16 | msg[5] << 24
                    )
                )
            elif msg[1] == ord("L") and len(msg) >= 4:
                self._handler(LEDFrame(on=LEDState(msg[2]), blink=LEDState(msg[3])))
            elif msg[1] == ord("S"):
                self._handler(StateFrame())
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
                    self._handler(
                        DCFrame(
                            dc_voltage=self._variable_info[4].parse(msg[6:8]),
                            dc_current_to_inverter=self._variable_info[5].parse(
                                msg[8:11]
                            ),
                            dc_current_from_charger=self._variable_info[5].parse(
                                msg[11:14]
                            ),
                            ac_inverter_frequency=VEBus._period_to_frequency(
                                self._variable_info[7].parse(msg[14:15])
                            ),
                        )
                    )
                elif msg[5] >= 0x05 and msg[5] <= 0x0B:
                    self._handler(
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
                            ac_mains_frequency=VEBus._period_to_frequency(
                                self._variable_info[8].parse(msg[14:15])
                            ),
                        )
                    )
            else:
                self._populate_next_variable_info()
        elif len(msg) >= 13 and msg[0] == 0x41:  # Config Frame
            self._handler(
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

    async def _reset_interface(self):
        # The sleep may not actually needed but the reset seems more reliable this way
        self._send_frame("R", [])
        await asyncio.sleep(1)
        self.send_version_request()

    def _populate_next_variable_info(self):
        if len(self._variable_id_queue) == 0:
            return
        now = time.monotonic()
        if (
            self._variable_info_request_time is not None
            and self._variable_info_request_time + VEBus._VARIABLE_INFO_REQUEST_TIMEOUT
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

    def _handle_variable_info_response(self, msg: bytes):
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
            self._variable_info[id] = VariableInfo(signed, scale, offset)
            self._populate_next_variable_info()

    def _send_w_request(self, msg: bytes, completion: Callable[[bytes], None]):
        self._w_nonce = (self._w_nonce + 1) % 4
        self._w_completion = completion
        self._send_frame(["W", "X", "Y", "Z"][self._w_nonce], msg)

    def _handle_w_response(self, nonce: int, msg: bytes):
        if self._w_nonce != nonce or self._w_completion is None:
            return None
        completion = self._w_completion
        self._w_completion = None
        completion(msg)

    def _period_to_frequency(period: float):
        return round(0 if period == 0 else 10 / period, 2)


async def open_bus(device: str, handler: Handler):
    reader, writer = await serial_asyncio.open_serial_connection(
        url=device,
        baudrate=2400,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
    )
    return VEBus(reader, writer, handler)
