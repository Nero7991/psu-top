"""Serial SCPI client for the Kiprim DC310S (OWON SPE3103).

Protocol (verified live on FW V5.2.0): commands must be terminated with
CR LF -- LF alone gets no response, despite community docs claiming
otherwise. Responses end with CR LF. Invalid commands return "ERR".
Community command reference: https://github.com/maximweb/kiprim-dc310s
"""

from __future__ import annotations

import threading

TERMINATOR = b"\r\n"


class PSUError(Exception):
    """Raised when the PSU returns ERR, nothing, or garbage."""


class PSUClient:
    """Talks to the PSU over a pyserial-compatible object.

    All serial I/O is serialized through one lock so writes triggered by
    the UI never interleave with poll-loop reads.
    """

    VOLTAGE_MAX = 30.0
    CURRENT_MAX = 10.0

    def __init__(self, ser) -> None:
        self._ser = ser
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._ser.close()

    def identify(self) -> str:
        return self._query("*IDN?")

    def measured_voltage(self) -> float:
        return self._query_float("MEAS:VOLT?")

    def measured_current(self) -> float:
        return self._query_float("MEAS:CURR?")

    def get_voltage_setpoint(self) -> float:
        return self._query_float("VOLT?")

    def get_current_setpoint(self) -> float:
        return self._query_float("CURR?")

    def get_output(self) -> bool:
        text = self._query("OUTP?").upper()
        if text not in ("ON", "OFF"):
            raise PSUError(f"unexpected response to 'OUTP?': {text!r}")
        return text == "ON"

    def set_voltage(self, volts: float) -> None:
        self._write(f"VOLT {_clamp(volts, self.VOLTAGE_MAX):.3f}")

    def set_current(self, amps: float) -> None:
        self._write(f"CURR {_clamp(amps, self.CURRENT_MAX):.3f}")

    def output_on(self) -> None:
        self._write("OUTP 1")

    def output_off(self) -> None:
        self._write("OUTP 0")

    def _query(self, cmd: str) -> str:
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(cmd.encode("ascii") + TERMINATOR)
            raw = self._ser.read_until(TERMINATOR)
        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            raise PSUError(f"no response to {cmd!r}")
        if text == "ERR":
            raise PSUError(f"device returned ERR for {cmd!r}")
        return text

    def _query_float(self, cmd: str) -> float:
        text = self._query(cmd)
        try:
            return float(text)
        except ValueError:
            raise PSUError(f"unparseable response to {cmd!r}: {text!r}") from None

    def _write(self, cmd: str) -> None:
        with self._lock:
            self._ser.write(cmd.encode("ascii") + TERMINATOR)


def _clamp(value: float, maximum: float) -> float:
    return max(0.0, min(float(value), maximum))
