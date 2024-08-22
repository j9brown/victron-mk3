import asyncio
import click
import logging
from victron_mk3 import (
    Fault,
    Frame,
    Handler,
    InterfaceFlags,
    SwitchState,
    StateFrame,
    VictronMK3,
    AC_PHASES_SUPPORTED,
    logger,
    probe,
)

DELAY_BETWEEN_REQUESTS = 2  # seconds


logging.basicConfig(format="%(message)s")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Increase logging output")
def cli(verbose):
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


@cli.command(
    help="Monitor the status of the device attached to a Victron MK3 interface"
)
@click.argument("path", type=str)
def monitor(path: str) -> None:
    async def main() -> None:
        handler = MonitorHandler()
        mk3 = VictronMK3(path)
        await mk3.start(handler)

        while not handler.faulted:
            mk3.send_interface_request()
            mk3.send_led_request()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            mk3.send_dc_request()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            for phase in range(1, AC_PHASES_SUPPORTED + 1):
                mk3.send_ac_request(phase)
                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            mk3.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

        await mk3.stop()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


@cli.command(
    help="Set the switch state and current limit of the device attached to a Victron MK3 interface"
)
@click.argument("path", type=str)
@click.argument(
    "switch_state", type=click.Choice(["on", "off", "charger_only", "inverter_only"])
)
@click.option("--current-limit", type=float, help="Current limit in amps")
@click.option(
    "--monitor/--no-monitor", help="Keep monitoring the status after acknowledgment"
)
@click.option(
    "--standby/--no-standby",
    help="Prevent the device from going to sleep when turned off",
)
def control(
    path: str, switch_state: str, current_limit: float, monitor: bool, standby: bool
):
    switch_state = SwitchState[switch_state.upper()]
    logger.info(
        f"Setting switch state to {switch_state.name} and current limit to {current_limit} amps"
    )
    flags = InterfaceFlags.PANEL_DETECT
    if standby:
        flags |= InterfaceFlags.STANDBY

    async def main() -> None:
        handler = MonitorHandler()
        mk3 = VictronMK3(path)
        await mk3.start(handler)

        while not handler.state_frame_seen and not handler.faulted:
            mk3.send_interface_request(flags)
            mk3.send_state_request(switch_state, current_limit)
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            mk3.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

        while monitor and not handler.faulted:
            mk3.send_interface_request(flags)
            mk3.send_led_request()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            mk3.send_dc_request()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            for phase in range(1, AC_PHASES_SUPPORTED + 1):
                mk3.send_ac_request(phase)
                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
            mk3.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

        await mk3.stop()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


@cli.command(
    name="probe",
    help="Probes an Victron MK3 interface to determine whether it is operational",
)
@click.argument("path", type=str)
def probe_command(path: str):
    async def main() -> None:
        result = await probe(path)
        logger.info(f"Result: {result.name}")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())


class MonitorHandler(Handler):
    def __init__(self):
        self.state_frame_seen = False
        self.faulted = False

    def on_frame(self, frame: Frame) -> None:
        frame.log(logger, logging.INFO)
        if isinstance(frame, StateFrame):
            self.state_frame_seen = True

    def on_idle(self) -> None:
        logger.info("Idle")

    def on_fault(self, fault: Fault) -> None:
        logger.error(f"Fault: {fault.name}")
        self.faulted = True


if __name__ == "__main__":
    cli()
