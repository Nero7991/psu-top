# psu-top

htop-style terminal monitor and controller for SCPI bench power supplies.
Built for and tested on the Kiprim DC310S (a rebranded OWON SPE3103) over
USB serial.

```
 PSU  KIPRIM DC310S  /dev/ttyUSB0  [CONNECTED]   OUTPUT: ON (CV)
┌─ Voltage ──────────────────────────────────────────────────────────┐
│  18.063 V   (set 18.100)                                            │
│                          ▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃▄▅▆▇█▇▆▅▄ │
└──────────────────────────────────────────────────────────────────────┘
┌─ Current ──────────────────────────────────────────────────────────┐
│   1.077 A   (set 2.100)                                             │
│              ▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃▄▅▆▇█▇ │
└──────────────────────────────────────────────────────────────────────┘
 Power:  19.45 W
 o Output on/off  v Set voltage  c Set current  r Clear graphs  q Quit
```

The graphs are rolling, oscilloscope-style strip charts: one sample per column,
newest on the right, scrolling left one column per poll. Heights autoscale to
the samples currently on screen so low-magnitude signals stay visible.

## Install

```bash
pip install psu-top
```

From a checkout, for development:

```bash
pip install --user -e .
```

## Usage

```bash
psu-top                          # defaults: /dev/ttyUSB0, 115200, 0.3 s poll
psu-top --port /dev/ttyUSB1 --baud 115200 --interval 0.5
```

Keys: `v` set voltage, `c` set current, `o` toggle output, `r` clear graphs
(empties the trace and rescales fresh), `q` quit.
Values are clamped to 0-30 V / 0-10 A before sending.

## Protocol notes (Kiprim DC310S, FW V5.2.0)

- 115200 8N1 over the built-in CH340 USB serial adapter.
- Commands MUST be terminated with CR LF (`\r\n`). LF alone gets no
  response from this firmware, although other documentation claims LF
  suffices. Responses end with CR LF. Invalid commands return `ERR`.
- Commands used: `*IDN?`, `OUTP?`, `OUTP <0/1>`, `VOLT?`, `VOLT x.xxx`,
  `CURR?`, `CURR x.xxx`, `MEAS:VOLT?`, `MEAS:CURR?`.
- CV/CC display is derived (measured current at/near the limit means CC);
  the device has no documented mode query.
- Command reference: https://github.com/maximweb/kiprim-dc310s

## Linux note: brltty steals CH340 ports

On Ubuntu, `brltty` claims CH340 adapters and detaches `ttyUSB0`. Fix:

```bash
sudo systemctl mask brltty-udev.service
sudo systemctl disable --now brltty.service
```

## License

MIT
