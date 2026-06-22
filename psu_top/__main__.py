"""Command-line entry point for psu-top."""

from __future__ import annotations

import argparse
import sys

import serial

from .app import PSUTopApp


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="psu-top",
        description="Terminal monitor and controller for SCPI bench power supplies",
    )
    parser.add_argument("--port", default="/dev/ttyUSB0", help="serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="baud rate (default: 115200)")
    parser.add_argument("--interval", type=float, default=0.3, help="seconds between polls (default: 0.3)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.interval <= 0:
        print(f"psu-top: --interval must be positive (got {args.interval})", file=sys.stderr)
        return 1
    try:
        serial.Serial(args.port, args.baud, timeout=1).close()
    except (serial.SerialException, OSError) as exc:
        print(f"psu-top: cannot open {args.port}: {exc}", file=sys.stderr)
        return 1
    PSUTopApp(args.port, args.baud, args.interval).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
