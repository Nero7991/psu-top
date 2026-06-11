# psu-top — Design

Date: 2026-06-10
Status: Approved (verbal, this document is the written record)

## Purpose

An htop/nvtop-style terminal monitor and controller for SCPI-speaking bench power
supplies, initially the Kiprim DC310S (rebranded OWON SPE3103). Live voltage,
current, and power readouts with history graphs, plus keyboard control of
setpoints and output state. Intended to be open-sourced.

## Target hardware and protocol

- Kiprim DC310S over USB serial (CH340, `1a86:7523`), default `/dev/ttyUSB0`,
  115200 8N1.
- Protocol: SCPI-like ASCII. Commands terminated with LF; responses end with
  CR LF. Case-insensitive. Invalid commands return `ERR`. No official
  programming manual; community reference:
  https://github.com/maximweb/kiprim-dc310s
- Commands used: `*IDN?`, `OUTPut?`, `OUTPut <0/1>`, `VOLTage?`, `VOLTage x.xxx`,
  `CURRent?`, `CURRent x.xxx`, `MEASure:VOLTage?`, `MEASure:CURRent?`.

## Approach

Python package using Textual for the TUI (chosen over a hand-rolled rich
Live + termios loop because the tool needs keybindings, modal numeric input,
and built-in sparklines; Textual provides all three). Dependencies: `textual`,
`pyserial`.

## Architecture

Two units with one interface between them:

- `psu_top/scpi.py` — `PSUClient`: owns the serial port. Methods:
  `identify()`, `measured_voltage()`, `measured_current()`, `set_voltage(v)`,
  `set_current(a)`, `get_voltage_setpoint()`, `get_current_setpoint()`,
  `output_on()`, `output_off()`, `get_output()`. All serial I/O goes through
  one internal lock so UI-triggered writes never interleave with poll reads.
  Constructor takes any pyserial-compatible object, so tests inject a fake.
- `psu_top/app.py` — Textual application. A background worker polls the client
  at a configurable interval (default ~3 Hz) and updates reactive state; the
  UI never touches the serial port directly.
- `psu_top/__main__.py` — argument parsing (`--port`, `--baud`, `--interval`)
  and entry point. Console script `psu-top` declared in `pyproject.toml`.

## UI

```
 PSU  Kiprim DC310S  /dev/ttyUSB0  [CONNECTED]            OUTPUT: ON (CV)
┌─ Voltage ──────────────────────────┐┌─ Current ──────────────────────────┐
│  18.063 V   (set 18.100)           ││  1.077 A    (set 2.100)            │
│  sparkline history                 ││  sparkline history                 │
└────────────────────────────────────┘└────────────────────────────────────┘
  Power: 19.45 W
 [o] output on/off  [v] set voltage  [c] set current  [q] quit
```

- CV/CC is derived (no documented mode query): measured current at or near the
  current setpoint means CC, otherwise CV. Only shown while output is on.
- `v` / `c` open a numeric input; values are validated and clamped to
  0–30 V / 0–10 A before sending. Escape cancels.
- `o` toggles output with no confirmation (matches the physical front-panel
  button).
- Power is computed as measured V x measured I.
- Sparkline history holds the last few minutes of samples in memory; nothing
  is persisted.

## Error handling

- Serial open failure at startup: print a clear message and exit nonzero
  (no TUI).
- Disconnect while running (USB unplug, read timeout): header flips to
  `[DISCONNECTED]`, controls are disabled, and the app retries the port every
  2 s. On reconnect it resumes polling.
- `ERR` or unparseable response: that sample is dropped and counted; the UI
  shows the last good value.

## Testing

- Unit tests for `PSUClient` against a fake serial object: command framing
  (LF out, CR LF in), response parsing, clamping, ERR handling.
- The Textual UI is verified live against the real PSU, not unit-tested.

## Out of scope (v1)

- Multi-channel or multi-PSU support
- Data logging to file / CSV export
- Other PSU protocol back ends (the client/UI split keeps the door open)
- PyPI publication (structure is ready for it, but not part of this work)
