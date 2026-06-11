"""Tests for command-line argument parsing."""

from psu_top.__main__ import parse_args


def test_defaults():
    args = parse_args([])
    assert args.port == "/dev/ttyUSB0"
    assert args.baud == 115200
    assert args.interval == 0.3


def test_overrides():
    args = parse_args(["--port", "/dev/ttyUSB1", "--baud", "9600", "--interval", "1.0"])
    assert args.port == "/dev/ttyUSB1"
    assert args.baud == 9600
    assert args.interval == 1.0
