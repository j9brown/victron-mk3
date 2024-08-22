# victron-mk3

A Python library for communicating with certain Victron charger and inverter
devices that have VE.Bus ports using the Victron Interface MK3-USB (VE.Bus to USB).

This library provides functions to allow the host computer to act as a remote
control panel for the device. It can monitor the status and performance of
the device and set remote switch state and current limits.

See also the [Home Assistant integration](https://github.com/j9brown/victron-mk3-hacs) based
on this library.

## Compatibility

This library has been tested with the following devices:

- Victron Multiplus II

Please inform the author if you test this library with other potentially compatible
devices.

## Command line interface

This repository includes a simple tool for testing the behavior of the MK3 interface.

Before running the CLI, install the required packages.

```
pip install -r requirements.txt
```

After attaching the MK3 interface, determine the path of the serial port device
to include on the command-line. In these examples, it is 'tty.usbserial-HQ2217T743W'
but yours may be different depending on your platform.

### Monitor the status of an attached device

The following command continuously queries and displays the status of the LEDs, the charger,
the inverter, and control panel configuration until stopped.

```
python3 cli.py monitor /dev/tty.usbserial-HQ2217T743W
```

### Set the remote switch state and current limit

The following command sets the remote switch state to `on` and the current limit to its maximum.

Note that the remote switch state and current limit persists even after the interface
has been disconnected or the device is turned off. Use the following command to restore
the device to its default behavior.

```
python3 cli.py control /dev/tty.usbserial-HQ2217T743W on
```

The following command sets the remote switch state to `charger_only` and the current limit to 12.5 amps
and continues monitoring indefinitely.

```
python3 cli.py control /dev/tty.usbserial-HQ2217T743W charger_only --current-limit 12.5 --monitor
```

The following command activates standby mode and sets the remote switch state to `off` to prevent the
interface from becoming unresponsive while the device is off. Refer to the standby section for
more details.

```
python3 cli.py control /dev/tty.usbserial-HQ2217T743W off --standby
```

Here's what each remote switch state means:

- `on`: Enable the charger and enable the inverter.
- `charger_only`: Enable the charger and disable the inverter.
- `inverter_only`: Enable the inverter and disable the charger.
- `off`: Disable the charger and disable the inverter.

The front panel switch and other inputs on the device may override the remote switch state.

- When the device is turned off by the front panel switch or by the remote on/off connection,
  neither the charger nor the inverter will operate.
- When the device is forced to charge only mode using the front panel switch, the inverter
  will not operate regardless of the remote switch state set by this interface.
- Other conditions determined by the device may also apply such as constraints on the
  mains voltage and battery state of charge.

### Probe whether a device is attached to the interface and operational

The following command attempts to connect to a device using the interface and reports whether
it is operational or the reason it was unable to connect.

```
python3 cli.py probe /dev/tty.usbserial-HQ2217T743W
```

## Standby

When the device is turned off, it may go to sleep and shut off its internal power supply
to avoid draining the batteries. Because the MK3 interface is powered from device's VE.Bus
port, it too will lose power and it will become unresponsive. Consequently, you will not
be able to turn the device back on again using the interface.

Don't panic!

There are two ways to resolve this issue:

- When standby mode is enabled, the interface will prevent the device from going to sleep
  as long as it remains connected to the device's VE.Bus. Note that the device draws more energy
  from the batteries while in standby than it would while sleeping.
- The device will automatically wake up from sleep whenever power is supplied to its AC input.

So if the device is asleep and it is not responding to the MK3 interface, just plug it into
the AC mains to wake it up. Try sending the command again and consider enabling standby mode.

## Build for distribution

```
pip install setuptools build
python3 -m build
```
