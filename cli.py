import asyncio
import click
import logging
from victron_mk3 import ACFrame, Frame, SwitchState, StateFrame, logger, open_victron_mk3

DELAY_BETWEEN_COMMANDS = 2


logging.basicConfig(format="%(message)s")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Increase logging output")
def cli(verbose):
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


@cli.command(
    help="Monitor the status of the device attached to a Victron MK3 interface"
)
@click.argument("device", type=str)
def monitor(device: str) -> None:
    ac_num_phases = 1
    loop = asyncio.get_event_loop()

    def handler(frame: Frame) -> None:
        frame.log(logger)
        if isinstance(frame, ACFrame) and frame.ac_num_phases != 0:
            nonlocal ac_num_phases
            ac_num_phases = frame.ac_num_phases

    async def main() -> None:
        mk3 = await open_victron_mk3(device, handler)
        loop.create_task(mk3.listen())

        while True:
            mk3.send_led_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            mk3.send_dc_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            for phase in range(1, ac_num_phases + 1):
                mk3.send_ac_request(phase)
                await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            mk3.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)

    loop.run_until_complete(main())


@cli.command(
    help="Set the switch state and current limit of the device attached to a Victron MK3 interface"
)
@click.argument("device", type=str)
@click.argument(
    "switch_state", type=click.Choice(["on", "off", "charger_only", "inverter_only"])
)
@click.option("--current-limit", type=float, help="Current limit in amps")
@click.option(
    "--monitor/--no-monitor", help="Keep monitoring the status after acknowledgment"
)
def control(device: str, switch_state: str, current_limit: float, monitor: bool):
    switch_state = SwitchState[switch_state.upper()]
    logger.info(
        f"Setting switch state to {switch_state.name} and current limit to {current_limit} amps"
    )

    ack = False
    ac_num_phases = 1
    loop = asyncio.get_event_loop()

    def handler(frame: Frame) -> None:
        frame.log(logger)
        if isinstance(frame, ACFrame) and frame.ac_num_phases != 0:
            nonlocal ac_num_phases
            ac_num_phases = frame.ac_num_phases
        if isinstance(frame, StateFrame):
            nonlocal ack
            ack = True
            logger.info("Switch state change acknowledged!")

    async def main() -> None:
        mk3 = await open_victron_mk3(device, handler)
        loop.create_task(mk3.listen())

        while not ack:
            mk3.send_state_request(switch_state, current_limit)
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            mk3.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)

        while monitor:
            mk3.send_led_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            mk3.send_dc_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            for phase in range(1, ac_num_phases + 1):
                mk3.send_ac_request(phase)
                await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            mk3.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)

        mk3.close()
        await mk3.wait_closed()

    loop.run_until_complete(main())


if __name__ == "__main__":
    cli()
