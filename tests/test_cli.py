"""Tests for command-line argument parsing and port resolution."""

from psu_top import detect
from psu_top.__main__ import parse_args, resolve_port


def test_defaults():
    args = parse_args([])
    assert args.port is None  # None means auto-detect
    assert args.baud == 115200
    assert args.interval == 0.3


def test_overrides():
    args = parse_args(["--port", "/dev/ttyUSB1", "--baud", "9600", "--interval", "1.0"])
    assert args.port == "/dev/ttyUSB1"
    assert args.baud == 9600
    assert args.interval == 1.0


def test_explicit_port_skips_scan(monkeypatch):
    scanned = []
    monkeypatch.setattr(detect, "scan", lambda *a, **k: scanned.append(True) or [])
    port, error = resolve_port(parse_args(["--port", "/dev/ttyUSB9"]))
    assert port == "/dev/ttyUSB9"
    assert error is None
    assert not scanned  # an explicit port must never trigger probing


def test_autodetect_single_supply(monkeypatch):
    monkeypatch.setattr(detect, "scan", lambda baud, *a, **k: [("/dev/ttyUSB1", "KIPRIM,DC310S")])
    monkeypatch.setattr(detect, "list_ports", lambda: ["/dev/ttyUSB0", "/dev/ttyUSB1"])
    port, error = resolve_port(parse_args([]))
    assert port == "/dev/ttyUSB1"
    assert error is None


def test_autodetect_none_found_returns_error(monkeypatch):
    monkeypatch.setattr(detect, "scan", lambda *a, **k: [])
    monkeypatch.setattr(detect, "list_ports", lambda: ["/dev/ttyUSB0"])
    port, error = resolve_port(parse_args([]))
    assert port is None
    assert "no power supply found" in error


_MULTI = [("/dev/ttyUSB0", "KIPRIM,DC310S"), ("/dev/ttyUSB1", "KIPRIM,DC605Pro")]


def test_multiple_interactive_uses_picker(monkeypatch):
    monkeypatch.setattr(detect, "scan", lambda *a, **k: _MULTI)
    monkeypatch.setattr("psu_top.__main__._interactive", lambda: True)
    monkeypatch.setattr(detect, "prompt_choice", lambda matches: matches[1][0])
    port, error = resolve_port(parse_args([]))
    assert port == "/dev/ttyUSB1"
    assert error is None


def test_multiple_interactive_abort_returns_error(monkeypatch):
    monkeypatch.setattr(detect, "scan", lambda *a, **k: _MULTI)
    monkeypatch.setattr("psu_top.__main__._interactive", lambda: True)
    monkeypatch.setattr(detect, "prompt_choice", lambda matches: None)
    port, error = resolve_port(parse_args([]))
    assert port is None
    assert "no supply selected" in error


def test_multiple_non_interactive_falls_back_to_listing(monkeypatch):
    monkeypatch.setattr(detect, "scan", lambda *a, **k: _MULTI)
    monkeypatch.setattr(detect, "list_ports", lambda: ["/dev/ttyUSB0", "/dev/ttyUSB1"])
    monkeypatch.setattr("psu_top.__main__._interactive", lambda: False)
    port, error = resolve_port(parse_args([]))
    assert port is None
    assert "/dev/ttyUSB0" in error and "/dev/ttyUSB1" in error
