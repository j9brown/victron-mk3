# victron-mk3

A Python library for communicating with certain Victron charger and inverter
devices that have VE.Bus ports using the Victron Interface MK3-USB (VE.Bus to USB).

This library provides functions to allow the host computer to act as a remote
control panel for the device. It can monitor the status and performance of
the device and set remote switch state and current limits.

## Compatibility

This library has been tested with the following devices:

- Victron Multiplus II

Please inform the author if you test this library with other potentially compatible
devices.

## Command Line Interface

This repository includes a simple command-line interface for testing the behavior
of the interface.

After connecting the MK3 interface, determine the path of the serial port device
to include on the command-line. In these examples, it is 'tty.usbserial-HQ2217T743W'
but yours may be different depending on the platform.

Before running the CLI, install the required packages.

```
pip install -r requirements.txt
```

### Monitor the status of an attached device

This command continuously queries and displays the status of the LEDs, the charger,
the inverter, and control panel configuration until stopped.

```
python3 cli.py monitor /dev/tty.usbserial-HQ2217T743W
```

### Set the remote switch state and current limit

This command sets the remote switch state to `on` and the current limit to its maximum.

Note that the remote switch state and current limit may persist even after the interface
has been disconnected or the device is turned off. Use the following command to restore
the device to its default behavior.

```
python3 cli.py control /dev/tty.usbserial-HQ2217T743W on
```

This command sets the remote switch state to `charger_only` and the current limit to 12.5 amps
and continues monitoring indefinitely.

```
python3 cli.py control /dev/tty.usbserial-HQ2217T743W charger_only --current-limit 12.5 --monitor
```

Here's what each remote switch state means:

- `on`: Enable the charger and enable the inverter
- `charger_only`: Enable the charger and disable the inverter
- `inverter_only`: Enable the inverter and disable the charger
- `off`: Disable the charger and disable the inverter

The front panel switch and other inputs on the device may override the remote switch state.

- When the device is turned off by the front panel switch or by the remote input pins,
  the interface will be unable to communicate with the device and neither the charger nor
  the inverter will operate.
- When the device is forced to charge only mode using the front panel switch, the inverter
  will not operate regardless of the remote switch state set by this interface.
- Other conditions determined by the device may also apply such as constaints on the
  mains voltage and battery state of charge.
