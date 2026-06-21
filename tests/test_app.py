"""Headless UI tests for PSUTopApp via the Textual pilot.

No real serial port is ever opened: psu_top.app.serial.Serial is patched to
raise OSError in every test, and connected states are produced by injecting a
FakeClient directly into the app before it starts.
"""

from __future__ import annotations

import pytest
import serial
from textual.widgets import Input, Static

import psu_top.app as app_module
from psu_top.app import WINDOW_SECONDS, History, MeterPanel, PSUTopApp, _Bars
from psu_top.scpi import PSUError

INTERVAL = 0.05


class FakeClient:
    """Stand-in for PSUClient: canned readings + recorded control calls."""

    def __init__(
        self,
        volts: float = 12.0,
        amps: float = 0.5,
        set_volts: float = 12.0,
        set_amps: float = 2.0,
        output_on: bool = True,
    ) -> None:
        self.volts = volts
        self.amps = amps
        self.set_volts = set_volts
        self.set_amps = set_amps
        self.output = output_on
        self.fail = False  # when True, every query raises PSUError
        self.fail_controls = False  # when True, every control call raises SerialException
        self.close_raises = False  # when True, close() raises OSError after recording
        self.set_voltage_calls: list[float] = []
        self.set_current_calls: list[float] = []
        self.output_on_calls = 0
        self.output_off_calls = 0
        self.closed = False

    def _maybe_fail(self) -> None:
        if self.fail:
            raise PSUError("injected failure")

    def measured_voltage(self) -> float:
        self._maybe_fail()
        return self.volts

    def measured_current(self) -> float:
        self._maybe_fail()
        return self.amps

    def get_voltage_setpoint(self) -> float:
        self._maybe_fail()
        return self.set_volts

    def get_current_setpoint(self) -> float:
        self._maybe_fail()
        return self.set_amps

    def get_output(self) -> bool:
        self._maybe_fail()
        return self.output

    def _maybe_fail_control(self) -> None:
        if self.fail_controls:
            raise serial.SerialException("injected control failure")

    def set_voltage(self, volts: float) -> None:
        self.set_voltage_calls.append(volts)
        self._maybe_fail_control()

    def set_current(self, amps: float) -> None:
        self.set_current_calls.append(amps)
        self._maybe_fail_control()

    def output_on(self) -> None:
        self.output_on_calls += 1
        self._maybe_fail_control()

    def output_off(self) -> None:
        self.output_off_calls += 1
        self._maybe_fail_control()

    def close(self) -> None:
        self.closed = True
        if self.close_raises:
            raise OSError("injected close failure")


class ScriptedSerial:
    """Command-aware fake serial.Serial so the real connect path can run."""

    RESPONSES = {
        b"*IDN?\r\n": b"KIPRIM,DC310S,25012662,FV:V5.2.0\r\n",
        b"MEAS:VOLT?\r\n": b"12.000\r\n",
        b"MEAS:CURR?\r\n": b"0.500\r\n",
        b"VOLT?\r\n": b"12.500\r\n",
        b"CURR?\r\n": b"2.000\r\n",
        b"OUTP?\r\n": b"ON\r\n",
    }

    def __init__(self) -> None:
        self._last: bytes | None = None

    def write(self, data: bytes) -> None:
        self._last = bytes(data)

    def reset_input_buffer(self) -> None:
        pass

    def read_until(self, terminator: bytes = b"\n") -> bytes:
        return self.RESPONSES.get(self._last, b"ERR\r\n")

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def no_real_serial(monkeypatch):
    """Make any attempt to open a serial port fail instead of touching hardware."""

    def boom(*args, **kwargs):
        raise OSError("no serial in tests")

    monkeypatch.setattr(app_module.serial, "Serial", boom)


def make_app(client: FakeClient | None = None) -> PSUTopApp:
    app = PSUTopApp(port="/dev/fake0", baud=115200, interval=INTERVAL)
    if client is not None:
        app._client = client
        app._identity = "KIPRIM DC310S"
    return app


def header_text(app: PSUTopApp) -> str:
    return str(app.query_one("#header", Static).content)


async def wait_for_header(pilot, app: PSUTopApp, substring: str, timeout: float = 3.0) -> str:
    """Pause in small steps until the header contains substring (or fail)."""
    elapsed = 0.0
    while elapsed < timeout:
        text = header_text(app)
        if substring in text:
            return text
        await pilot.pause(0.05)
        elapsed += 0.05
    pytest.fail(f"header never contained {substring!r}; last: {header_text(app)!r}")


# ---- connection states ----


async def test_disconnected_when_serial_fails():
    app = make_app()
    async with app.run_test() as pilot:
        text = await wait_for_header(pilot, app, "[DISCONNECTED]")
        assert "unknown" in text
        assert app._client is None


async def test_connected_header_and_meters():
    client = FakeClient(volts=12.0, amps=0.5, set_volts=12.5, set_amps=2.0, output_on=True)
    app = make_app(client)
    async with app.run_test() as pilot:
        text = await wait_for_header(pilot, app, "[CONNECTED]")
        assert "KIPRIM DC310S" in text
        assert "/dev/fake0" in text
        assert "OUTPUT: ON" in text

        power = str(app.query_one("#power", Static).content)
        assert f"{12.0 * 0.5:6.2f} W" in power  # V * I

        volt_text = str(app.query_one("#voltage", MeterPanel).query_one(".meter-value", Static).content)
        assert "12.000 V" in volt_text
        assert "(set 12.500)" in volt_text

        curr_text = str(app.query_one("#current", MeterPanel).query_one(".meter-value", Static).content)
        assert "0.500 A" in curr_text
        assert "(set 2.000)" in curr_text


async def test_output_off_shown_in_header():
    app = make_app(FakeClient(output_on=False))
    async with app.run_test() as pilot:
        text = await wait_for_header(pilot, app, "OUTPUT: OFF")
        assert "(CC)" not in text
        assert "(CV)" not in text


async def test_connect_builds_client_from_serial(monkeypatch):
    """Exercise _connect itself: Serial succeeds (scripted fake), the app builds
    a real PSUClient, parses the IDN identity, and goes CONNECTED."""
    monkeypatch.setattr(app_module.serial, "Serial", lambda *a, **k: ScriptedSerial())
    app = make_app()  # no injected client; the poll loop must connect on its own
    async with app.run_test() as pilot:
        text = await wait_for_header(pilot, app, "[CONNECTED]")
        assert "KIPRIM DC310S" in text  # first two IDN fields, comma replaced
        assert app._client is not None
        volt_text = str(app.query_one("#voltage", MeterPanel).query_one(".meter-value", Static).content)
        assert "12.000 V" in volt_text


# ---- rolling history graph ----


def _bars_to_rows(renderable, width: int, height: int) -> list[str]:
    """Render a _Bars renderable to plain text rows (no color)."""
    from rich.console import Console

    con = Console(width=width, force_terminal=False, color_system=None)
    with con.capture() as cap:
        con.print(renderable, end="")
    return cap.get().split("\n")


def test_history_starts_empty_not_zero_filled():
    """The graph must not be pre-seeded with a zero baseline."""
    panel = MeterPanel("Voltage", "V", capacity=1000)
    assert len(panel._graph._samples) == 0
    assert panel._graph._samples.maxlen == 1000


def test_capacity_is_five_minute_window():
    """The window holds WINDOW_SECONDS worth of samples at the poll rate."""
    app = PSUTopApp(port="/dev/fake0", baud=115200, interval=0.3)
    assert app._capacity == round(WINDOW_SECONDS / 0.3)


def test_history_columns_dont_stretch_and_fill_right_to_left():
    """A part-full window occupies only its right portion -- newest at the right
    edge, one sample per column, no stretching."""
    h = History(capacity=100)
    for _ in range(10):
        h.add(5.0)
    cols = h._columns(width=40)
    filled = [c is not None for c in cols]
    # one column per sample, anchored to the right edge; the rest stay empty
    assert filled[-1] and not filled[0]
    assert sum(filled) == 10  # 10 samples -> exactly 10 filled columns
    assert filled == sorted(filled)  # contiguous block on the right (no gaps)


def test_each_update_scrolls_exactly_one_column():
    """Rolling rate == update rate: one new sample advances the trace by exactly
    one column, every other bar keeping its height (just shifted left)."""
    import math

    h = History(capacity=100)
    for i in range(50):
        h.add(math.sin(i / 3))
    width = 20
    before = h._columns(width)
    h.add(0.123)  # a single new sample
    after = h._columns(width)
    for c in range(1, width):  # the whole trace slid left by one column
        assert before[c] == after[c - 1], (c, before[c], after[c - 1])
    assert after[-1] == 0.123  # newest sample is the new rightmost column


def test_clear_empties_the_graph():
    """Clearing the graph drops all samples so the next reading rescales fresh."""
    h = History(capacity=100)
    for v in (1.0, 2.0, 3.0):
        h.add(v)
    assert len(h._samples) == 3
    h.clear()
    assert len(h._samples) == 0
    assert all(c is None for c in h._columns(width=10))


def test_autoscale_uses_whole_window_so_low_signals_are_visible():
    """A window of small currents autoscales to its own min/max, so the spread
    fills the graph height instead of collapsing to an invisible floor line."""
    h = History(capacity=5)
    for v in (0.01, 0.02, 0.03, 0.04, 0.05):  # tiny currents, ascending
        h.add(v)
    width = 5
    cols = h._columns(width)  # one sample per column, right-aligned
    data = h._samples
    bars = _Bars(cols, 4, min(data), max(data), "white")
    rows = _bars_to_rows(bars, width, 4)
    # smallest value (left column) is near the floor; largest (right) reaches top
    assert rows[0][-1] != " "   # top row, max column filled -> zoomed in
    assert rows[0][0] == " "    # top row, min column empty
    assert rows[-1][0] != " "   # bottom row, min column still has a base bar


def test_scale_is_independent_of_window_size():
    """The same min/max range renders the max at full height regardless of the
    absolute magnitude -- proving it is window-relative, not a fixed scale."""
    small = History(capacity=2)
    small.add(0.01)
    small.add(0.05)
    big = History(capacity=2)
    big.add(2.0)
    big.add(10.0)
    rows_small = _bars_to_rows(
        _Bars(small._columns(2), 4, 0.01, 0.05, "white"), 2, 4
    )
    rows_big = _bars_to_rows(
        _Bars(big._columns(2), 4, 2.0, 10.0, "white"), 2, 4
    )
    # both reach full height for their own max despite very different magnitudes
    assert rows_small[0][-1] != " " and rows_big[0][-1] != " "


async def test_graph_plots_only_real_samples():
    """Once connected, the voltage graph holds only measured values, never an
    injected 0.0 floor."""
    client = FakeClient(volts=12.0, set_volts=12.0, output_on=True)
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        await pilot.pause(0.2)  # let a few samples accumulate
        graph = app.query_one("#voltage", MeterPanel)._graph
        data = list(graph._samples)
        assert data, "graph should have at least one sample"
        assert all(v == 12.0 for v in data), data
        assert len(data) <= graph._capacity


async def test_r_key_clears_both_graphs():
    """The 'r' binding empties both channel graphs so they rescale fresh."""
    client = FakeClient()
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        await pilot.pause(0.4)  # accumulate a clear backlog of samples
        volt = app.query_one("#voltage", MeterPanel)._graph
        curr = app.query_one("#current", MeterPanel)._graph
        assert len(volt._samples) >= 4 and len(curr._samples) >= 4
        await pilot.press("r")
        # cleared to empty; the background poll may have re-added at most a sample
        assert len(volt._samples) <= 1
        assert len(curr._samples) <= 1


# ---- CV/CC derivation ----


async def test_cc_mode_when_current_at_limit():
    app = make_app(FakeClient(amps=1.98, set_amps=2.0, output_on=True))
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "(CC)")


async def test_cv_mode_when_current_below_limit():
    app = make_app(FakeClient(amps=0.5, set_amps=2.0, output_on=True))
    async with app.run_test() as pilot:
        text = await wait_for_header(pilot, app, "(CV)")
        assert "(CC)" not in text


# ---- voltage/current entry ----


async def test_set_voltage_via_entry():
    client = FakeClient()
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        await pilot.press("v")
        entry = app.query_one("#entry", Input)
        assert entry.has_class("visible")
        assert "voltage" in entry.placeholder
        await pilot.press("1", "3", ".", "5", "enter")
        await pilot.pause(0.3)  # let the setter worker thread run
        assert client.set_voltage_calls == [13.5]
        assert client.set_current_calls == []
        assert not entry.has_class("visible")


async def test_set_current_via_entry():
    client = FakeClient()
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        await pilot.press("c")
        entry = app.query_one("#entry", Input)
        assert "current" in entry.placeholder
        await pilot.press("0", ".", "7", "5", "enter")
        await pilot.pause(0.3)
        assert client.set_current_calls == [0.75]
        assert client.set_voltage_calls == []


async def test_escape_cancels_entry():
    client = FakeClient()
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        await pilot.press("v")
        entry = app.query_one("#entry", Input)
        assert entry.has_class("visible")
        await pilot.press("1", "2")
        await pilot.press("escape")
        assert not entry.has_class("visible")
        assert app._entry_target is None
        await pilot.pause(0.2)
        assert client.set_voltage_calls == []
        assert client.set_current_calls == []


async def test_non_numeric_entry_ignored():
    client = FakeClient()
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        await pilot.press("v")
        await pilot.press("x", "y", "z", "enter")
        await pilot.pause(0.2)
        assert client.set_voltage_calls == []
        assert client.set_current_calls == []
        assert not app.query_one("#entry", Input).has_class("visible")


async def test_submit_without_pending_target_ignored():
    client = FakeClient()
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        await pilot.press("v")
        await pilot.press("5")
        app._entry_target = None  # prompt state lost before Enter
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert client.set_voltage_calls == []
        assert client.set_current_calls == []


# ---- output toggle ----


async def test_toggle_output_calls_off_when_on():
    client = FakeClient(output_on=True)
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "OUTPUT: ON")
        await pilot.press("o")
        await pilot.pause(0.3)
        assert client.output_off_calls == 1
        assert client.output_on_calls == 0


async def test_toggle_output_calls_on_when_off():
    client = FakeClient(output_on=False)
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "OUTPUT: OFF")
        await pilot.press("o")
        await pilot.pause(0.3)
        assert client.output_on_calls == 1
        assert client.output_off_calls == 0


# ---- robustness ----


async def test_controls_noop_when_disconnected():
    app = make_app()
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[DISCONNECTED]")
        await pilot.press("o")
        await pilot.press("v")
        await pilot.press("c")
        await pilot.pause(0.2)
        # entry never shown, app still alive and disconnected
        assert not app.query_one("#entry", Input).has_class("visible")
        assert "[DISCONNECTED]" in header_text(app)


async def test_control_serial_error_does_not_kill_app():
    """Regression: control workers use exit_on_error=False, so a SerialException
    inside output_on/output_off/set_voltage must not crash the app."""
    client = FakeClient(output_on=True)
    client.fail_controls = True
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "OUTPUT: ON")
        await pilot.press("o")
        await pilot.pause(0.3)  # let the failing control worker finish
        assert client.output_off_calls == 1
        assert app.is_running
        # the app still polls and updates the UI afterwards
        await wait_for_header(pilot, app, "[CONNECTED]")

        # same for a setter dispatched from the entry prompt
        await pilot.press("v")
        await pilot.press("5", "enter")
        await pilot.pause(0.3)
        assert client.set_voltage_calls == [5.0]
        assert app.is_running


async def test_psu_error_flips_to_disconnected():
    client = FakeClient()
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        client.fail = True
        text = await wait_for_header(pilot, app, "[DISCONNECTED]")
        assert "KIPRIM DC310S" in text  # identity retained in disconnected header
        assert app._client is None
        assert client.closed


async def test_close_oserror_during_disconnect_is_swallowed():
    client = FakeClient()
    client.close_raises = True
    app = make_app(client)
    async with app.run_test() as pilot:
        await wait_for_header(pilot, app, "[CONNECTED]")
        client.fail = True
        await wait_for_header(pilot, app, "[DISCONNECTED]")
        assert client.closed
        assert app.is_running
