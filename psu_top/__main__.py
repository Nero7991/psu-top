"""Command-line entry point for psu-top."""

from __future__ import annotations

import argparse
import sys

import serial

from . import detect
from .app import PSUTopApp


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="psu-top",
        description="Terminal monitor and controller for SCPI bench power supplies",
    )
    parser.add_argument("--port", default=None, help="serial port (default: auto-detect by probing all ports)")
    parser.add_argument("--baud", type=int, default=115200, help="baud rate (default: 115200)")
    parser.add_argument("--interval", type=float, default=0.3, help="seconds between polls (default: 0.3)")
    return parser.parse_args(argv)


def _interactive() -> bool:
    return sys.stdin.isatty()


def resolve_port(args) -> tuple[str | None, str | None]:
    """Return the port to use, or an error message if it can't be resolved.

    An explicit --port is taken as-is (validated on open later); otherwise we
    probe every serial port and pick the one supply that answers. When several
    answer and we have a terminal, the user picks one interactively; piped or
    non-interactive, we fall back to listing them and asking for --port.
    """
    if args.port is not None:
        return args.port, None
    matches = detect.scan(args.baud)
    if len(matches) > 1 and _interactive():
        chosen = detect.prompt_choice(matches)
        if chosen is None:
            return None, "no supply selected."
        return chosen, None
    return detect.select_port(matches, detect.list_ports())


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.interval <= 0:
        print(f"psu-top: --interval must be positive (got {args.interval})", file=sys.stderr)
        return 1
    port, error = resolve_port(args)
    if error is not None:
        print(f"psu-top: {error}", file=sys.stderr)
        return 1
    try:
        serial.Serial(port, args.baud, timeout=1).close()
    except (serial.SerialException, OSError) as exc:
        print(f"psu-top: cannot open {port}: {exc}", file=sys.stderr)
        return 1
    PSUTopApp(port, args.baud, args.interval).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
