import asyncio
import click
import logging
from victron_mk3 import (
    Fault,
    Response,
    Handler,
    InterfaceFlags,
    SwitchState,
    VictronMK3,
    AC_PHASES_SUPPORTED,
    logger,
    probe,
)

POLL_INTERVAL_SECONDS = 2


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
            await mk3.send_interface_request()
            await mk3.send_led_request()
            await mk3.send_dc_request()
            for phase in range(1, AC_PHASES_SUPPORTED + 1):
                await mk3.send_ac_request(phase)
            await mk3.send_config_request()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

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

        state_response = None
        while state_response is None and not handler.faulted:
            await mk3.send_interface_request(flags)
            state_response = await mk3.send_state_request(switch_state, current_limit)
            await mk3.send_config_request()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        while monitor and not handler.faulted:
            await mk3.send_interface_request(flags)
            await mk3.send_led_request()
            await mk3.send_dc_request()
            for phase in range(1, AC_PHASES_SUPPORTED + 1):
                await mk3.send_ac_request(phase)
            await mk3.send_config_request()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

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
        self.faulted = False

    def on_response(self, response: Response) -> None:
        response.log(logger, logging.INFO)

    def on_idle(self) -> None:
        logger.info("Idle")

    def on_fault(self, fault: Fault) -> None:
        logger.error(f"Fault: {fault.name}")
        self.faulted = True


if __name__ == "__main__":
    cli()
