"""Auto-detection of the power supply when no --port is given.

We enumerate every serial port, send ``*IDN?`` to each, and keep the ones
whose reply identifies a supported supply. Probing writes to ports that may
be other instruments, so this only runs when the user omits --port.
"""

from __future__ import annotations

import sys

import serial
import serial.tools.list_ports

from .scpi import PSUClient, PSUError

# Vendor/model tokens that mark an *IDN? reply as a supply we drive.
PSU_TAGS = ("KIPRIM", "OWON", "SPE")


def looks_like_psu(idn: str) -> bool:
    """True if an *IDN? reply identifies a supported supply."""
    up = idn.upper()
    return any(tag in up for tag in PSU_TAGS)


def list_ports() -> list[str]:
    """Device paths of every serial port currently present."""
    return [info.device for info in serial.tools.list_ports.comports()]


def probe(device: str, baud: int, timeout: float = 0.5) -> str | None:
    """Open ``device`` and return its *IDN? reply if it is a supply, else None."""
    try:
        ser = serial.Serial(device, baud, timeout=timeout)
    except (serial.SerialException, OSError):
        return None
    try:
        idn = PSUClient(ser).identify()
    except (PSUError, serial.SerialException, OSError):
        idn = None
    finally:
        ser.close()
    return idn if idn and looks_like_psu(idn) else None


def scan(baud: int, timeout: float = 0.5) -> list[tuple[str, str]]:
    """Probe all serial ports; return (device, idn) for each that is a supply."""
    matches = []
    for device in list_ports():
        idn = probe(device, baud, timeout)
        if idn is not None:
            matches.append((device, idn))
    return matches


def parse_choice(text: str, count: int) -> int | None:
    """Map a 1-based menu entry to a 0-based index, or None if invalid."""
    text = text.strip()
    if not text.isdigit():
        return None
    n = int(text)
    return n - 1 if 1 <= n <= count else None


def prompt_choice(matches, input_fn=input, out=sys.stderr) -> str | None:
    """Ask the user to pick a supply from ``matches``; return its device.

    Loops until a valid 1-based number is entered. Returns None if the user
    quits ('q'), or on EOF/interrupt (e.g. piped or Ctrl-C/Ctrl-D).
    """
    print("Multiple power supplies found:", file=out)
    for i, (dev, idn) in enumerate(matches, start=1):
        print(f"  {i}) {dev}  ({idn})", file=out)
    prompt = f"Select 1-{len(matches)} (q to quit): "
    while True:
        try:
            raw = input_fn(prompt)
        except (EOFError, KeyboardInterrupt):
            return None
        index = parse_choice(raw, len(matches))
        if index is not None:
            return matches[index][0]
        if raw.strip().lower() in ("q", "quit"):
            return None
        print("Invalid selection, try again.", file=out)


def select_port(
    matches: list[tuple[str, str]], all_devices: list[str]
) -> tuple[str | None, str | None]:
    """Resolve probe results to a single port or an explanatory error.

    Returns ``(port, None)`` on a unique match, else ``(None, message)``.
    """
    if len(matches) == 1:
        return matches[0][0], None
    if len(matches) > 1:
        lines = "\n".join(f"  {dev}  ({idn})" for dev, idn in matches)
        return None, (
            "multiple power supplies found; pick one with --port:\n" + lines
        )
    if all_devices:
        return None, (
            "no power supply found. Probed ports (none answered as a "
            "Kiprim/OWON supply): " + ", ".join(all_devices) + "\n"
            "Connect the supply, or pass its port with --port."
        )
    return None, (
        "no serial ports detected. Connect the supply (check the USB cable "
        "and that brltty isn't holding the CH340 -- see the README), or pass "
        "--port."
    )
