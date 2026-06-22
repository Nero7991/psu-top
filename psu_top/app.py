"""Textual UI for psu-top."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import ClassVar

import serial
from rich.console import Console, ConsoleOptions, RenderResult
from rich.segment import Segment
from rich.style import Style
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Footer, Input, Static
from textual.worker import get_current_worker

from .scpi import PSUClient, PSUError

WINDOW_SECONDS = 300.0  # width of the rolling time window shown in each graph
CC_MARGIN = 0.05        # amps below the limit at which we call the mode CC
RECONNECT_DELAY = 2.0   # seconds between reopen attempts after a serial error


@dataclass
class Sample:
    volts: float
    amps: float
    set_volts: float
    set_amps: float
    output_on: bool


class _Bars:
    """Rich renderable for a rolling column graph.

    ``columns`` is one value (or None for "no data yet") per character cell, one
    raw sample each. Bar height is the value scaled against ``low``/``high`` --
    the min and max of the samples currently on screen -- so the scale zooms to
    the visible data and low currents stay visible.
    """

    BARS = "▁▂▃▄▅▆▇█"  # eight eighths, index 0 = shortest

    def __init__(
        self, columns: list[float | None], height: int, low: float, high: float, color
    ) -> None:
        self._columns = columns
        self._height = height
        self._low = low
        self._extent = (high - low) or 0.0
        self._style = Style.from_color(color)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        cols = self._columns
        height = self._height
        per_row = len(self.BARS)               # eighths per text row
        span = per_row * height - 1            # total addressable eighths
        for row in reversed(range(height)):    # top row first
            floor = row * per_row
            for value in cols:
                if value is None:
                    yield Segment(" ")
                    continue
                # A flat (zero-extent) window sits mid-height rather than on the
                # floor, so a steady reading reads as a level line, not "empty".
                ratio = 0.5 if not self._extent else (value - self._low) / self._extent
                ratio = 0.0 if ratio < 0.0 else 1.0 if ratio > 1.0 else ratio
                index = int(ratio * span)
                if index < floor:
                    yield Segment(" ")
                elif index >= floor + per_row:
                    yield Segment("█", self._style)
                else:
                    yield Segment(self.BARS[index - floor], self._style)
            if row > 0:
                yield Segment.line()


class History(Widget):
    """A rolling, oscilloscope-style bar graph: one sample per column.

    The newest sample sits at the right edge and the trace scrolls one column
    left per poll. Heights autoscale to the min/max of the samples on screen, so
    low-magnitude signals stay visible. ``clear()`` empties the trace, which also
    forces a fresh autoscale as new samples arrive.
    """

    COMPONENT_CLASSES: ClassVar[set[str]] = {"history--bar"}
    DEFAULT_CSS = """
    History > .history--bar { color: $primary; }
    """

    def __init__(self, capacity: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._capacity = max(1, capacity)
        self._samples: deque[float] = deque(maxlen=self._capacity)

    def add(self, value: float) -> None:
        self._samples.append(float(value))
        self.refresh()

    def clear(self) -> None:
        self._samples.clear()
        self.refresh()

    def _columns(self, width: int) -> list[float | None]:
        """One raw sample per column, newest at the right.

        The graph shows the most recent ``width`` samples, so each poll advances
        the trace exactly one column (rolling rate == update rate) and a sample's
        height never changes once drawn -- it simply slides left. Older samples
        beyond the terminal width stay in the buffer but off-screen.
        """
        data = list(self._samples)
        count = len(data)
        if count >= width:
            return list(data[-width:])           # newest width samples, right-aligned
        return [None] * (width - count) + list(data)  # part-full: pad the left

    def render(self) -> RenderResult:
        width, height = self.size.width, self.size.height
        if width <= 0 or height <= 0:
            return ""
        columns = self._columns(width)
        visible = [c for c in columns if c is not None]
        low, high = (min(visible), max(visible)) if visible else (0.0, 1.0)
        color = self.get_component_styles("history--bar").color
        return _Bars(columns, height, low, high, color.rich_color)


class MeterPanel(Vertical):
    """One bordered panel: live value + setpoint + rolling history graph."""

    def __init__(self, title: str, unit: str, capacity: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = title
        self._unit = unit
        self._graph = History(capacity)

    def compose(self) -> ComposeResult:
        yield Static("--", classes="meter-value")
        yield self._graph

    def update_value(self, measured: float, setpoint: float) -> None:
        self._graph.add(measured)
        self.query_one(".meter-value", Static).update(
            f"{measured:8.3f} {self._unit}   (set {setpoint:.3f})"
        )

    def clear_graph(self) -> None:
        self._graph.clear()


class PSUTopApp(App):
    """htop-style monitor/controller for the Kiprim DC310S."""

    CSS = """
    #header { height: 1; background: $boost; }
    #power { height: 1; }
    MeterPanel { border: round $primary; height: 9; }
    .meter-value { height: 1; }
    History { height: 4; margin: 1 0 0 0; }
    #entry { display: none; }
    #entry.visible { display: block; }
    """

    # Without this, Textual auto-focuses the hidden #entry Input on mount and
    # it swallows the key bindings.
    AUTO_FOCUS = None

    BINDINGS = [
        ("o", "toggle_output", "Output on/off"),
        ("v", "set_voltage", "Set voltage"),
        ("c", "set_current", "Set current"),
        ("r", "clear_graphs", "Clear graphs"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, port: str, baud: int, interval: float) -> None:
        super().__init__()
        self._port = port
        self._baud = baud
        self._interval = interval
        # Samples that fill the rolling window at the current poll rate.
        self._capacity = max(1, round(WINDOW_SECONDS / interval))
        self._client: PSUClient | None = None
        self._identity = ""
        self._last: Sample | None = None
        self._entry_target: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(" connecting...", id="header")
        # Stacked, not side by side, so each graph spans the full terminal width.
        yield MeterPanel("Voltage", "V", self._capacity, id="voltage")
        yield MeterPanel("Current", "A", self._capacity, id="current")
        yield Static(id="power")
        yield Input(id="entry")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._poll_loop, thread=True, exclusive=True)

    # ---- poll worker (runs in its own thread) ----

    def _poll_loop(self) -> None:
        worker = get_current_worker()
        while not worker.is_cancelled:
            try:
                if self._client is None:
                    self._connect()
                client = self._client
                sample = Sample(
                    volts=client.measured_voltage(),
                    amps=client.measured_current(),
                    set_volts=client.get_voltage_setpoint(),
                    set_amps=client.get_current_setpoint(),
                    output_on=client.get_output(),
                )
            except (PSUError, serial.SerialException, OSError):
                dead, self._client = self._client, None
                if dead is not None:
                    try:
                        dead.close()
                    except OSError:
                        pass
                if worker.is_cancelled:
                    return
                self.call_from_thread(self._show_disconnected)
                self._sleep(worker, RECONNECT_DELAY)
                continue
            if worker.is_cancelled:
                return
            self.call_from_thread(self._show_sample, sample)
            self._sleep(worker, self._interval)

    def _connect(self) -> None:
        ser = serial.Serial(self._port, self._baud, timeout=1)
        try:
            client = PSUClient(ser)
            idn = client.identify()              # e.g. KIPRIM,DC310S,25012662,FV:V5.2.0
        except Exception:
            ser.close()                          # don't leak the port if identify fails
            raise
        self._identity = " ".join(idn.split(",")[:2])
        self._client = client

    @staticmethod
    def _sleep(worker, seconds: float) -> None:
        """Sleep in small steps so quit is not delayed by a long wait."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not worker.is_cancelled:
            time.sleep(0.1)

    # ---- UI updates (run on the app thread via call_from_thread) ----

    def _show_sample(self, sample: Sample) -> None:
        self._last = sample
        mode = ""
        if sample.output_on:
            mode = " (CC)" if sample.amps >= sample.set_amps - CC_MARGIN else " (CV)"
        state = ("ON" if sample.output_on else "OFF") + mode
        self.query_one("#header", Static).update(
            f" PSU  {self._identity}  {self._port}  [CONNECTED]   OUTPUT: {state}"
        )
        self.query_one("#voltage", MeterPanel).update_value(sample.volts, sample.set_volts)
        self.query_one("#current", MeterPanel).update_value(sample.amps, sample.set_amps)
        self.query_one("#power", Static).update(
            f" Power: {sample.volts * sample.amps:6.2f} W"
        )

    def _show_disconnected(self) -> None:
        self._last = None
        self.query_one("#header", Static).update(
            f" PSU  {self._identity or 'unknown'}  {self._port}  [DISCONNECTED] retrying every {RECONNECT_DELAY:.0f}s"
        )

    # ---- controls ----

    def _run_control(self, fn) -> None:
        """Run a control command off the UI thread, surfacing any error.

        Control writes can race a serial disconnect; on failure we notify
        rather than let the worker die silently (the next poll would also
        report it, but only after a visible lag).
        """
        def task() -> None:
            try:
                fn()
            except (PSUError, serial.SerialException, OSError) as exc:
                self.call_from_thread(self.notify, f"Command failed: {exc}", severity="error")

        self.run_worker(task, thread=True, exit_on_error=False)

    def action_toggle_output(self) -> None:
        client, last = self._client, self._last
        if client is None or last is None:
            return
        target = client.output_off if last.output_on else client.output_on
        self._run_control(target)

    def action_set_voltage(self) -> None:
        self._prompt("voltage", "V")

    def action_set_current(self) -> None:
        self._prompt("current", "A")

    def action_clear_graphs(self) -> None:
        for panel in self.query(MeterPanel):
            panel.clear_graph()

    def _prompt(self, target: str, unit: str) -> None:
        if self._client is None:
            return
        self._entry_target = target
        entry = self.query_one("#entry", Input)
        entry.placeholder = f"new {target} ({unit}) -- Enter to apply, Esc to cancel"
        entry.value = ""
        entry.add_class("visible")
        entry.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        client, target = self._client, self._entry_target
        self._dismiss_entry()
        if client is None or target is None:
            return
        try:
            value = float(event.value)
        except ValueError:
            return
        setter = client.set_voltage if target == "voltage" else client.set_current
        self._run_control(lambda: setter(value))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self._dismiss_entry()

    def _dismiss_entry(self) -> None:
        self._entry_target = None
        entry = self.query_one("#entry", Input)
        entry.remove_class("visible")
        self.set_focus(None)
