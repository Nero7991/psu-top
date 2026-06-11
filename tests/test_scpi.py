"""Unit tests for PSUClient against a fake serial port."""

import pytest

from psu_top.scpi import PSUClient, PSUError


class FakeSerial:
    """Minimal stand-in for serial.Serial."""

    def __init__(self, responses=None):
        self.written = []
        self._responses = list(responses or [])

    def write(self, data):
        self.written.append(data)

    def reset_input_buffer(self):
        pass

    def read_until(self, terminator=b"\n"):
        return self._responses.pop(0) if self._responses else b""


def test_query_is_crlf_terminated():
    ser = FakeSerial([b"KIPRIM,DC310S,25012662,FV:V5.2.0\r\n"])
    PSUClient(ser).identify()
    assert ser.written == [b"*IDN?\r\n"]


def test_identify_strips_crlf():
    ser = FakeSerial([b"KIPRIM,DC310S,25012662,FV:V5.2.0\r\n"])
    assert PSUClient(ser).identify() == "KIPRIM,DC310S,25012662,FV:V5.2.0"


def test_measured_voltage_parses_float():
    ser = FakeSerial([b"18.063\r\n"])
    assert PSUClient(ser).measured_voltage() == pytest.approx(18.063)


def test_measured_current_parses_float():
    ser = FakeSerial([b"1.077\r\n"])
    assert PSUClient(ser).measured_current() == pytest.approx(1.077)


def test_setpoint_queries():
    ser = FakeSerial([b"18.100\r\n", b"2.100\r\n"])
    client = PSUClient(ser)
    assert client.get_voltage_setpoint() == pytest.approx(18.1)
    assert client.get_current_setpoint() == pytest.approx(2.1)
    assert ser.written == [b"VOLT?\r\n", b"CURR?\r\n"]


def test_get_output_on_and_off():
    assert PSUClient(FakeSerial([b"ON\r\n"])).get_output() is True
    assert PSUClient(FakeSerial([b"OFF\r\n"])).get_output() is False


def test_err_response_raises():
    with pytest.raises(PSUError):
        PSUClient(FakeSerial([b"ERR\r\n"])).measured_voltage()


def test_empty_response_raises():
    with pytest.raises(PSUError):
        PSUClient(FakeSerial([])).measured_voltage()


def test_set_voltage_formats_three_decimals():
    ser = FakeSerial()
    PSUClient(ser).set_voltage(12.5)
    assert ser.written == [b"VOLT 12.500\r\n"]


def test_set_voltage_clamps_to_range():
    ser = FakeSerial()
    client = PSUClient(ser)
    client.set_voltage(99)
    client.set_voltage(-3)
    assert ser.written == [b"VOLT 30.000\r\n", b"VOLT 0.000\r\n"]


def test_set_current_clamps_to_range():
    ser = FakeSerial()
    client = PSUClient(ser)
    client.set_current(99)
    client.set_current(-1)
    assert ser.written == [b"CURR 10.000\r\n", b"CURR 0.000\r\n"]


def test_output_on_off_commands():
    ser = FakeSerial()
    client = PSUClient(ser)
    client.output_on()
    client.output_off()
    assert ser.written == [b"OUTP 1\r\n", b"OUTP 0\r\n"]
