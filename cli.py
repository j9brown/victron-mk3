import asyncio
import click
import logging
import vebus

DELAY_BETWEEN_COMMANDS = 2


logging.basicConfig(format="%(message)s")
logger = logging.getLogger("victron_mk3")


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Increase logging output")
def cli(verbose):
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


@cli.command(help="Monitor the status of the attached VE.Bus device")
@click.argument("device", type=str)
def monitor(device: str):
    ac_num_phases = 1
    loop = asyncio.get_event_loop()

    def handler(frame: vebus.Frame):
        frame.log(logger)
        if isinstance(frame, vebus.ACFrame) and frame.ac_num_phases != 0:
            nonlocal ac_num_phases
            ac_num_phases = frame.ac_num_phases

    async def main():
        bus = await vebus.open_bus(device, handler)
        loop.create_task(bus.listen())

        while True:
            bus.send_led_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            bus.send_dc_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            for phase in range(1, ac_num_phases + 1):
                bus.send_ac_request(phase)
                await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            bus.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)

    loop.create_task(main())
    loop.run_forever()


@cli.command(
    help="Set the switch state and current limit of the attached VE.Bus device"
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
    switch_state = vebus.SwitchState[switch_state.upper()]
    logger.info(
        f"Setting switch state to {switch_state.name} and current limit to {current_limit} amps"
    )

    ack = False
    ac_num_phases = 1
    loop = asyncio.get_event_loop()

    def handler(frame: vebus.Frame):
        frame.log(logger)
        if isinstance(frame, vebus.ACFrame) and frame.ac_num_phases != 0:
            nonlocal ac_num_phases
            ac_num_phases = frame.ac_num_phases
        if isinstance(frame, vebus.StateFrame):
            nonlocal ack
            ack = True
            logger.info("Switch state change acknowledged!")

    async def main():
        bus = await vebus.open_bus(device, handler)
        loop.create_task(bus.listen())

        while not ack:
            bus.send_state_request(switch_state, current_limit)
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            bus.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)

        while monitor:
            bus.send_led_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            bus.send_dc_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            for phase in range(1, ac_num_phases + 1):
                bus.send_ac_request(phase)
                await asyncio.sleep(DELAY_BETWEEN_COMMANDS)
            bus.send_config_request()
            await asyncio.sleep(DELAY_BETWEEN_COMMANDS)

        bus.close()
        await bus.wait_closed()

    loop.run_until_complete(main())


if __name__ == "__main__":
    cli()
